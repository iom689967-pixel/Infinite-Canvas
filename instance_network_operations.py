"""Owned metadata, platform uploads and native RunningHub node operations."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException
from instance_executor import Execution
from instance_model_policy import failure
from instance_providers import task_provider_revision, atomic_private

class NetworkOperations:
    def validate_rh_scalar(self,field,value):
        kind=self.app.rh_field_kind(field)
        if kind in {'number','slider'}:
            try:
                number=float(value)
                if not __import__('math').isfinite(number):raise ValueError()
                for key,lower in [('min',True),('max',False),('minValue',True),('maxValue',False)]:
                    if field.get(key) not in (None,'') and (number<float(field[key]) if lower else number>float(field[key])):raise ValueError()
            except (TypeError,ValueError):raise HTTPException(400,'RunningHub 数值参数不符合已保存的接口契约') from None
        elif kind=='boolean' and str(value).lower() not in {'true','false','0','1'}:raise HTTPException(400,'RunningHub 布尔参数无效')

    async def avatar_operation(self,item_id,payload,query=False):
        provider=self.policy.providers.get(payload.provider_id)
        if not provider or not provider.get('personal') or not provider.get('enabled') or provider['settings']['protocol'] not in {'apimart','volcengine'}:raise failure('not_allowed')
        platform=provider['settings']['protocol']
        fields=['api_key'] if platform=='apimart' else ['volcengine_access_key_id','volcengine_secret_access_key']
        if not all(provider.get('secret_refs',{}).get(f) for f in fields):raise HTTPException(400,'平台素材接口缺少所需私有凭证：'+', '.join(fields))
        lib=self.app.load_asset_library()
        item=self.app.find_asset_item_in_library(lib,item_id,payload.library_id)
        if not item:raise HTTPException(404,'当前用户没有该素材')
        reg=item.get('registrations',{}).get(platform,{})
        revision=task_provider_revision(self.policy,provider)
        if query:
            if reg.get('provider_id')!=provider['id'] or reg.get('provider_revision')!=revision:raise HTTPException(409,'原素材审核的 Provider / 凭证不匹配；不会使用新凭证查询')
            if not reg.get('task_id'):raise HTTPException(409,'原审核提交状态不明且没有任务 ID；不会自动再次提交')
        else:
            self.media_references([item.get('url','')],{'max_references':1,'max_reference_bytes':30*1024*1024})
            if reg.get('status') in {'Processing','Active','SubmissionUnknown'}:raise HTTPException(409,'素材已有审核或状态待确认；请查询原任务')
            self.check_capacity()
        async def observer(stage,phase,*args):
            if stage=='before' and phase=='submit':
                item.setdefault('registrations',{})[platform]=dict(provider_id=provider['id'],provider_revision=revision,status='SubmissionUnknown',task_id='',asset_uri='',detail='提交状态待确认；不会自动再次提交')
                self.app.save_asset_library(lib)
        execution=Execution(self,provider,'','metadata',None if query else observer)
        execution.signed_asset=platform=='volcengine'
        if platform=='volcengine' and not query:
            _,uploads=self.cloud_registry()
            public_url=payload.public_url or next((url for url,value in uploads.items() if value==item.get('url')), '')
            if not public_url or uploads.get(public_url)!=item.get('url'):raise HTTPException(400,'火山资产接口需要已由当前用户发布的公网素材 URL；请先使用素材云端上传，不会使用其他用户或服务器文件')
            execution.public_asset_url=public_url
        self.llm_active+=1
        try:
            with execution.activate():
                async with asyncio.timeout(600):
                    result=await (self.app.check_asset_library_avatar(item_id,payload) if query else self.app.register_asset_library_avatar(item_id,payload))
            result['item']['registrations'][platform]['provider_revision']=revision
            self.app.save_asset_library(result['library'])
            return result
        finally:
            self.llm_active-=1
            await execution.close()

    def cloud_registry(self):
        path=self.paths.data_root/'.auth/cloud-uploads.json'
        if path.is_symlink():raise failure('not_allowed')
        return path,json.loads(path.read_text()) if path.exists() else {}

    async def cloud_upload(self,payload):
        self.media_references([payload.url],{'max_references':1,'max_reference_bytes':30*1024*1024})
        self.check_capacity()
        service=getattr(payload,'service','auto')
        if service not in {'auto','temp','temp.sh','tempsh','litterbox','catbox'}:raise HTTPException(400,'未适配此云端上传服务')
        targets={'temp.sh':'https://temp.sh/upload','litterbox':'https://litterbox.catbox.moe/resources/internals/api.php'}
        if self.policy.mode=='mock':
            host,port=sorted(self.paths.upstreams)[0]
            targets={kind:f'http://{host}:{port}/anonymous/{kind}/upload' for kind in targets}
        provider=dict(id='anonymous-upload',personal=True,settings=dict(protocol='openai',base_url=targets['temp.sh']),secret_refs={})
        execution=Execution(self,provider,'','metadata');execution.anonymous_uploads=frozenset(targets.values());execution.upload_targets=targets
        self.llm_active+=1
        try:
            with execution.activate():
                async with asyncio.timeout(600):result=await self.app.upload_local_video_to_cloud(payload.url,service)
            client=execution.client();client.validate_media_url(result['url'])
            response=await client.get(result['url']);response.raise_for_status()
            path,registry=self.cloud_registry();registry[result['url']]=payload.url
            atomic_private(path,json.dumps(registry))
            return result
        finally:
            self.llm_active-=1
            await execution.close()

    def owned_platform(self, provider_id='', protocol='runninghub'):
        candidates=[p for p in self.policy.providers.values() if p.get('personal') and p.get('enabled') and p['settings']['protocol']==protocol]
        if provider_id:
            provider=next((p for p in candidates if p['id']==provider_id),None)
        else:
            provider=candidates[0] if len(candidates)==1 else None
        if not provider:raise HTTPException(400,'请选择当前用户启用的 '+protocol+' Provider；多平台时必须明确指定')
        return provider

    def upload_registry(self):
        path=self.paths.data_root/'.auth/network-uploads.json'
        if path.is_symlink():raise failure('not_allowed')
        return path,json.loads(path.read_text()) if path.exists() else {}

    async def native_rh_upload(self,payload):
        provider=self.owned_platform(payload.provider_id)
        self.media_references([payload.url], {'max_references':1,'max_reference_bytes':30*1024*1024})
        execution=Execution(self,provider,'','metadata')
        if not execution.key('wallet_api_key' if payload.useWallet else 'api_key'):raise failure('not_configured')
        self.check_capacity()
        self.llm_active+=1
        try:
            with execution.activate():
                async with asyncio.timeout(600):result=await self.app.runninghub_upload_asset(payload)
            token=result['data']['fileName']
            if not isinstance(token,str) or len(token)>2048:raise failure('upstream',502)
            path,registry=self.upload_registry()
            registry[provider['id']+'|'+token]=task_provider_revision(self.policy,provider)
            atomic_private(path,json.dumps(registry))
            return result
        finally:
            self.llm_active-=1
            await execution.close()

    def validate_native_rh(self, provider, model, data):
        kind,_,entry_id=model.partition(':')
        if data.get('provider_id')!=provider['id'] or data.get('entry_action',kind)!=kind or data.get('webappId' if kind=='app' else 'workflowId')!=entry_id:raise failure('not_allowed')
        cap=provider['capabilities'].get(data.get('purpose','image'),{}).get(model,{})
        if cap.get('adapter') not in {'app','workflow'}:raise failure('not_allowed')
        entries=provider['settings'].get('rh_apps' if cap['adapter']=='app' else 'rh_workflows',[])
        entry=next(e for e in entries if self.app.runninghub_entry_id(e,cap['adapter'])==model.partition(':')[2])
        fields={(str(f.get('nodeId')),str(f.get('fieldName'))):f for f in entry['fields'] if f.get('enabled') is True}
        received={}
        _,registry=self.upload_registry()
        for node in data.get('nodeInfoList',[]):
            if set(node)-{'nodeId','fieldName','fieldValue'}:raise failure('not_allowed')
            key=(str(node.get('nodeId')),str(node.get('fieldName')))
            field=fields.get(key)
            if not field or key in received:raise HTTPException(400,'RunningHub 参数不属于用户已保存的节点契约')
            value=node.get('fieldValue')
            self.validate_rh_scalar(field,value)
            if self.app.rh_field_kind(field) in {'image','video','audio'} and value:
                if registry.get(provider['id']+'|'+str(value))!=task_provider_revision(self.policy,provider):raise HTTPException(403,'素材必须由当前用户上传到当前 Provider；不能使用其他任务或用户的 fileName')
            options=field.get('options') or []
            if options and str(value) not in {str(v) for v in options}:raise HTTPException(400,'参数不在已保存的上游接口契约内')
            received[key]=value
        if any(f.get('required') is True and received.get(k) in (None,'') for k,f in fields.items()):raise HTTPException(400,'缺少 RunningHub 必填节点参数')
        # A supplied workflow can only prune the saved graph; executable code,
        # file paths and links cannot be introduced by a node request.
        workflow=data.get('workflow')
        if workflow:
            if isinstance(workflow,str):
                try:workflow=json.loads(workflow)
                except ValueError:raise failure('not_allowed') from None
            original=entry.get('workflowJson',{})
            if not isinstance(workflow,dict) or not original:raise HTTPException(400,'请先保存该工作流接口契约')
            for key,node in workflow.items():
                saved=original.get(key,{})
                if node.get('class_type')!=saved.get('class_type') or any(v!=saved.get('inputs',{}).get(k) for k,v in node.get('inputs',{}).items()):raise HTTPException(400,'请求只能裁剪已保存工作流，不能替换节点或输入')
        if data.get('useWallet') and not provider.get('secret_refs',{}).get('wallet_api_key'):raise HTTPException(400,'缺少 RunningHub 钱包凭证')

    def create_native_rh(self,payload,kind):
        provider=self.owned_platform(payload.provider_id)
        model=kind+':'+(payload.webappId if kind=='app' else payload.workflowId)
        purpose=payload.purpose
        self.policy.allowed(provider['id'],model,purpose)
        data=payload.model_dump();data['provider_id']=provider['id']
        self.validate_native_rh(provider,model,data)
        common=dict(provider_id=provider['id'],model=model,prompt='RunningHub '+kind,adapter_parameters={'runninghub':dict(data,entry_action=kind)},request_id=payload.request_id,canvas_id=payload.canvas_id,node_id=payload.node_id,generation_id=payload.generation_id)
        created=self.create(self.app.OnlineImageRequest(**common) if purpose=='image' else self.app.CanvasVideoRequest(**common),purpose=purpose)
        return {'success':True,'data':{'taskId':created['task_id']}}

    async def execute_native_rh(self,params,execution):
        data=dict(params['adapter_parameters']['runninghub']);kind=data.pop('entry_action')
        payload=(self.app.RunningHubSubmitRequest if kind=='app' else self.app.RunningHubWorkflowSubmitRequest)(**data)
        result=await (self.app.runninghub_submit(payload) if kind=='app' else self.app.runninghub_workflow_submit(payload))
        tid=result['data']['taskId']
        while True:
            response=await execution.client().post(self.app.runninghub_endpoint_url(execution.provider,'/task/openapi/outputs'),headers=self.app.runninghub_app_headers(True,payload.useWallet),json={'apiKey':execution.key('wallet_api_key' if payload.useWallet else 'api_key'),'taskId':tid})
            response.raise_for_status();raw=response.json()
            if raw.get('code') in {805,'805'}:raise failure('upstream',502)
            outputs=self.network_outputs(raw,execution.purpose,'runninghub')
            if outputs:return [await execution.save_image(o) if execution.purpose=='image' else await execution.save_video(o['value']) for o in outputs]
            await asyncio.sleep(.05 if self.policy.mode=='mock' else 2.5)

    def native_rh_query(self,task_id):
        job=self.owned(task_id)
        if self.policy.providers.get(job['provider_id'],{}).get('settings',{}).get('protocol')!='runninghub':raise failure('not_allowed')
        if job['status'] in {'failed','canceled','result_recovery_required'}:self.refresh(task_id)
        result=job.get('result') or {};urls=result.get('images') or result.get('videos') or []
        status='SUCCESS' if job['status']=='succeeded' else 'FAILED' if job.get('upstream_status')=='fail' else 'RUNNING'
        return {'success':True,'data':{'status':status,'urls':urls,'image_items':[{'url':u} for u in urls],'failReason':job.get('error',''),'task_id':job['id']}}

    async def native_rh_metadata(self,provider_id,kind,entry_id,payload=None):
        provider=self.owned_platform(provider_id)
        execution=Execution(self,provider,'','metadata')
        if not execution.key():raise failure('not_configured')
        self.check_capacity();self.llm_active+=1
        try:
            with execution.activate():
                async with asyncio.timeout(120):
                    result=await (self.app.runninghub_app_info(entry_id) if kind=='app' else self.app.fetch_runninghub_workflow(payload or self.app.RunningHubWorkflowConfig(workflowId=entry_id)))
            return json.loads(self.policy.redact(json.dumps(result,ensure_ascii=False)))
        finally:
            self.llm_active-=1
            await execution.close()

    def native_rh_workflow(self,provider_id,workflow_id='',payload=None,delete=False):
        provider=self.owned_platform(provider_id)
        entries=provider['settings'].get('rh_workflows',[])
        if not workflow_id:return {'workflows':[dict(workflowId=self.app.runninghub_entry_id(e,'workflow'),title=e.get('title',''),fieldCount=len(e.get('fields',[]))) for e in entries]}
        entry=next((e for e in entries if self.app.runninghub_entry_id(e,'workflow')==workflow_id),None)
        if payload is None and not delete:
            if not entry:raise HTTPException(404,'当前用户没有该工作流')
            return {'workflow':entry}
        from instance_provider_settings import FullProviderSettings
        from instance_providers import OwnProviders
        adapter=FullProviderSettings(OwnProviders(self))
        settings=[adapter.settings(p) for p in adapter.own.config().get('personal_providers',[])]
        target=next(p for p in settings if p['id']==provider['id'])
        remaining=[e for e in entries if self.app.runninghub_entry_id(e,'workflow')!=workflow_id]
        if payload:
            entry=payload.model_dump(exclude={'provider_id'});entry.update(id=workflow_id,workflowId=workflow_id)
            remaining.append(entry)
        target['rh_workflows']=remaining
        if delete:
            for category in ('image_models','video_models'):target[category]=[m for m in target[category] if m!='workflow:'+workflow_id]
        adapter.save(settings)
        return {'success':True,'workflow':entry}
