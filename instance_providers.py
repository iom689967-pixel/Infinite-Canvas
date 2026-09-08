"""Instance-only provider administration. Never reads env/owner provider configuration.

The existing private model-access.json remains the single source of truth. Its
administrator entries and resource ceilings are not writable by this API.
"""
import hashlib
import json
import os
import re
import tempfile
import uuid
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import HTTPException

from instance_model_policy import GuardedClient, ModelPolicy, failure, origin

ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}')
FIELDS = {'id', 'name', 'protocol', 'base_url', 'enabled', 'models'}


def model_limits(policy, protocol):
    common = dict(max_references=8, max_reference_bytes=1048576, timeout_seconds=120)
    defaults = (dict(max_output_tokens=4096, max_text_chars=100000) if protocol != 'kie' else
                dict(max_images=1, sizes=['1024x1024'], resolutions=['1K'], aspect_ratios=['1:1'], output_formats=['']))
    limits = common | defaults
    # Existing administrator limits remain ceilings, not browser-editable defaults.
    for p in policy.providers.values():
        if p.get('personal') or (p['protocol'] == 'kie') != (protocol == 'kie'):
            continue
        for configured in p['models'].values():
            for key, value in limits.items():
                if key in configured:
                    limits[key] = min(value, configured[key]) if isinstance(value, int) else [v for v in value if v in configured[key]]
    return limits


def validate_item(policy, item):
    if set(item) != FIELDS or not ID.fullmatch(item.get('id', '')):
        raise ValueError()
    if not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 100:
        raise ValueError()
    if item['protocol'] not in {'openai', 'gemini', 'kie'} or type(item['enabled']) is not bool:
        raise ValueError()
    url = item['base_url']
    if not isinstance(url, str) or len(url) > 2048 or '\\' in url or any(ord(c) < 33 for c in url):
        raise ValueError()
    parsed = urlsplit(url)
    if any(v in {'.', '..'} for v in unquote(parsed.path).split('/')):
        raise ValueError()
    policy.validate_config_url(url)
    if policy.mode == 'live':
        import ipaddress
        host = parsed.hostname
        if host.lower().rstrip('.') in {'localhost', 'localhost.localdomain'}:
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
            if (not ip.is_global or ip.is_multicast or ip.is_reserved) and host not in policy.private_targets.get(origin(url), frozenset()):
                raise ValueError('Private address')
        except ValueError as exc:
            if str(exc) == 'Private address':
                raise
    if not isinstance(item['models'], list) or len(item['models']) > 200:
        raise ValueError()
    seen = set()
    for m in item['models']:
        if not isinstance(m, dict) or set(m) != {'id', 'purpose'} or not MODEL.fullmatch(m.get('id', '')):
            raise ValueError()
        if '..' in m['id'] or m['id'] in seen or m['purpose'] not in {'llm', 'image', 'video'}:
            raise ValueError()
        seen.add(m['id'])


def compile_personal(policy, stored):
    if set(stored) != FIELDS | {'credential_file', 'revision'}:
        raise ValueError('Invalid personal provider storage')
    validate_item(policy, {k: stored[k] for k in FIELDS})
    credential = stored['credential_file']
    if credential and not re.fullmatch(r'credentials/personal-[0-9a-f]{32}\.key', credential):
        raise ValueError('Invalid personal credential reference')
    if credential:
        policy.private_file(policy.paths.data_root / '.auth' / credential)
    limits = model_limits(policy, stored['protocol'])
    supported = {}
    from providers.kie.models import KIE_UI_MODELS
    for m in stored['models']:
        compatible = (stored['protocol'] in {'openai', 'gemini'} and m['purpose'] == 'llm') or (
            stored['protocol'] == 'kie' and m['purpose'] == 'image' and m['id'] in KIE_UI_MODELS)
        if compatible and credential:
            supported[m['id']] = dict(limits)
    result = dict(stored, personal=True, models=supported)
    if stored['protocol'] == 'kie':
        # Mock targets are administrator-provided, never a production localhost escape.
        result['upload_base_url'] = stored['base_url'] if policy.mode == 'mock' else 'https://kieai.redpandaai.co'
        result['media_origins'] = []  # GuardedClient validates every public media DNS/connection.
    return result


