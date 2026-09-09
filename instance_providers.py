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

from fastapi import HTTPException
from provider_schema import NETWORK_PROTOCOLS

from instance_model_policy import origin

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
    if item['protocol'] not in NETWORK_PROTOCOLS or type(item['enabled']) is not bool:
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
    if not FIELDS | {'credential_file', 'revision'} <= set(stored) or set(stored) - FIELDS - {'credential_file', 'revision', 'settings', 'secret_refs'}:
        raise ValueError('Invalid personal provider storage')
    validate_item(policy, {k: stored[k] for k in FIELDS})
    for ref in stored.get('secret_refs', {}).values():
        if not re.fullmatch(r'credentials/personal-[0-9a-f]{32}\.key', ref):
            raise ValueError('Invalid credential reference')
        policy.private_file(policy.paths.data_root / '.auth' / ref)
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
        from instance_provider_settings import FullProviderSettings
        if not isinstance(body, dict) or not isinstance(body.get('id'), str):
            raise ValueError()
        settings = FullProviderSettings(self)
        items = {p['id']: settings.settings(p) for p in self.config().get('personal_providers', [])}
        pid = body['id']
        if pid in {p['id'] for p in self.config()['providers']}:
            raise HTTPException(403, '管理员 Provider 不属于个人配置')
        if delete:
            if set(body) != {'id'} or pid not in items:
                raise HTTPException(404, '本实例没有此个人 Provider')
            del items[pid]
        else:
            if set(body) - FIELDS - {'api_key', 'clear_key'} or not FIELDS <= set(body):
                raise ValueError()
            validate_item(self.models.policy, {k: body[k] for k in FIELDS})
            if type(body.get('clear_key', False)) is not bool:
                raise ValueError()
            item = items.get(pid, {}) | {k:v for k,v in body.items() if k != 'models'}
            for category, purpose in [('image','image'), ('chat','llm'), ('video','video')]:
                item[category+'_models'] = [m['id'] for m in body['models'] if m['purpose'] == purpose]
            items[pid] = item
        settings.save(list(items.values()))
        return self.public()

    async def discover(self, body):
        from instance_provider_settings import FullProviderSettings
        if not isinstance(body, dict) or set(body) != {'id'}:
            raise ValueError()
        provider = self.models.policy.providers.get(body['id'])
        if not provider or not provider.get('personal') or not provider.get('enabled'):
            raise HTTPException(404, '本实例没有已启用的个人 Provider')
        result = await FullProviderSettings(self).probe({
            'provider_id': provider['id'], 'base_url': provider['base_url'],
            'protocol': provider['protocol'],
        }, 'fetch-models')
        return {'models': [dict(id=m, purpose=purpose)
            for category,purpose in [('image','image'),('chat','llm'),('video','video')]
            for m in result.get(category+'_models', [])], 'source': result.get('message','只读模型目录')}


def task_provider_revision(policy, provider):
    # Internal digest only; never exported. Also detects administrator key rotation.
    return hashlib.sha256((json.dumps(provider, sort_keys=True) + policy.credential(provider)).encode()).hexdigest()
