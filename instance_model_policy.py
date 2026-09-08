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
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

OUTBOUND_ENDPOINTS = ContextVar('instance_model_outbound', default=frozenset())


def failure(code, status=403):
    messages = {
        'not_configured': '模型调用尚未配置，请联系本机管理员',
        'not_allowed': 'Provider、模型或请求类型未获批准',
        'limits': '请求参数超出管理员允许范围',
        'reference': '参考图必须是本实例已上传的图片',
        'busy': '实例并发已满，或有待确认的上游任务，请先查询原任务',
        'network': '上游网络异常；未自动重复提交',
        'timeout': '上游等待超时；未自动重复提交',
        'upstream': '上游请求失败，内部响应已隐藏',
        'download': '上游媒体地址或内容不符合安全规则',
        'conflict': '请求标识已使用，不能替换其内容',
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
        self.max_concurrent = 1
        if not self.file.exists():
            return
        self.private_file(self.file)
        config = json.loads(self.file.read_text())
        if set(config) != {'schema_version', 'mode', 'max_concurrent', 'providers'} or config['schema_version'] != 1:
            raise RuntimeError('Invalid model access configuration')
        self.mode = config['mode']
        if self.mode not in {'mock', 'live'}:
            raise RuntimeError('Model mode must be explicitly mock or live')
        self.max_concurrent = self.integer(config['max_concurrent'], 1, 8)
        if not isinstance(config['providers'], list) or not config['providers']:
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
        elif endpoint[0] != 'https' or endpoint[2] != 443:
            raise RuntimeError('Live endpoints require HTTPS on port 443')

    def credential(self, provider):
        path = self.paths.data_root / '.auth' / provider['credential_file']
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
        protocol = 'gemini' if kind == 'llm' else 'kie'
        if not provider or provider['protocol'] != protocol or model not in provider['models']:
            raise failure('not_allowed')
        return provider, provider['models'][model]

    def catalog(self):
        result = []
        for p in self.providers.values():
            result.append({'id': p['id'], 'name': p['id'], 'protocol': p['protocol'], 'enabled': True,
                           'chat_models': list(p['models']) if p['protocol'] == 'gemini' else [],
                           'image_models': list(p['models']) if p['protocol'] == 'kie' else [], 'video_models': [],
                           'model_limits': p['models']})
        return {'api_providers': result, 'chat_models': [m for p in result for m in p['chat_models']],
                'image_models': [m for p in result for m in p['image_models']], 'video_models': []}

    def redact(self, text, *, used_credential=''):
        text = str(text)
        if used_credential:
            text = text.replace(used_credential, '[redacted]')
        for p in self.providers.values():
            for value in (self.credential(p), p['base_url'], p.get('upload_base_url', ''), p['credential_file']):
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
            if origin(url) not in {origin(v) for v in self.provider.get('media_origins', [])}:
                raise ValueError()
            if self.policy.credential(self.provider) in url or (self.used_credential and self.used_credential in url):
                raise ValueError()
        except (ValueError, TypeError):
            raise failure('download', 502)
        return True

    async def request(self, method, url, **kwargs):
        endpoint = origin(url)
        headers = dict(kwargs.pop('headers', {}))
        has_auth = any(k.lower() in {'authorization', 'x-goog-api-key'} for k in headers)
        if has_auth:
            allowed = {origin(self.provider['base_url'])}
            if self.kie_upload_base_url:
                allowed.add(origin(self.kie_upload_base_url))
            if endpoint not in allowed:
                raise failure('download', 502)
        else:
            self.validate_media_url(url)
            if method.upper() not in {'GET', 'HEAD'}:
                raise failure('download', 502)
        if self.policy.mode == 'mock':
            if endpoint[0] != 'http' or endpoint[1:] not in self.policy.paths.upstreams:
                raise failure('download', 502)
            addresses = {endpoint[1:]}
        else:
            if endpoint[0] != 'https' or endpoint[2] != 443:
                raise failure('download', 502)
            resolved = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, endpoint[1], endpoint[2], type=socket.SOCK_STREAM), 10)
            addresses = {(result[4][0], endpoint[2]) for result in resolved}
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip, _ in addresses):
                raise failure('download', 502)
        context = OUTBOUND_ENDPOINTS.set(frozenset(addresses))
        try:
            # Do not carry an upstream Set-Cookie into uploads or media downloads either.
            self.client.cookies.clear()
            # Stream and cap every response before buffering; even an approved CDN is untrusted.
            async with self.client.stream(method, url, headers=headers, **kwargs) as response:
                if 300 <= response.status_code < 400:
                    raise failure('download', 502)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 32*1024*1024:
                        raise failure('download', 502)
                return httpx.Response(response.status_code, headers=response.headers, content=bytes(data), request=response.request)
        finally:
            OUTBOUND_ENDPOINTS.reset(context)

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request('POST', url, **kwargs)

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        yield await self.request(method, url, **kwargs)

    async def aclose(self):
        await self.client.aclose()