def atomic_private(path, value):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Linked private storage')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = handle.name
        os.chmod(temporary, 0o600)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OwnProviders:
    def __init__(self, models):
        self.models = models
        self.discovering = False

    def config(self):
        policy = self.models.policy
        if policy.file.exists():
            policy.private_file(policy.file)
            return json.loads(policy.file.read_text())
        return dict(schema_version=1, mode='live', max_concurrent=1, providers=[], personal_providers=[])

    def public(self):
        policy = self.models.policy
        result = []
        for stored in self.config().get('personal_providers', []):
            compiled = policy.providers[stored['id']]
            item = {k: stored[k] for k in FIELDS}
            item['has_key'] = bool(stored['credential_file'])
            item['models'] = [dict(m, supported=m['id'] in compiled['models'],
                                   support_note='' if m['id'] in compiled['models'] else '尚未适配此用途，或未配置 Key；不可运行') for m in stored['models']]
            result.append(item)
        return {'providers': result, 'shared_provider_ids': [p['id'] for p in policy.providers.values() if not p.get('personal')],
                'max_concurrent': policy.max_concurrent}

    def save(self, body, delete=False):
        # Synchronous commit on the ASGI event loop: no create/discover can interleave.
        if self.discovering or self.models.llm_active or self.models.runners or self.models.outstanding():
            raise HTTPException(409, '仍有运行中或提交状态未确认的任务；配置未修改，请先处理原任务')
        config = self.config()
        policy = self.models.policy
        entries = {p['id']: p for p in config.get('personal_providers', [])}
        if not isinstance(body, dict) or not isinstance(body.get('id'), str):
            raise ValueError()
        provider_id = body['id']
        if provider_id in {p['id'] for p in config['providers']}:
            raise HTTPException(403, '管理员提供的 Provider 不属于个人可编辑配置')
        old = entries.get(provider_id)
        new_key_path = None
        if delete:
            if set(body) != {'id'} or not old:
                raise HTTPException(404, '本实例没有此个人 Provider')
            del entries[provider_id]
        else:
            if set(body) - FIELDS - {'api_key', 'clear_key'} or not FIELDS <= set(body):
                raise ValueError()
            item = {k: body[k] for k in FIELDS}
            validate_item(policy, item)
            key = body.get('api_key')
            clear = body.get('clear_key', False)
            if type(clear) is not bool or (key is not None and (not isinstance(key, str) or not key or len(key) > 4096 or any(c in key for c in '\r\n'))):
                raise ValueError()
            if clear and key is not None:
                raise ValueError()
            # A changed base path can also select a different tenant/authentication target.
            if old and (old['base_url'].rstrip('/') != item['base_url'].rstrip('/') or old['protocol'] != item['protocol']) and old['credential_file'] and key is None and not clear:
                raise HTTPException(409, '地址或协议已更改，请提供匹配的新 Key，或明确清除旧 Key')
            credential = old['credential_file'] if old and not clear else ''
            if key is not None:
                directory = policy.paths.data_root / '.auth/credentials'
                if directory.is_symlink():
                    raise ValueError()
                directory.mkdir(mode=0o700, exist_ok=True)
                credential = 'credentials/personal-' + uuid.uuid4().hex + '.key'
                new_key_path = policy.paths.data_root / '.auth' / credential
                atomic_private(new_key_path, key)
            entries[provider_id] = dict(item, credential_file=credential, revision=uuid.uuid4().hex)
            compile_personal(policy, entries[provider_id])
        config['personal_providers'] = list(entries.values())
        try:
            atomic_private(policy.file, json.dumps(config, ensure_ascii=False))
        except Exception:
            if new_key_path:
                new_key_path.unlink(missing_ok=True)
            raise
        self.models.policy = ModelPolicy(policy.paths)
        if old and old['credential_file'] and old['credential_file'] != entries.get(provider_id, {}).get('credential_file'):
            (policy.paths.data_root / '.auth' / old['credential_file']).unlink(missing_ok=True)
        return self.public()

    async def discover(self, body):
        if not isinstance(body, dict) or set(body) != {'id'}:
            raise ValueError()
        policy = self.models.policy
        provider = policy.providers.get(body['id'])
        if not provider or not provider.get('personal') or not provider.get('enabled'):
            raise HTTPException(404, '本实例没有已启用的个人 Provider')
        if self.discovering:
            raise HTTPException(429, '模型发现正在进行')
        if provider['protocol'] == 'kie':
            from providers.kie.models import KIE_UI_MODELS
            return {'models': [{'id': m, 'purpose': 'image'} for m in KIE_UI_MODELS], 'source': '已适配的 Kie 模型目录；未执行生成或连接验证'}
        self.models.check_capacity()
        self.discovering = True
        self.models.llm_active += 1
        client = GuardedClient(policy, provider, timeout=15)
        try:
            base = provider['base_url'].rstrip('/')
            gemini = provider['protocol'] == 'gemini'
            if gemini:
                base = re.sub(r'/v1(?:beta)?$', '', base) + '/v1beta'
            key = policy.credential(provider)
            headers = {'x-goog-api-key': key} if gemini else {'Authorization': 'Bearer '+key}
            import asyncio
            async with asyncio.timeout(15):
                response = await client.get(base + '/models', headers=headers)
            response.raise_for_status()
            data = response.json()
            models = []
            for m in data.get('models' if gemini else 'data', [])[:200]:
                name = str(m.get('name' if gemini else 'id', '')).removeprefix('models/')
                if MODEL.fullmatch(name) and '..' not in name and key not in name:
                    models.append({'id': name, 'purpose': 'llm'})
            return {'models': models, 'source': '上游模型列表；未执行生成'}
        except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
            raise failure('upstream', 502) from None
        finally:
            self.discovering = False
            self.models.llm_active -= 1
            await client.aclose()


def task_provider_revision(policy, provider):
    # Internal digest only; never exported. Also detects administrator key rotation.
    return hashlib.sha256((json.dumps(provider, sort_keys=True) + policy.credential(provider)).encode()).hexdigest()
