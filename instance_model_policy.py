"""Administrator-owned model capabilities and per-request network boundary.

Only this process and trusted local administrators can read .auth credentials.
This is application isolation, not an OS sandbox against the same OS account.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import ipaddress
import json
from pathlib import Path
import re
import socket
import copy
import time
from urllib.parse import urlsplit, unquote, quote, quote_plus, parse_qs

import httpx
from fastapi import HTTPException

OUTBOUND_ENDPOINTS = ContextVar('instance_model_outbound', default=frozenset())


def secret_variants(secret):
    """Cover raw, URL and JSON literal representations of private values."""
    if not secret:return set()
    return {secret,quote(secret,safe=''),quote_plus(secret),
            json.dumps(secret,ensure_ascii=False)[1:-1],
            json.dumps(secret,ensure_ascii=True)[1:-1]}


def failure(code, status=403):
    messages = {
        'not_configured': '请在个人 API 设置中添加 Provider、凭证和模型用途',
        'not_allowed': 'Provider 已停用、模型用途未配置或缺少适配契约/凭证',
        'limits': '请求参数超出管理员允许范围',
        'reference': '参考图必须是本实例已上传的图片',
        'busy': '实例并发已满，或有待确认的上游任务，请先查询原任务',
        'network': '上游网络异常；未自动重复提交',
        'timeout': '上游等待超时；未自动重复提交',
        'upstream': '上游请求失败，内部响应已隐藏',
        'download': '上游媒体地址或内容不符合安全规则',
        'task_incomplete': '任务未完成；请查询或恢复原任务，不会自动重新生成',
        'conflict': '请求标识已使用，不能替换其内容',
        'storage_full': '当前工作区存储空间已满。',
        'server_storage_full': '服务器存储资源不足，暂时停止新的内容写入。',
    }
    return HTTPException(status_code=status, detail={'code': code, 'message': messages[code]})


def origin(value):
    parsed = urlsplit(str(value))
    if parsed.username or parsed.password or not parsed.hostname or parsed.fragment:
        raise ValueError('Invalid model origin')
    return (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))


class ModelPolicy:
    def __init__(self, paths):
        self.paths = paths
        self.file = paths.data_root / '.auth/model-access.json'
        self.providers = {}
        self.mode = 'live'
        self.private_targets = {}
        self.max_concurrent = 1
        if not self.file.exists():
            return
        self.private_file(self.file)
        config = json.loads(self.file.read_text())
        if set(config) - {'schema_version', 'mode', 'max_concurrent', 'providers', 'personal_providers', 'private_targets'} or config['schema_version'] != 1:
            raise RuntimeError('Invalid model access configuration')
        self.mode = config['mode']
        if self.mode not in {'mock', 'live'}:
            raise RuntimeError('Model mode must be explicitly mock or live')
        self.max_concurrent = self.integer(config['max_concurrent'], 1, 8)
        # Server-admin-only exact HTTPS LAN destinations. Never permit loopback,
        # link-local metadata, CIDRs, or a browser-controlled credential target list.
        for target in config.get('private_targets', []):
            if set(target) != {'origin', 'ips'}:
                raise RuntimeError('Invalid private target')
            endpoint = origin(target['origin'])
            if endpoint[0] != 'https' or endpoint[2] in {3000, paths.port} or urlsplit(target['origin']).path not in {'', '/'}:
                raise RuntimeError('Invalid private target origin')
            ips = frozenset(target['ips'])
            if not ips:
                raise RuntimeError('Empty private target addresses')
            for value in ips:
                ip = ipaddress.ip_address(value)
                if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                    raise RuntimeError('Unsafe private target address')
            self.private_targets[endpoint] = ips
        if not isinstance(config['providers'], list) or (not config['providers'] and 'personal_providers' not in config):
            raise RuntimeError('Model access requires an explicit nonempty provider allowlist')
        for item in config['providers']:
            required = {'id', 'protocol', 'base_url', 'credential_file', 'models'}
            if not required <= set(item) or set(item) - required - {'upload_base_url', 'media_origins'}:
                raise RuntimeError('Invalid provider configuration fields')
            provider_id = item['id']
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', provider_id) or provider_id in self.providers:
                raise RuntimeError('Invalid or duplicate provider ID')
            if item['protocol'] not in {'gemini', 'kie'}:
                raise RuntimeError('Only native Gemini and Kie are enabled')
            self.validate_config_url(item['base_url'])
            key_path = paths.data_root / '.auth' / item['credential_file']
            if not str(item['credential_file']).startswith('credentials/'):
                raise RuntimeError('Credentials must be in the private credential directory')
            self.private_file(key_path)
            if not key_path.read_text().strip():
                raise RuntimeError('Empty model credential')
            if item['protocol'] == 'kie':
                if not item.get('upload_base_url') or not item.get('media_origins'):
                    raise RuntimeError('Kie requires explicit upload and media origins')
                self.validate_config_url(item['upload_base_url'])
                for value in item['media_origins']:
                    self.validate_config_url(value, only_origin=True)
            for model_id, limits in item['models'].items():
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', model_id):
                    raise RuntimeError('Invalid model ID')
                common = {'max_references', 'max_reference_bytes', 'timeout_seconds'}
                fields = common | ({'max_output_tokens', 'max_text_chars'} if item['protocol'] == 'gemini' else
                                   {'max_images', 'sizes', 'resolutions', 'aspect_ratios', 'output_formats'})
                if set(limits) != fields:
                    raise RuntimeError('Every cost limit must be explicitly configured')
                self.integer(limits['max_references'], 0, 8)
                self.integer(limits['max_reference_bytes'], 1024, 30*1024*1024)
                self.integer(limits['timeout_seconds'], 1, 120 if item['protocol'] == 'gemini' else 1800)
                if item['protocol'] == 'gemini':
                    self.integer(limits['max_output_tokens'], 1, 8192)
                    self.integer(limits['max_text_chars'], 1, 100000)
                else:
                    from providers.kie.models import capability_for, build_create_payload
                    capability_for(model_id)
                    self.integer(limits['max_images'], 1, 4)
                    for key in ('sizes', 'resolutions', 'aspect_ratios', 'output_formats'):
                        if not isinstance(limits[key], list) or not limits[key] or not all(isinstance(v, str) for v in limits[key]):
                            raise RuntimeError('Invalid image cost allowlist')
                    for size in limits['sizes']:
                        if not re.fullmatch(r'[1-9][0-9]{1,3}x[1-9][0-9]{1,3}', size) or max(map(int, size.split('x'))) > 4096:
                            raise RuntimeError('Invalid image dimensions')
                    for resolution in limits['resolutions']:
                        for ratio in limits['aspect_ratios']:
                            for fmt in limits['output_formats']:
                                build_create_payload(model_id, 'validate', [], ratio, resolution, fmt)
            if not item['models']:
                raise RuntimeError('Empty model allowlist')
            self.providers[provider_id] = item
        from instance_providers import compile_personal
        for item in config.get('personal_providers', []):
            compiled = compile_personal(self, item)
            if compiled['id'] in self.providers:
                raise RuntimeError('Duplicate personal provider')
            self.providers[compiled['id']] = compiled
        if getattr(paths, 'public_beta', False):
            budget = json.loads((paths.data_root / '.auth/public-beta.json').read_text())
            self.max_concurrent = min(self.max_concurrent, budget['max_concurrent_generations'])

    @staticmethod
    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise RuntimeError('Invalid model limit')
        return value

    def private_file(self, path):
        path = Path(path)
        private = self.paths.data_root / '.auth'
        if '..' in path.parts or not path.resolve().is_relative_to(private.resolve()):
            raise RuntimeError('Credential path must stay in this instance')
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise RuntimeError('Private model file missing or linked')
        if path.stat().st_mode & 0o077:
            raise RuntimeError('Private model files must have mode 0600')

    def validate_config_url(self, url, only_origin=False):
        parsed = urlsplit(url)
        endpoint = origin(url)
        if parsed.query or (only_origin and parsed.path not in {'', '/'}):
            raise RuntimeError('Model configuration URLs cannot contain query credentials')
        if self.mode == 'mock':
            if endpoint[0] != 'http' or endpoint[1:] not in self.paths.upstreams:
                raise RuntimeError('Mock endpoint must be an approved loopback upstream')
        elif endpoint[0] != 'https' or (endpoint[2] != 443 and endpoint not in self.private_targets):
            raise RuntimeError('Live endpoints require HTTPS on port 443')

    def credential(self, provider, field='api_key'):
        ref = provider.get('secret_refs', {}).get(field, provider.get('credential_file', '') if field == 'api_key' else '')
        path = self.paths.data_root / '.auth' / ref
        try:
            self.private_file(path)
            value = path.read_text().strip()
        except (OSError, RuntimeError):
            raise failure('not_configured', 503) from None
        if not value or len(value) > 4096 or any(c in value for c in '\r\n'):
            raise failure('not_configured', 503)
        return value

    def allowed(self, provider_id, model, kind):
        if not self.providers:
            raise failure('not_configured', 503)
        provider = self.providers.get(provider_id)
        if not provider or not provider.get('enabled', True):
            raise failure('not_allowed')
        if provider.get('personal'):
            cap = provider['capabilities'].get(kind, {}).get(model)
            if not cap or not cap['executable']:
                raise HTTPException(403, {'code':'not_allowed', 'message':cap['reason'] if cap else '未配置此模型用途'})
            return provider, provider['model_uses'][kind][model]
        # Shared administrator providers retain their explicit cost/permission policy.
        if kind != ('llm' if provider['protocol'] == 'gemini' else 'image') or model not in provider['models']:
            raise failure('not_allowed')
        return provider, provider['models'][model]

    def catalog(self):
        result = []
        for p in self.providers.values():
            if p.get('personal'):
                settings = p['settings']
                public = {k:v for k,v in settings.items() if k not in {'api_key','wallet_api_key','volcengine_access_key_id','volcengine_secret_access_key'} and not k.startswith('clear_')}
                public.update(personal=True, capabilities=self.public_capabilities(p), model_limits=p['models'],
                              has_key=bool(p.get('credential_file')), configured=True)
                result.append(public)
                continue
            if not p.get('enabled', True) or not p['models']:
                continue
            result.append({'id': p['id'], 'name': p.get('name', p['id']), 'protocol': p['protocol'], 'enabled': True,
                           'chat_models': list(p['models']) if p['protocol'] in {'gemini', 'openai'} else [],
                           'image_models': list(p['models']) if p['protocol'] == 'kie' else [], 'video_models': [],
                           'model_limits': p['models']})
            # Only explicitly approved shared models enter these categories;
            # the shared parser supplies UI metadata without widening the ACL.
            from provider_capabilities import capabilities
            result[-1]['capabilities']=capabilities(result[-1],{'api_key'})
        return {'api_providers': result, 'chat_models': [m for p in result for m in p['chat_models']],
                'image_models': [m for p in result for m in p['image_models']],
                'video_models': [m for p in result for m in p['video_models']]}

    def verification_store(self):
        path=self.paths.data_root/'.auth/model-verifications.json'
        if path.is_symlink():raise failure('not_allowed')
        return path,json.loads(path.read_text()) if path.exists() else {}

    def mark_verified(self,provider,model,purpose):
        if not provider.get('personal'):return
        from instance_providers import task_provider_revision,atomic_private
        path,records=self.verification_store()
        records[provider['id']+'|'+purpose+'|'+model]=dict(revision=task_provider_revision(self,provider),mode=self.mode,at=time.time())
        atomic_private(path,json.dumps(records))

    def public_capabilities(self,provider):
        from instance_providers import task_provider_revision
        caps=copy.deepcopy(provider['capabilities'])
        _,records=self.verification_store()
        revision=None
        for purpose,models in caps.items():
            for model,cap in models.items():
                record=records.get(provider['id']+'|'+purpose+'|'+model,{})
                if record:
                    if revision is None:revision=task_provider_revision(self,provider)
                    if record.get('revision')==revision:
                        cap['verified']=record.get('mode')=='live'
                        cap['mock_verified']=record.get('mode')=='mock'
                        if cap['verified'] and cap['executable']:cap['state']='call_verified'
        return caps

    def redact(self, text, *, used_credential=''):
        text = str(text)
        if used_credential:
            for value in secret_variants(used_credential):text=text.replace(value,'[redacted]')
        for p in self.providers.values():
            for field in p.get('secret_refs', {}):
                try:
                    secret = self.credential(p,field)
                    for encoded in secret_variants(secret):text=text.replace(encoded,'[redacted]')
                except HTTPException:
                    pass
            try:
                key = self.credential(p)
            except HTTPException:
                key = ''
            for value in secret_variants(key) | {p['base_url'],p.get('upload_base_url',''),p['credential_file']}:
                if value:
                    text = text.replace(value, '[redacted]')
        return text


class GuardedClient:
    """No redirects/retries/proxy credentials; DNS answers must pass the socket audit hook."""
    quiet = True

    def __init__(self, policy, provider, *, timeout=120):
        self.policy, self.provider = policy, provider
        self.used_credential = ''
        self.kie_upload_base_url = provider.get('upload_base_url', '')
        self.client = httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)

    def validate_media_url(self, url):
        try:
            endpoint = origin(url)
            if endpoint[0] not in {'https', 'http'}:
                raise ValueError()
            if not self.provider.get('personal') and endpoint not in {origin(v) for v in self.provider.get('media_origins', [])}:
                raise ValueError()
            secrets = []
            for field in self.provider.get('secret_refs', {}) or {'api_key': self.provider.get('credential_file','')}:
                try: secrets.append(self.policy.credential(self.provider) if field=='api_key' else self.policy.credential(self.provider, field))
                except HTTPException: pass
            destination=unquote(str(url))
            if any(value in destination for key in secrets+[self.used_credential] for value in secret_variants(key)):
                raise ValueError()
        except (ValueError, TypeError):
            raise failure('download', 502)
        return True

    async def prepare(self, method, url, kwargs):
        execution = getattr(self, 'execution', None)
        if getattr(self, 'query_only', False) and method.upper() not in {'GET', 'HEAD'}:
            raise failure('not_allowed')
        diagnostic = getattr(self, 'reference_diagnostic', None)
        if diagnostic:
            diagnostic('http')
        endpoint = origin(url)
        headers = dict(getattr(self, 'default_headers', {})) | dict(kwargs.pop('headers', {}))
        kwargs.pop('follow_redirects', None)
        has_auth = any(k.lower() in {'authorization', 'x-goog-api-key','x-api-key'} for k in headers)
        if execution:
            body = kwargs.get('json') or kwargs.get('data') or {}
            has_auth = has_auth or (isinstance(body, dict) and any(v and body.get('apiKey') == v for v in execution.secrets.values()))
        if has_auth:
            allowed = {origin(self.provider['base_url'])}
            if self.kie_upload_base_url:
                allowed.add(origin(self.kie_upload_base_url))
            signed_target=False
            if execution and getattr(execution,'signed_asset',False):
                parsed=urlsplit(str(url));query=parse_qs(parsed.query)
                target=(endpoint==('https','open.volcengineapi.com',443) and parsed.path=='/') or (self.policy.mode=='mock' and endpoint==origin(self.provider['base_url']) and parsed.path==urlsplit(self.provider['base_url']).path.rstrip('/')+'/asset-api')
                signed_target=(target and set(query)=={'Action','Version'} and query['Action'][0] in {'ListAssetGroups','CreateAssetGroup','CreateAsset','GetAsset'} and query['Version']==['2024-01-01'])
                if signed_target:
                    authorization=next((v for k,v in headers.items() if k.lower()=='authorization'),'')
                    signed_target=authorization.startswith('HMAC-SHA256 Credential='+execution.key('volcengine_access_key_id')+'/')
                    if self.policy.mode!='mock' and any(execution.key(f) and execution.key(f) in str(headers) for f in ('api_key','wallet_api_key','volcengine_secret_access_key')):signed_target=False
                if not signed_target:raise failure('download',502)
            if endpoint not in allowed and not signed_target:
                raise failure('download', 502)
        else:
            self.validate_media_url(url)
            if method.upper() not in {'GET', 'HEAD'} and not (execution and str(url) in getattr(execution,'anonymous_uploads',())):
                raise failure('download', 502)
        if self.policy.mode == 'mock':
            if endpoint[0] != 'http' or endpoint[1:] not in self.policy.paths.upstreams:
                raise failure('download', 502)
            addresses = {endpoint[1:]}
        else:
            if diagnostic:
                diagnostic('dns')
            private = getattr(self.policy, 'private_targets', {}).get(endpoint, frozenset())
            if endpoint[0] != 'https' or (endpoint[2] != 443 and not private):
                raise failure('download', 502)
            resolved = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, endpoint[1], endpoint[2], type=socket.SOCK_STREAM), 10)
            addresses = {(result[4][0], endpoint[2]) for result in resolved}
            def approved(value):
                ip = ipaddress.ip_address(value)
                return (ip.is_global and not (ip.is_multicast or ip.is_reserved)) or value in private
            if not addresses or any(not approved(ip) for ip, _ in addresses):
                if diagnostic:
                    diagnostic('dns', destination='rejected')
                raise failure('download', 502)
            if diagnostic:
                diagnostic('dns', destination='approved_private' if private else 'public')
        return execution,diagnostic,headers,addresses

    async def request(self, method, url, **kwargs):
        execution,diagnostic,headers,addresses=await self.prepare(method,url,kwargs)
        context = OUTBOUND_ENDPOINTS.set(frozenset(addresses))
        try:
            if execution:
                await execution.before(method, url, kwargs)
            if 'json' in kwargs and len(json.dumps(kwargs['json'],ensure_ascii=False).encode()) > 32*1024*1024:
                raise failure('limits',400)
            if diagnostic:
                diagnostic('http')
            # Do not carry an upstream Set-Cookie into uploads or media downloads either.
            self.client.cookies.clear()
            # Stream and cap every response before buffering; even an approved CDN is untrusted.
            async with self.client.stream(method, url, headers=headers, **kwargs) as response:
                if diagnostic:
                    diagnostic('response', http_status=response.status_code,
                               media_type=response.headers.get('content-type', '').split(';')[0],
                               http_version=response.http_version, streaming=True, headers_received=True,
                               content_length_present='content-length' in response.headers,
                               content_length=(int(response.headers['content-length'])
                                   if response.headers.get('content-length', '').isascii()
                                   and response.headers.get('content-length', '').isdigit()
                                   and len(response.headers['content-length']) <= 9 else None),
                               content_encoding=response.headers.get('content-encoding', 'none').lower(),
                               transfer_encoding=response.headers.get('transfer-encoding', 'none').lower())
                if 300 <= response.status_code < 400:
                    if diagnostic:
                        diagnostic('redirect', redirect_count=1)
                    raise failure('download', 502)
                data = bytearray()
                event_buffer=b''
                body_bytes_read = 0
                if diagnostic:
                    diagnostic('body_read', body_read_started=True, body_bytes_read=0)
                try:
                    async for chunk in response.aiter_bytes():
                        body_bytes_read += len(chunk)
                        if len(data) + len(chunk) > 32*1024*1024:
                            raise failure('download', 502)
                        data.extend(chunk)
                        if execution and execution.observer and 'text/event-stream' in response.headers.get('content-type',''):
                            event_buffer+=chunk
                            while b'\n' in event_buffer:
                                line,event_buffer=event_buffer.split(b'\n',1)
                                if not line.startswith(b'data:'):continue
                                try:event=json.loads(line[5:].strip())
                                except (ValueError,UnicodeError):continue
                                if isinstance(event,dict) and event.get('type') in {'response.created','response.completed','response.failed'} and isinstance(event.get('response'),dict):
                                    synthetic=httpx.Response(response.status_code,json=event['response'],request=response.request)
                                    await execution.after(method,url,kwargs,synthetic)
                finally:
                    if diagnostic:
                        diagnostic('body_read', body_read_started=True, body_bytes_read=body_bytes_read)
                if diagnostic:
                    diagnostic('response_rebuild', body_bytes_read=len(data))
                # aiter_bytes has already decoded Content-Encoding and HTTP framing.
                # Reusing wire headers would decode the buffered content a second time.
                buffered_headers = response.headers.copy()
                for name in ('content-encoding', 'content-length', 'transfer-encoding'):
                    buffered_headers.pop(name, None)
                buffered = httpx.Response(response.status_code, headers=buffered_headers, content=bytes(data),
                                      request=response.request,
                                      extensions={'http_version': response.extensions.get('http_version', b'HTTP/1.1')})
                if execution:
                    await execution.after(method, url, kwargs, buffered)
                return buffered
        except Exception as exc:
            if diagnostic and hasattr(diagnostic, 'failed'):
                diagnostic.failed(exc)
            raise
        finally:
            OUTBOUND_ENDPOINTS.reset(context)

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request('POST', url, **kwargs)

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        if not kwargs.pop('guarded_live',False):
            yield await self.request(method, url, **kwargs)
            return
        execution,diagnostic,headers,addresses=await self.prepare(method,url,kwargs)
        context=OUTBOUND_ENDPOINTS.set(frozenset(addresses))
        try:
            if execution:await execution.before(method,url,kwargs)
            self.client.cookies.clear()
            async with self.client.stream(method,url,headers=headers,**kwargs) as original:
                if 300 <= original.status_code < 400:raise failure('download',502)
                class BoundedDecoded(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        size=0
                        async for chunk in original.aiter_bytes():
                            size+=len(chunk)
                            if size>32*1024*1024:raise failure('download',502)
                            yield chunk
                decoded_headers=original.headers.copy()
                for name in ('content-encoding','content-length','transfer-encoding'):decoded_headers.pop(name,None)
                yield httpx.Response(original.status_code,headers=decoded_headers,stream=BoundedDecoded(),request=original.request)
        finally:
            OUTBOUND_ENDPOINTS.reset(context)

    async def aclose(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
