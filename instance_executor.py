"""Dependency injection for the existing network executors (never owner fallback)."""
from contextvars import ContextVar
from contextlib import contextmanager
import asyncio
import base64
import json
from io import BytesIO
from urllib.parse import urlsplit,parse_qs
from PIL import Image
import httpx as real_httpx
from fastapi import HTTPException
from instance_model_policy import GuardedClient, failure
from provider_capabilities import effective_protocol

EXECUTION = ContextVar('personal_provider_execution', default=None)

class HttpxFacade:
    """Only main's HTTP dependency is injected; no process-global monkey patch."""
    def __getattr__(self, name):
        if name == 'AsyncClient' and EXECUTION.get():
            return EXECUTION.get().client
        return getattr(real_httpx, name)

class Execution:
    def __init__(self, models, provider, model, purpose, observer=None, cancel=None):
        self.models, self.policy, self.stored = models, models.policy, provider
        self.model, self.purpose, self.observer, self.cancel = model, purpose, observer, cancel
        self.provider = dict(provider.get('settings', provider))
        self.provider['protocol'] = effective_protocol(self.provider, model,purpose)
        self.provider['personal'] = True
        cap=provider.get('capabilities',{}).get(purpose,{}).get(model,{})
        self.adapter=cap.get('adapter','')
        if purpose=='image' and self.provider['protocol']=='openai' and self.adapter in __import__('provider_capabilities').IMAGE_MODES:
            self.provider['image_request_mode']=cap.get('adapter',self.provider.get('image_request_mode','openai'))
        elif purpose=='image' and self.provider['protocol']!='openai':
            # OpenAI request mode is unrelated to a per-model native protocol.
            # Normalize only the injected runtime copy, preserving saved settings.
            self.provider['image_request_mode']='openai'
        self.secrets = {field:self.policy.credential(provider, field) for field in provider.get('secret_refs', {})}
        if not self.secrets and provider.get('credential_file'):
            self.secrets['api_key'] = self.policy.credential(provider)
        self.provider.update(self.secrets)
        self.clients = []
        self.submitted = False
        from model_diagnostics import Diagnostic
        self.diagnostic = Diagnostic()

    def key(self, field='api_key'):
        return self.secrets.get(field, '')

    def client(self, **kwargs):
        # Local executor timeout declarations cannot relax the Instance boundary.
        targets=dict(self.stored)
        if self.provider['protocol']!='kie':targets.pop('upload_base_url',None)
        client = GuardedClient(self.policy, targets, timeout=1800)
        client.default_headers = dict(kwargs.get('headers', {}))
        client.execution = self
        client.used_credential = self.key()
        self.clients.append(client)
        return client

    def classify(self, method, url):
        path = urlsplit(str(url)).path.rstrip('/')
        if getattr(self,'signed_asset',False):
            action=parse_qs(urlsplit(str(url)).query).get('Action',[''])[0]
            if action in {'ListAssetGroups','GetAsset'}:return 'query'
            if action=='CreateAssetGroup':return 'upload'
        if str(url) in getattr(self,'anonymous_uploads',()):return 'upload'
        if method.upper() in {'GET', 'HEAD'}:
            return 'query'
        if path.endswith(('/query', '/outputs', '/models', '/model-list', '/ai-app/detail', '/workflow/json', '/getJsonApiFormat')):
            return 'query'
        if path.endswith(('/upload','/binary','/files','/assets','/presign')) or (self.provider['protocol']=='apimart' and '/uploads/' in path):
            return 'upload'
        return 'submit'

    async def before(self, method, url, kwargs):
        if self.cancel and self.cancel.is_set():
            raise asyncio.CancelledError()
        # Cap the request before any upload/submission is recorded or sent.
        size = len(json.dumps(kwargs.get('json'),ensure_ascii=False).encode()) if 'json' in kwargs else 0
        for value in (kwargs.get('content'), kwargs.get('data')):
            if isinstance(value,(str,bytes,bytearray)):size += len(value.encode() if isinstance(value,str) else value)
            elif isinstance(value,(dict,list,tuple)):size += len(json.dumps(value,ensure_ascii=False).encode())
        files = kwargs.get('files',{})
        for _, value in (files.items() if isinstance(files,dict) else files):
            value = value[1] if isinstance(value,tuple) else value
            if isinstance(value,(str,bytes,bytearray)):size += len(value.encode() if isinstance(value,str) else value)
            elif hasattr(value,'seek') and hasattr(value,'tell'):
                position=value.tell()
                value.seek(0,2)
                size += value.tell()-position
                value.seek(position)
            else:raise failure('limits',400)
        if size > 32*1024*1024 - 65536:raise HTTPException(400,'Instance 请求体安全上限为 32 MiB（含素材及协议开销），请减少本次素材')
        phase = self.classify(method, url)
        if phase == 'submit':
            if self.submitted:
                raise HTTPException(409, '已提交过此生成请求；禁止回退接口或自动重复生成，请查询原任务')
            body = kwargs.get('json') or kwargs.get('data') or {}
            if isinstance(body,(list,tuple)):
                try:body=dict(body)
                except (TypeError,ValueError):raise failure('limits',400) from None
            params = getattr(self,'params',{})
            if isinstance(body,dict) and self.purpose == 'image':
                if self.provider['protocol']=='gemini':
                    config=body.setdefault('generationConfig',{}).setdefault('imageConfig',{})
                    if params.get('resolution'):config['imageSize']=params['resolution']
                    if params.get('aspect_ratio'):config['aspectRatio']=params['aspect_ratio']
                elif self.provider['protocol']=='apimart':
                    if params.get('resolution'):body['resolution']=params['resolution'].lower()
                    if params.get('aspect_ratio'):body['size']=params['aspect_ratio']
                elif self.provider['protocol']=='openai' and self.provider.get('image_request_mode')=='openai-json':
                    if params.get('quality'):body['quality']=params['quality']
            if isinstance(body,dict) and self.purpose=='video':
                if 'duration' in body:body['duration']=params.get('duration',body['duration'])
                if 'seconds' in body:body['seconds']=str(params.get('duration',body['seconds']))
                if 'resolution' in body and params.get('resolution'):body['resolution']=params['resolution']
            if isinstance(body,dict) and self.purpose=='image' and params.get('output_format'):
                if self.provider.get('image_request_mode','').startswith('openai-responses'):
                    for tool in body.get('tools',[]):
                        if tool.get('type')=='image_generation':tool['output_format']=params['output_format']
                else:body['output_format']=params['output_format']
            extra={k:v for k,v in params.get('adapter_parameters',{}).items() if k not in {'midjourney','runninghub'}}
            if self.adapter in {'app','workflow'}:extra={}
            if isinstance(body,dict):body.update(extra)
            if kwargs.get('files') and not kwargs.get('json') and not kwargs.get('data'):
                pairs=list(kwargs['files'].items() if isinstance(kwargs['files'],dict) else kwargs['files'])
                fields={k:v[1] for k,v in pairs if isinstance(v,tuple) and v[0] is None}
                sent=fields.get('model')
                if sent and sent!=self.model:raise HTTPException(400,'适配器必须原样提交模型 ID')
                pairs=[(k,(None,str(extra.pop(k)))) if k in extra and k in fields else (k,v) for k,v in pairs]
                pairs.extend((k,(None,json.dumps(v) if isinstance(v,(dict,list)) else str(v))) for k,v in extra.items())
                kwargs['files']=pairs
            sent_model = body.get('model') if isinstance(body, dict) else None
            if sent_model and sent_model != self.model:
                raise HTTPException(400, '此适配器会改写模型 ID；请填写精确上游 ID，不会自动替换模型')
            if kwargs.get('json') is not None and len(json.dumps(kwargs['json'],ensure_ascii=False).encode())>32*1024*1024:
                raise failure('limits',400)
            self.submitted = True
        if self.observer:
            await self.observer('before', phase, method, str(url), kwargs, None)
        if phase == 'submit' and self.purpose == 'llm':
            from instance_maintenance import CURRENT_ACTIVITY
            activity = CURRENT_ACTIVITY.get()
            if activity:
                self.maintenance_submission = (activity.gate, activity.gate.uncertain_llm(activity.instance))

    async def after(self, method, url, kwargs, response):
        # An upstream proxy 5xx/408 is not evidence the original remote LLM
        # stopped; retain its receipt for administrator reconciliation.
        if (self.purpose == 'llm' and self.classify(method, url) == 'submit'
            and response.status_code < 500 and response.status_code != 408):
            self.finish_maintenance_submission()
        if self.observer:
            await self.observer('after', self.classify(method, url), method, str(url), kwargs, response)

    def finish_maintenance_submission(self):
        receipt = getattr(self, 'maintenance_submission', None)
        if receipt:
            receipt[0].complete_llm(receipt[1])
            self.maintenance_submission = None

    @contextmanager
    def activate(self):
        token = EXECUTION.set(self)
        try:
            yield self
        finally:
            EXECUTION.reset(token)

    async def close(self):
        for client in self.clients:
            await client.aclose()

    async def save_image(self, image_data, prefix='api_', category='output'):
        if image_data.get('type') == 'url':
            response = await self.client().get(image_data['value'])
            response.raise_for_status()
            data = response.content
        elif image_data.get('type') in {'b64', 'base64'}:
            try:
                data = base64.b64decode(image_data['value'], validate=True)
            except (ValueError, TypeError):
                raise failure('download', 502) from None
        else:
            raise failure('download', 502)
        if len(data) > 32*1024*1024:
            raise failure('download', 502)
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height > 32_000_000:
                    raise ValueError()
                image.load()
                extension={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}[image.format]
        except (OSError, ValueError, Image.DecompressionBombError):
            raise failure('download', 502) from None
        app = self.models.app
        filename = prefix + __import__('uuid').uuid4().hex + extension
        app.write_public_media(app.output_path_for(filename, category), data)
        return app.output_url_for(filename, category)

    async def save_video(self, url, prefix='video_', category='output'):
        response = await self.client().get(url)
        response.raise_for_status()
        data = response.content
        # Container signature validation without invoking unrestricted subprocesses.
        mp4 = len(data) >= 12 and data[4:8] == b'ftyp'
        webm = data.startswith(b'\x1a\x45\xdf\xa3')
        if not data or not (mp4 or webm):
            raise failure('download', 502)
        app = self.models.app
        filename = prefix + __import__('uuid').uuid4().hex + ('.mp4' if mp4 else '.webm')
        app.write_public_media(app.output_path_for(filename, category), data)
        return app.output_url_for(filename, category)
