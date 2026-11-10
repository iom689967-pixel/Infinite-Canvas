"""Session-scoped storage adapter for the shared full API settings UI/schema."""
import asyncio
import json
import uuid
from urllib.parse import urlsplit
from fastapi import HTTPException
from provider_schema import ApiProviderPayload, NETWORK_PROTOCOLS, SECRET_FIELDS
from provider_probes import probe_settings
from instance_model_policy import GuardedClient, ModelPolicy
from instance_providers import FIELDS, atomic_private, compile_personal, validate_item

class FullProviderSettings:
    def __init__(self, own):
        self.own, self.models = own, own.models

    def settings(self, stored):
        if 'settings' in stored:
            return dict(stored['settings'])
        item = {k:v for k,v in stored.items() if k in FIELDS and k != 'models'}
        for category,purpose in [('chat','llm'),('image','image'),('video','video')]:
            item[category+'_models'] = [m['id'] for m in stored['models'] if m['purpose']==purpose]
        return self.models.app.normalize_provider(item)

    def public(self):
        items=[]
        for p in self.own.config().get('personal_providers',[]):
            item=self.settings(p)
            compiled=self.models.policy.providers[p['id']]
            item.update(has_key=bool(p['credential_file']),key_preview='',key_env='',personal=True)
            refs=p.get('secret_refs',{})
            item.update(has_wallet_key=bool(refs.get('wallet_api_key')),
                        has_volcengine_access_key=bool(refs.get('volcengine_access_key_id')),
                        has_volcengine_secret_key=bool(refs.get('volcengine_secret_access_key')))
            item['runnable_models']=list(compiled['models']) if item['enabled'] else []
            item['model_support']={m:('runnable' if m in item['runnable_models'] else
                '未配置 Key' if not item['has_key'] else '当前实例运行权限尚未开放此适配器/用途')
                for k in ('image_models','chat_models','video_models') for m in item.get(k,[])}
            items.append(item)
        return {'providers':items}

    def normalize(self, body):
        if not isinstance(body,dict) or set(body)-set(ApiProviderPayload.model_fields):
            raise ValueError('Unexpected Provider fields')
        if body.get('protocol','openai') not in NETWORK_PROTOCOLS:
            raise HTTPException(403,'此实例不开放 CLI / Shell 协议')
        if body.get('image_request_mode','openai') not in self.models.app.SUPPORTED_IMAGE_REQUEST_MODES | {'kie'}:
            raise ValueError('Invalid request mode')
        if body.get('image_edit_route','general') not in {'general','auto','chat'}:
            raise ValueError('Invalid edit route')
        parsed=ApiProviderPayload(**body).model_dump()
        item=self.models.app.normalize_provider(parsed)
        if item['protocol'] not in NETWORK_PROTOCOLS:
            raise HTTPException(403,'此实例不开放 CLI / Shell 协议')
        # The shared schema stores HTTP image mode independently of the adapter.
        if item['protocol']=='kie': item['image_request_mode']='openai'
        projected={k:item[k] for k in FIELDS-{'models'}}
        projected['models']=[{'id':m,'purpose':purpose} for category,purpose in [('chat','llm'),('image','image'),('video','video')] for m in item[category+'_models']]
        validate_item(self.models.policy,projected)
        for name in ('image_generation_endpoint','image_edit_endpoint'):
            endpoint=item.get(name,'')
            if endpoint:
                if endpoint.startswith('/'):
                    if endpoint.startswith('//') or '\\' in endpoint or any(v in {'.','..'} for v in endpoint.split('/')) or '?' in endpoint or '#' in endpoint:
                        raise ValueError('Invalid endpoint')
                else:
                    self.models.policy.validate_config_url(endpoint)
                    if (urlsplit(endpoint).scheme,urlsplit(endpoint).netloc)!=(urlsplit(item['base_url']).scheme,urlsplit(item['base_url']).netloc):
                        raise ValueError('Cross-origin credential target')
        return parsed,item,projected

    def save(self, bodies):
        if not isinstance(bodies,list) or len(bodies)>100:
            raise ValueError('Invalid Provider collection')
        if self.own.discovering or self.models.llm_active or self.models.runners or self.models.outstanding():
            raise HTTPException(409,'仍有运行中或状态不明的任务；配置未修改')
        policy=self.models.policy;config=self.own.config()
        old={p['id']:p for p in config.get('personal_providers',[])}
        shared={p['id'] for p in config['providers']}
        entries={};created=[]
        try:
            for body in bodies:
                parsed,item,projected=self.normalize(body);pid=item['id']
                if pid in entries or pid in shared: raise HTTPException(403,'重复 ID 或不属于个人配置')
                previous=old.get(pid,{});refs=dict(previous.get('secret_refs',{}))
                if previous.get('credential_file'):refs['api_key']=previous['credential_file']
                before=self.settings(previous) if previous else {}
                target_changed=previous and any(before.get(k)!=item.get(k) for k in ('base_url','protocol','image_request_mode','image_edit_route','image_generation_endpoint','image_edit_endpoint','model_protocols','volcengine_project_name','volcengine_region'))
                for secret,clear_field in SECRET_FIELDS.items():
                    key=parsed[secret];clear=parsed[clear_field]
                    if key is not None and (not key or len(key)>4096 or any(c in key for c in '\r\n')):raise ValueError('Invalid credential')
                    if key is not None and clear:raise ValueError('Conflicting credential intent')
                    if target_changed and refs.get(secret) and key is None and not clear:
                        raise HTTPException(409,'认证目标或协议已改变，请重新输入匹配的 Key，或明确清除旧 Key')
                    if clear:refs.pop(secret,None)
                    if key is not None:
                        directory=policy.paths.data_root/'.auth/credentials';directory.mkdir(mode=0o700,exist_ok=True)
                        ref='credentials/personal-'+uuid.uuid4().hex+'.key';path=policy.paths.data_root/'.auth'/ref
                        atomic_private(path,key);created.append(path);refs[secret]=ref
                entry=dict(projected,settings=item,secret_refs=refs,credential_file=refs.get('api_key',''),revision=uuid.uuid4().hex)
                compile_personal(policy,entry);entries[pid]=entry
            config['personal_providers']=list(entries.values())
            atomic_private(policy.file,json.dumps(config,ensure_ascii=False))
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise
        self.models.policy=ModelPolicy(policy.paths)
        retained={ref for p in entries.values() for ref in p.get('secret_refs',{}).values()}
        for p in old.values():
            for ref in set(p.get('secret_refs',{}).values()) | {p.get('credential_file','')}:
                if ref and ref not in retained:(policy.paths.data_root/'.auth'/ref).unlink(missing_ok=True)
        return self.public()

    async def probe(self, body, action):
        if self.own.discovering:raise HTTPException(429,'已有验证请求正在运行')
        if not isinstance(body,dict) or set(body)-{'provider_id','base_url','api_key','protocol','image_request_mode'}:
            raise ValueError('Invalid probe')
        policy=self.models.policy;pid=body.get('provider_id','')
        previous=next((p for p in self.own.config().get('personal_providers',[]) if p['id']==pid),None)
        if pid in policy.providers and not previous:raise HTTPException(403,'仅能验证自己的 Provider')
        _,item,projected=self.normalize({'id':pid,'name':pid,'base_url':body.get('base_url',''),'protocol':body.get('protocol','openai'),'image_request_mode':body.get('image_request_mode','openai')})
        key=body.get('api_key') or ''
        if not isinstance(key,str) or len(key)>4096 or any(c in key for c in '\r\n'):raise ValueError('Invalid credential')
        if not key and previous and previous.get('credential_file'):
            before=self.settings(previous)
            if any(before.get(k)!=item.get(k) for k in ('base_url','protocol')):
                raise HTTPException(409,'地址或协议已改变，请先输入匹配的 Key')
            key=policy.credential(previous)
        provider=dict(projected,personal=True,credential_file=previous.get('credential_file','') if previous else '')
        client=GuardedClient(policy,provider,timeout=15);client.used_credential=key
        self.own.discovering=True
        try:
            async with asyncio.timeout(30):
                return await probe_settings(self.models.app,client,item['base_url'],key,item['protocol'],item['image_request_mode'],action=action)
        except HTTPException:raise
        except Exception:raise HTTPException(502,'只读验证失败；请检查地址、凭证与网络安全限制') from None
        finally:
            self.own.discovering=False
            await client.aclose()
