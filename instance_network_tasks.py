"""Personal network tasks reuse the local adapters with an owned durable ledger."""
import asyncio
import json
import time
from urllib.parse import quote, quote_plus, urlsplit
import httpx
from fastapi import HTTPException
from instance_executor import Execution
from instance_model_policy import failure, secret_variants
from instance_providers import task_provider_revision

from instance_network_operations import NetworkOperations

class NetworkTasks(NetworkOperations):
    def stream_chat(self,payload,request,user_id=''):
        from personal_llm_stream import stream_personal_chat
        return stream_personal_chat(self,payload,request,user_id)

    def create_midjourney(self,payload,action='submit'):
        provider=self.policy.providers.get(payload.provider_id)
        if not provider or not provider.get('personal'):raise failure('not_allowed')
        configured=[m for m,c in provider['capabilities']['image'].items() if c.get('adapter')=='midjourney' and c['executable']]
        if len(configured)!=1:raise HTTPException(400,'请为该个人 Provider 配置一个图片用途的 Midjourney 适配器')
        data=payload.model_dump()
        if action!='submit':
            parent=self.owned(payload.task_id)
            if parent['provider_id']!=payload.provider_id or parent['model']!=configured[0] or parent['provider_revision']!=task_provider_revision(self.policy,provider) or not parent['upstream'] or not parent['upstream'][0]['id']:
                raise HTTPException(409,'原任务与当前 Provider / 模型 / 凭证不匹配')
        refs=getattr(payload,'reference_images',[])
        if action=='modal' and payload.mask_image:refs=[payload.mask_image]
        return self.create(self.app.OnlineImageRequest(provider_id=payload.provider_id,model=configured[0],prompt=payload.prompt or 'Midjourney '+action,size=getattr(payload,'size','1:1'),reference_images=refs,adapter_parameters={'midjourney':dict(data,entry_action=action)}))

    async def execute_midjourney(self,params,execution):
        data=dict(params.get('adapter_parameters',{}).get('midjourney',{}))
        action=data.pop('entry_action','submit')
        if action=='submit':
            payload=self.app.MidjourneySubmitRequest(**data) if data else self.app.MidjourneySubmitRequest(provider_id=execution.provider['id'],prompt=params['prompt'],size=params.get('aspect_ratio') or '1:1',reference_images=params.get('reference_images',[]))
            receipt=await self.app.submit_midjourney(payload)
        else:
            parent=self.owned(data['task_id'])
            data['task_id']=parent['upstream'][0]['id']
            receipt=await (self.app.submit_midjourney_action(self.app.MidjourneyActionRequest(**data)) if action=='action' else self.app.submit_midjourney_modal(self.app.MidjourneyModalRequest(**data)))
        while True:
            result=await self.app.midjourney_result(execution.provider,receipt['task_id'])
            if result['status']=='succeeded':return result['images']
            if result['status']=='failed':raise failure('upstream',502)
            await asyncio.sleep(.05 if self.policy.mode=='mock' else 2)
    async def agent_chat(self,payload,request,user_id=''):
        self.policy.allowed(payload.provider,payload.model,'llm')
        decision=await self.llm(self.app.CanvasLLMRequest(provider=payload.provider,model=payload.model,message=payload.message,system_prompt='Personal API intent: return only JSON {"action":"chat"|"generate_image"|"edit_image"}. Choose images only when explicitly requested. Do not rewrite the prompt.'))
        try:
            action=json.loads(decision['text'])['action']
            if action not in {'chat','generate_image','edit_image'}:raise ValueError()
        except (ValueError,KeyError,TypeError):raise HTTPException(502,'个人文本模型未返回有效的 Agent 意图契约；未提交图片任务') from None
        controlled=payload.model_copy(update={'mode':'image' if action!='chat' else 'chat'})
        if action=='edit_image' and not controlled.reference_images:
            raise HTTPException(400,'图片编辑需要明确提供当前用户的参考图；不会自动改为生成')
        result=await self.chat(controlled,request,user_id)
        result['agent']={'action':action}
        return result

    async def chat(self, payload, request, user_id=''):
        if payload.ms_model:raise HTTPException(400,'请选择个人 Provider 并填写精确模型 ID，不使用全局 ModelScope 路径')
        app=self.app
        owner=app.safe_user_id(user_id,request)
        conversation=app.load_conversation(owner,payload.conversation_id) if payload.conversation_id else app.new_conversation(owner,app.display_title(payload.message))
        image_mode=payload.mode=='image'
        provider_id=(payload.image_provider or payload.provider) if image_mode else payload.provider
        model=(payload.image_model or payload.model) if image_mode else payload.model
        self.policy.allowed(provider_id,model,'image' if image_mode else 'llm')
        user_message=dict(id=__import__('uuid').uuid4().hex,role='user',content=payload.message,created_at=app.now_ms(),attachments=[r.model_dump() for r in payload.reference_images])
        if image_mode:
            result=await self.wait_local(app.OnlineImageRequest(provider_id=provider_id,model=model,prompt=payload.message,size=payload.size,quality=payload.quality,aspect_ratio=payload.aspect_ratio,resolution=payload.resolution,reference_images=payload.reference_images))
            assistant=dict(type='image',content=payload.message,image_url=result['images'][0],task_id=result['task_id'])
        else:
            history=[{'role':m['role'],'content':m['content']} for m in conversation.get('messages',[]) if m.get('role') in {'user','assistant'} and isinstance(m.get('content'),str)]
            result=await self.llm(app.CanvasLLMRequest(provider=provider_id,model=model,message=payload.message,system_prompt=payload.system_prompt,messages=history,images=[r.url for r in payload.reference_images]))
            assistant=dict(content=result['text'])
        assistant.update(id=__import__('uuid').uuid4().hex,role='assistant',model=model,created_at=app.now_ms(),raw_usage=None)
        conversation.setdefault('messages',[]).extend([user_message,assistant])
        conversation['updated_at']=app.now_ms()
        app.save_conversation(owner,conversation)
        return dict(conversation=conversation,message=assistant)

    def validate_network(self, payload, purpose):
        provider, limits = self.policy.allowed(payload.provider_id, payload.model, purpose)
        cap=provider['capabilities'][purpose][payload.model]
        refs = payload.reference_images if purpose == 'image' else payload.images
        if cap.get('reference_transport')=='owned-public-upload':
            _,uploads=self.cloud_registry()
            sources=[r.url for r in refs]+(payload.videos+payload.audios if purpose=='video' else [])
            if any(source not in uploads.values() for source in sources):raise HTTPException(400,cap['reference_reason'])
        if purpose=='video':
            adapter=cap.get('adapter','')
            if payload.resolution and 'resolution' not in cap.get('parameters',[]):raise HTTPException(400,'所选适配器没有分辨率参数契约；不会忽略所选分辨率')
            if adapter in {'agnes-video','tudou-grok-video'}:self.app.personal_video_dimensions(payload.aspect_ratio,payload.resolution,payload.size)
            if cap['protocol']=='openai' and adapter=='openai' and payload.audios:raise HTTPException(400,'通用 OpenAI 视频接口尚未实现音频参考字段契约；请选择已支持该用途的适配器')
            if cap['protocol']=='apimart' and any(r.role for r in refs) and payload.videos:raise HTTPException(400,'此 APIMart 契约不能同时使用图片帧角色与参考视频')
            if cap['protocol']=='volcengine':
                roles=[r.role for r in refs if r.role in {'first_frame','last_frame'}]
                if len(roles)!=len(set(roles)):raise HTTPException(400,'此接口每种帧角色只接受一张图；请修正重复的首尾帧角色')
                if not payload.multimodal:
                    roles=[r.role or 'first_frame' for r in refs]
                    if len(roles)!=len(set(roles)):raise HTTPException(400,'此接口每种帧角色只接受一张图；多张参考图请明确开启多模态参考模式')
            if adapter=='tudou-pixverse' and any(r.role=='first_frame' for r in refs) and any(r.role=='last_frame' for r in refs) and len(refs)!=2:raise HTTPException(400,'此 Pixverse 契约的首尾帧模式不能同时携带其它参考图；不会丢弃参考图')
            if adapter=='yuli-native-video' and (payload.duration!=5 or payload.resolution or payload.audios or payload.videos):raise HTTPException(400,'Yuli 原生接口未实现时长、分辨率和音视频参考参数契约；请选择支持这些参数的适配器')
            if adapter=='yuli-openai-video' and len(refs)>1:raise HTTPException(400,'此 multipart 适配器只有一个 input_reference 字段；不会丢弃其它参考图')
            if adapter in {'tudou-grok-video','yuli-openai-video','lingjing-video','agnes-video'} and (payload.videos or payload.audios):raise HTTPException(400,'所选适配器未实现视频或音频参考字段契约')
            if adapter.startswith('tudou-') and adapter not in {'tudou-seedance','tudou-grok-video'} and (payload.videos or payload.audios):raise HTTPException(400,'所选 Tudou 接口模板未实现音视频参考字段契约')
            if adapter=='tudou-seedance' and payload.audios and not (refs or payload.videos):raise HTTPException(400,'所选模板要求音频参考同时带有图片或视频参考，不会静默忽略音频')
        if purpose=='image':
            if refs and cap.get('edit_reason'):raise HTTPException(400,cap['edit_reason'])
            if payload.quality not in {'','auto'} and 'quality' not in cap.get('parameters',[]):raise HTTPException(400,'所选适配器没有 quality 参数契约；不会忽略或降低所选质量')
            if payload.output_format and 'output_format' not in cap.get('parameters',[]):raise HTTPException(400,'所选适配器没有 output_format 参数契约；不会忽略所选格式')
        extra=getattr(payload,'adapter_parameters',{})
        protected={'model','apikey','walletapikey','volcengineaccesskeyid','volcenginesecretaccesskey','authorization','headers','baseurl','providerid','credentialfile','instanceid','prompt','size','aspectratio','resolution','quality','n','duration','seconds','images','image','imageurls','imageurl','referenceimages','inputreference','videos','videourls','videourl','audios','audiourls','audiourl','firstframeurl','lastframeurl','taskid','requestid','canvasid','nodeid','generationid'}
        if not isinstance(extra,dict) or any(k.lower().replace('_','') in protected for k in extra):
            raise HTTPException(400,'平台参数不能覆盖模型身份、凭证、目标地址或用户身份')
        if 'loras' in extra:
            loras=extra['loras']
            if not isinstance(loras,dict) or any(not isinstance(k,str) or not k or isinstance(v,bool) or not isinstance(v,(int,float)) or not __import__('math').isfinite(v) for k,v in loras.items()):raise HTTPException(400,'LoRA 必须是精确 ID 与有限数值权重的映射；不会自动修正权重')
        if len(json.dumps(extra,ensure_ascii=False).encode()) > 65536:
            raise failure('limits',400)
        if cap.get('adapter')=='midjourney':
            data=extra.get('midjourney',{})
            if not isinstance(data,dict) or data.get('speed','relax') not in self.app.MIDJOURNEY_SPEEDS or data.get('mode','imagine') not in {'imagine','blend','edit'} or not __import__('re').fullmatch(r'\d{1,2}:\d{1,2}',data.get('size',getattr(payload,'aspect_ratio','') or '1:1')):
                raise HTTPException(400,'Midjourney 参数不符合所选接口契约')
            if len(refs)>4 or (data.get('mode')=='blend' and len(refs)<2):raise HTTPException(400,'Midjourney blend 接口契约需要 2–4 张参考图')
        if extra.get('runninghub'):
            self.validate_native_rh(provider,payload.model,extra['runninghub'])
        if cap['protocol']=='runninghub' and not extra.get('runninghub'):
            settings=provider['settings']
            if cap['adapter'] in {'app','workflow'}:
                entry_id=payload.model.partition(':')[2]
                entries=settings.get('rh_apps' if cap['adapter']=='app' else 'rh_workflows',[])
                contract=next(e for e in entries if self.app.runninghub_entry_id(e,cap['adapter'])==entry_id)
                fields=[f for f in contract['fields'] if f.get('enabled') is True]
                if any(not f.get('nodeId') or not f.get('fieldName') for f in fields):
                    raise HTTPException(400,'RunningHub 节点契约缺少 nodeId / fieldName')
                known={str(f.get(k) or '') for f in fields for k in ('fieldName','fieldKey')}
                if set(extra)-known:raise HTTPException(400,'平台参数没有对应的 RunningHub 节点契约')
                for f in fields:
                    value=self.app.runninghub_request_field_value(f,payload.model_dump(),getattr(payload,'size',''))
                    if value is None:continue
                    options=self.app.runninghub_schema_options(f)
                    if options and str(value).lower() not in {str(v).lower() for v in options}:raise HTTPException(400,'RunningHub 节点参数不在已保存的上游接口契约内')
                    self.validate_rh_scalar(f,value)
                for kind,values in [('image',refs),('video',getattr(payload,'videos',[])),('audio',getattr(payload,'audios',[]))]:
                    slots=[f for f in fields if self.app.rh_field_kind(f)==kind]
                    if len(values)>len(slots):raise HTTPException(400,'RunningHub 契约没有足够的 '+kind+' 输入字段；不会丢弃素材')
                    for index,f in enumerate(slots):
                        if f.get('required') is True and index>=len(values) and not self.app.rh_default_value(f):
                            raise HTTPException(400,'缺少 RunningHub 必填 '+kind+' 素材')
            else:
                contract=next((d for d in settings.get('rh_model_definitions',[]) if payload.model in {d.get('id'),d.get('name_en'),d.get('endpoint')}),{})
                fields=contract.get('params',[])
                if contract:
                    image_field=self.app.runninghub_schema_field(fields,'imageUrls','image_urls','imageUrl','image_url','images','image','referenceImages','referenceImageUrls')
                    first=self.app.runninghub_schema_field(fields,'firstFrameUrl','first_frame_url','firstFrameImage','first_frame_image')
                    last=self.app.runninghub_schema_field(fields,'lastFrameUrl','last_frame_url','lastFrameImage','last_frame_image')
                    multiple=image_field and (str(image_field.get('fieldKey','')).endswith('s') or image_field.get('multipleInputs') is True)
                    slots=(int(bool(first))+int(bool(last))) if purpose=='video' else 0
                    if len(refs)>max(slots,20 if multiple else int(bool(image_field))):raise HTTPException(400,'RunningHub 契约没有足够的图片输入字段；不会上传后丢弃素材')
                    if first and first.get('required') is True and not refs:raise HTTPException(400,'缺少 RunningHub 必填首帧素材')
                for category,names in [('videos',('videoUrls','videoUrl','videos','video')),('audios',('audioUrls','audioUrl','audios','audio'))]:
                    values=getattr(payload,category,[]);f=self.app.runninghub_schema_field(fields,*names)
                    if values and not f:raise HTTPException(400,'所选 RunningHub 契约没有 '+category+' 输入字段')
                    if len(values)>1 and f and not (str(f.get('fieldKey','')).endswith('s') or f.get('multipleInputs') is True):raise HTTPException(400,'RunningHub 媒体字段只接收单个素材；不会丢弃其它素材')
                requested=dict(extra)
                requested.update(aspectRatio=getattr(payload,'aspect_ratio',''),resolution=getattr(payload,'resolution',''),quality=getattr(payload,'quality',''))
                if purpose=='video':requested['duration']=payload.duration
                for field in contract.get('params',[]):
                    key=field.get('fieldKey');value=requested.get(key)
                    options=self.app.runninghub_schema_options(field)
                    if value not in (None,'') and options and str(value).lower() not in {str(v).lower() for v in options}:
                        raise HTTPException(400,'参数 '+str(key)+' 不在已保存的上游接口契约中；不会自动降级')
        if purpose=='image' and payload.operation=='edit' and not refs:
            raise HTTPException(400,'图片编辑需要参考素材')
        self.references([r.url for r in refs if not self.trusted_reference(r.url,provider,payload,purpose)], limits)
        if purpose == 'video':
            media=payload.videos+payload.audios
            self.media_references([u for u in media if not self.trusted_reference(u,provider,payload,purpose)], limits)
            if not 1 <= payload.duration <= 3600:
                raise failure('limits', 400)
        else:
            if not 1 <= payload.n <= limits['max_images'] or payload.operation not in {'', 'generate', 'edit'}:
                raise failure('limits', 400)
        # Arbitrary prompts/model IDs are not shortened or replaced.
        if len(payload.prompt) > limits['max_text_chars']:
            raise failure('limits', 400)
        return provider, limits

    def trusted_reference(self,url,provider,payload,purpose):
        if not str(url).startswith('asset://'):return False
        protocol=provider['capabilities'].get(purpose,{}).get(payload.model,{}).get('protocol')
        if purpose!='video' or not getattr(payload,'trusted_asset',False) or protocol not in {'apimart','volcengine'}:raise HTTPException(400,'认证素材 URI 只能用于其已适配的视频平台')
        revision=task_provider_revision(self.policy,provider)
        lib=self.app.load_asset_library()
        for library in lib.get('libraries',[]):
            for category in library.get('categories',[]):
                for item in category.get('items',[]):
                    reg=item.get('registrations',{}).get(protocol,{})
                    if reg.get('asset_uri')==url and reg.get('status')=='Active' and reg.get('provider_id')==provider['id'] and reg.get('provider_revision')==revision:return True
        raise HTTPException(403,'认证素材必须属于当前用户、当前 Provider 及原凭证；不能借用其他用户的 asset URI')

    def media_references(self, urls, limits):
        from pathlib import Path
        if len(urls) > limits['max_references']:
            raise failure('limits', 400)
        for url in urls:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not url.startswith(('/assets/','/output/','/api/storage-files/')):
                raise failure('reference', 400)
            try:
                path = Path(self.paths.user_path(self.app.output_file_from_url(url)))
                if not path.is_file() or path.stat().st_size > limits['max_reference_bytes']:
                    raise ValueError()
            except (TypeError, ValueError, OSError):
                raise failure('reference', 400) from None

    async def wait_local(self, payload, purpose='image'):
        created = self.create(payload, purpose=purpose)
        task_id = created['task_id']
        runner = self.runners.get(task_id)
        if runner:
            await asyncio.shield(runner)
        job = self.owned(task_id)
        if job['status'] != 'succeeded':
            from model_diagnostics import task_diagnostic
            error=task_diagnostic(job.get('diagnostic'));error.detail['task_id']=task_id
            raise error
        return dict(job['result'], task_id=task_id)

    def query_spec(self, execution, task_id, submit_url):
        p = execution.provider
        protocol, purpose = p['protocol'], execution.purpose
        adapter=execution.stored.get('capabilities',{}).get(purpose,{}).get(execution.model,{}).get('adapter')
        if adapter=='midjourney':return dict(method='GET',url=self.app.video_api_root(p)+'/v1/midjourney/'+quote(task_id,safe=''))
        if adapter=='modelscope-async':return dict(method='GET',url=self.app.modelscope_api_root(p)+'/tasks/'+quote(task_id,safe=''),headers={'X-ModelScope-Task-Type':'image_generation'})
        if purpose=='video' and adapter in __import__('provider_capabilities').VIDEO_ADAPTERS:
            root=self.app.video_api_root(p)
            if adapter.startswith('tudou-') and adapter!='tudou-grok-video':return dict(method='GET',url=self.app.tudou_api_endpoint(root,'/tasks/'+quote(task_id,safe='')))
            if adapter=='yuli-native-video':return dict(method='GET',url=root+'/v1/video/query?id='+quote(task_id,safe=''))
            if adapter=='agnes-video':return dict(method='GET',url=root+'/agnesapi?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'video_id':task_id,'model_name':execution.model}))
        if protocol == 'runninghub':
            entry = task_id and execution.model.startswith(('app:', 'workflow:'))
            return dict(method='POST', url=self.app.runninghub_endpoint_url(p, '/task/openapi/outputs') if entry else self.app.runninghub_openapi_url(p, 'query'),
                        body={'taskId':task_id}, body_key=('wallet_api_key' if execution.params.get('adapter_parameters',{}).get('runninghub',{}).get('useWallet') else 'api_key') if entry else '')
        if p.get('image_request_mode','').startswith('openai-responses') and purpose == 'image':
            return dict(method='GET', url=submit_url.rstrip('/')+'/'+quote(task_id, safe=''))
        url = self.app.image_task_url_for_provider(p, quote(task_id,safe='')) if purpose == 'image' else self.app.video_task_url_candidates(p,self.app.video_api_root(p),quote(task_id,safe=''),submit_url)[0]
        return dict(method='GET', url=url)

    def network_outputs(self, raw, purpose, protocol):
        if purpose=='image' and isinstance(raw,dict):
            urls=raw.get('output_images') or self.app.midjourney_remote_images(raw)
            if urls:return [{'type':'url','value':u} for u in urls]
        if protocol == 'runninghub':
            outputs = self.app.runninghub_extract_outputs(raw.get('data') if isinstance(raw,dict) else raw)
            return [{'type':'url','value':u} for u in outputs]
        if purpose == 'video':
            return [{'type':'url','value':u} for u in self.app.video_output_urls(raw)]
        try:
            return self.app.extract_images(raw)
        except HTTPException:
            return []  # A submission receipt is not an image result.

    async def run_network(self, job, cancel, *, query_only=False):
        executions = []
        purpose = job.get('purpose','image')
        try:
            provider, limits = self.policy.allowed(job['provider_id'],job['model'],purpose)
            if job['provider_revision'] != task_provider_revision(self.policy,provider):
                raise failure('conflict',409)
            params = job['params']
            count = len(job['upstream']) if query_only else params.get('n',1)
            collected = []
            for index in range(count):
                entry = job['upstream'][index] if index < len(job['upstream']) else None
                if entry and entry.get('done'):
                    collected.extend(entry['local']); continue
                async def observer(stage, phase, method, url, kwargs, response):
                    nonlocal entry
                    if stage == 'before' and phase == 'submit':
                        if query_only: raise failure('not_allowed')
                        entry = dict(id='',local=[],done=False,submit_url=url)
                        job['upstream'].append(entry)
                        job.update(status='submitting',submission_uncertain=True)
                        self.save(job)
                    elif stage == 'after' and phase in {'submit','query'} and entry:
                        try:
                            raw = response.json()
                        except ValueError:
                            return
                        # Never retain an upstream credential echo in the ledger.
                        serialized = json.dumps(raw,ensure_ascii=False)
                        for key in execution.secrets.values():
                            if key:
                                for value in secret_variants(key):serialized=serialized.replace(value,'[redacted]')
                        raw = json.loads(serialized)
                        if response.is_success:
                            status=self.app.image_task_status(raw)
                            if status in {'FAIL','FAILED','FAILURE','ERROR','CANCELED','CANCELLED'} or (isinstance(raw,dict) and raw.get('code') in {805,'805'}):job.update(upstream_status='fail',outstanding=False,submission_uncertain=False)
                            task_id = self.app.extract_task_id(raw)
                            if not task_id and isinstance(raw,dict) and execution.provider.get('image_request_mode','').startswith('openai-responses'):task_id=raw.get('id')
                            if not task_id and isinstance(raw,dict):
                                task_id=raw.get('video_id')
                            if not task_id and isinstance(raw,dict):
                                data = raw.get('data')
                                if isinstance(data,dict):task_id=data.get('taskId') or data.get('id')
                            if task_id and not entry['id']:
                                entry.update(id=str(task_id),query=self.query_spec(execution,str(task_id),url))
                            outputs = self.network_outputs(raw,purpose,execution.provider['protocol'])
                            if outputs:
                                entry.update(raw=raw,remote_done=True)
                                job.update(upstream_status='success',local_result_status='pending',status='result_pending_download')
                            if entry['id'] or outputs:
                                job['submission_uncertain']=False
                            if phase == 'query' and entry['id'] and not entry.get('remote_done'):
                                # Remember the exact successful read route used by this adapter.
                                entry['query'] = dict(method=method,url=url,body=kwargs.get('json',{}),body_key=('wallet_api_key' if params.get('adapter_parameters',{}).get('runninghub',{}).get('useWallet') else 'api_key') if 'apiKey' in kwargs.get('json',{}) else '')
                                entry['query']['body'].pop('apiKey',None)
                            self.save(job)
                        elif phase == 'submit' and 400 <= response.status_code < 500 and response.status_code != 408:
                            job.update(submission_uncertain=False,upstream_status='fail',outstanding=False)
                            self.save(job)
                execution = Execution(self,provider,job['model'],purpose,observer,cancel)
                execution.params = params
                executions.append(execution)
                with execution.activate():
                    async with asyncio.timeout(limits['timeout_seconds']):
                        if query_only:
                            if not entry: raise failure('not_allowed')
                            raw = entry.get('raw') if entry.get('remote_done') else None
                            while raw is None:
                                spec=entry.get('query')
                                if not spec:raise HTTPException(409,'缺少原任务查询契约；不会重新提交')
                                body=dict(spec.get('body',{}))
                                if spec.get('body_key'):body['apiKey']=execution.key(spec['body_key'] if isinstance(spec['body_key'],str) else 'api_key')
                                headers=(self.app.runninghub_api_headers(execution.provider,use_wallet=True) if execution.provider['protocol']=='runninghub' and not spec.get('body_key') else self.app.runninghub_app_headers(True,spec.get('body_key')=='wallet_api_key',execution.provider) if execution.provider['protocol']=='runninghub' else self.app.api_headers(provider=execution.provider)) | spec.get('headers',{})
                                response=await execution.client().request(spec['method'],spec['url'],headers=headers,**({'json':body} if spec['method']=='POST' else {}))
                                response.raise_for_status()
                                result=response.json()
                                if self.network_outputs(result,purpose,execution.provider['protocol']):raw=result
                                else:
                                    status=self.app.image_task_status(result)
                                    if status in {'FAIL','FAILED','FAILURE','ERROR','CANCELED','CANCELLED'} or result.get('code') in {805,'805'}:
                                        job.update(upstream_status='fail',outstanding=False)
                                        raise failure('upstream',502)
                                    await asyncio.sleep(.05 if self.policy.mode=='mock' else 2.5)
                            outputs=self.network_outputs(raw,purpose,execution.provider['protocol'])
                            local=[await execution.save_image(o) if purpose=='image' else await execution.save_video(o['value']) for o in outputs]
                        elif purpose == 'image':
                            refs=params.get('reference_images',[])
                            if params.get('adapter_parameters',{}).get('runninghub'):local=await self.execute_native_rh(params,execution)
                            elif provider['capabilities']['image'][job['model']].get('adapter')=='midjourney':local=await self.execute_midjourney(params,execution)
                            else:
                                image_data, raw = await self.app.generate_ai_image(params['prompt'],params['size'],params['quality'],job['model'],refs,job['provider_id'],params['aspect_ratio'],params['resolution'],params['output_format'],cancel)
                                outputs=self.network_outputs(raw,'image',execution.provider['protocol']) or [image_data]
                                local=[await execution.save_image(o) for o in outputs]
                        else:
                            payload=self.app.CanvasVideoRequest(**{k:v for k,v in params.items() if k not in {'binding','purpose'}})
                            if params.get('adapter_parameters',{}).get('runninghub'):
                                local=await self.execute_native_rh(params,execution)
                            elif execution.provider['protocol']=='runninghub' and job['model'].startswith(('app:','workflow:')):
                                entry_config=self.app.runninghub_entry_config_from_model(execution.provider,job['model'])
                                output,raw=await self.app.generate_runninghub_entry_image(params['prompt'],params.get('size') or '1024x1024',job['model'],[r.model_dump() for r in payload.images],execution.provider,entry_config)
                                local=[await execution.save_video(o['value']) for o in self.network_outputs(raw,'video','runninghub')]
                            else:
                                result=await self.app.canvas_video(payload)
                                local=result.get('videos',[])
                        if not entry or not local:raise failure('upstream',502)
                        entry.update(local=local,done=True,remote_done=True)
                        entry.pop('raw',None)
                        collected.extend(local)
                        self.save(job)
            result=dict(prompt=params['prompt'],model=job['model'],provider_id=job['provider_id'],task_id=job['id'],timestamp=self.app.now_ms(),binding=job.get('binding',{}))
            if purpose=='image':
                result.update(images=collected,image_items=[dict(url=u,**({'mj_task_id':job['id']} if provider['capabilities']['image'][job['model']].get('adapter')=='midjourney' else {})) for u in collected],size=params['size'],quality=params['quality'])
                self.app.save_to_history(result,identity_key=job['id'])
            else:
                result['videos']=collected
                self.app.save_to_history(result,identity_key=job['id'])
            self.policy.mark_verified(provider,job['model'],purpose)
            job.update(status='succeeded',result=result,outstanding=False,submission_uncertain=False,upstream_status='success',local_result_status='completed',error='',recovery='')
            self.save(job)
        except BaseException as exc:
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
            job['failure_type'] = type(exc).__name__
            job['failure_status'] = getattr(exc,'status_code',None)
            pending=any(e.get('remote_done') and not e.get('done') for e in job['upstream'])
            from model_diagnostics import Diagnostic
            execution=executions[-1] if executions else None
            diagnostic=execution.diagnostic if execution else Diagnostic()
            safe=(diagnostic.error('local_save',phase='save') if pending or getattr(execution,'saving_result',False)
                  else diagnostic.error('cancelled',phase='cancel') if isinstance(exc,asyncio.CancelledError)
                  else getattr(execution,'last_http_failure',None) or diagnostic.from_exception(exc))
            job['diagnostic']=safe.detail

            job.update(status='result_recovery_required' if pending else 'canceled' if isinstance(exc,asyncio.CancelledError) else 'failed',
                       error='上游结果待恢复；只处理原任务，不会再次生成' if pending else '请求未完成；不会自动重新提交',
                       recovery=self.recovery(job),outstanding=bool(job.get('submission_uncertain') or any(not e.get('remote_done') for e in job['upstream'])) and job.get('upstream_status')!='fail')
            self.save(job)
        finally:
            for execution in executions:await execution.close()
