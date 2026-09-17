"""Shared network adapter capabilities, independent of account authorization.

Model IDs are opaque upstream identifiers. Presets describe contracts, never a
global model allowlist. A configured model may have several independent uses.
"""
from provider_schema import NETWORK_PROTOCOLS

CATEGORIES = {'llm': 'chat_models', 'image': 'image_models', 'video': 'video_models'}
IMAGE_MODES = {'openai', 'openai-json', 'openai-responses','openai-responses-stream','openai-responses-sync', 'openai-video-proxy', 'tudou-async','modelscope-async'}
IMAGE_ADAPTERS = IMAGE_MODES | {'tudou-grok-image'}
VIDEO_ADAPTERS = {'apimart-veo31','tudou-grok-video','tudou-sora2','tudou-veo31','tudou-kling','tudou-pixverse','tudou-seedance','yuli-openai-video','yuli-native-video','lingjing-video','agnes-video'}
# This is the implemented executor inventory, not a user permission list.
PROTOCOL_USES = {
    'openai': {'llm', 'image', 'video'},
    'gemini': {'llm', 'image'},
    'apimart': {'llm', 'image', 'video'},
    'volcengine': {'llm', 'image', 'video'},
    'runninghub': {'llm', 'image', 'video'},
    'kie': {'image'},
}

def effective_protocol(settings, model, purpose=''):
    overrides=settings.get('model_protocols', {})
    return overrides.get(purpose+'|'+model,overrides.get(model,settings.get('protocol','openai')))

def selected_adapter(settings, model, purpose):
    overrides=settings.get('model_adapters', {})
    # Legacy image adapter selections must not disable another use of this ID.
    return overrides.get(purpose+'|'+model,overrides.get(model,'') if purpose=='image' or (purpose=='video' and effective_protocol(settings,model,purpose)=='runninghub') else '')

def resolve(settings, model, purpose):
    entry=None
    protocol = effective_protocol(settings, model, purpose)
    chosen=selected_adapter(settings,model,purpose)
    result = dict(protocol=protocol, executor=protocol, purpose=purpose,
                  configured=model in settings.get(CATEGORIES.get(purpose, ''), []),
                  executable=False, verified=False, reason='', credential_fields=['api_key'])
    if protocol not in NETWORK_PROTOCOLS:
        result['reason'] = '未适配此网络协议'
    elif purpose not in PROTOCOL_USES[protocol]:
        result['reason'] = '当前协议未实现此用途的接口契约；可选择已实现的逐模型协议'
    elif not result['configured']:
        result['reason'] = '未配置模型或用途'
    elif protocol=='runninghub' and purpose=='llm' and model.startswith(('app:','workflow:')):
        result['reason']='RunningHub app/workflow 的文本结果契约尚未实现；兼容文本 API 请明确选择逐模型 OpenAI 协议'
    elif protocol == 'kie':
        from providers.kie.models import KIE_UI_MODELS
        template = chosen or model
        if template not in KIE_UI_MODELS:
            result['reason'] = '缺少 Kie 模型 input 契约；请选择已有模板适配器'
        else:
            result.update(executable=True, adapter=template, asynchronous=True)
            from providers.prompt_limits import resolve_prompt_capability
            result['prompt_limits']={kind:resolve_prompt_capability('kie',model,count) for kind,count in [('text',0),('image',1)]}
    elif protocol == 'runninghub' and purpose != 'llm':
        kind, _, entry_id = model.partition(':')
        result['asynchronous'] = True
        if kind in {'app', 'workflow'}:
            entries = settings.get('rh_apps' if kind == 'app' else 'rh_workflows', [])
            entry = next((e for e in entries if str(e.get('id') or e.get('webappId') or e.get('appId') or e.get('workflowId')) == entry_id), None)
            if not entry or not entry.get('fields'):
                result['reason'] = '缺少 RunningHub app/workflow 的节点参数契约'
            elif entry.get('enabled') is False or entry.get('hidden') is True:
                result['reason'] = 'RunningHub app/workflow 已停用'
            else:
                result.update(executable=True, adapter=kind)
        else:
            definitions=settings.get('rh_model_definitions',[])
            definition=next((d for d in definitions if model in {d.get('id'),d.get('name_en'),d.get('endpoint')}),None)
            if not definition and chosen != 'runninghub-openapi':
                result['reason']='缺少 RunningHub OpenAPI 参数契约；请拉取目录或选择标准 OpenAPI 适配器'
            else:
                result.update(executable=True, adapter='openapi', credential_fields=['wallet_api_key'])
    elif purpose == 'image':
        mode = chosen or settings.get('image_request_mode', 'openai')
        if protocol=='apimart' and chosen=='midjourney':
            result.update(executable=True,adapter='midjourney',asynchronous=True,edit=True)
        elif protocol == 'openai' and mode not in IMAGE_ADAPTERS:
            result['reason'] = '未适配此图片请求模式'
        else:
            result.update(executable=True, adapter=mode if protocol == 'openai' else protocol,
                          asynchronous=protocol == 'apimart' or mode in {'openai-responses','openai-responses-stream','openai-responses-sync', 'openai-video-proxy', 'tudou-async','modelscope-async'},
                          edit=True, edit_route=settings.get('image_edit_route', 'general'))
    else:
        result.update(executable=True, adapter=chosen or protocol, asynchronous=purpose == 'video')
    if chosen and not result.get('reason'):
        valid=(protocol=='kie' and purpose=='image') or (protocol=='runninghub' and chosen=='runninghub-openapi') or (protocol=='openai' and purpose=='image' and chosen in IMAGE_ADAPTERS) or (protocol=='openai' and purpose=='video' and chosen in VIDEO_ADAPTERS) or (protocol=='apimart' and ((purpose=='image' and chosen=='midjourney') or (purpose=='video' and chosen=='apimart-veo31')))
        if not valid:result.update(executable=False,reason='此用途没有所选适配器的接口契约')
    result['parameters']=['prompt','size','aspect_ratio','resolution'] if purpose=='image' else ['prompt','duration','aspect_ratio','resolution'] if purpose=='video' else ['max_output_tokens']
    if purpose=='video' and result.get('adapter')=='yuli-native-video':result['parameters']=['prompt','aspect_ratio','enhance_prompt','enable_upsample']
    if purpose=='video' and result.get('adapter') in {'tudou-sora2','tudou-kling'}:result['parameters'].remove('resolution')
    if result.get('adapter')=='openai-video-proxy' or result.get('adapter')=='agnes-video' or (purpose=='video' and result.get('adapter','').startswith('tudou-') and result['adapter']!='tudou-grok-video'):
        result.update(reference_transport='owned-public-upload',reference_reason='此接口要求公开素材链接；请先对当前用户素材使用云端上传（temp.sh / Litterbox），再生成')
    if purpose=='image' and protocol=='openai' and result.get('adapter') in {'openai','openai-json','openai-responses','openai-responses-stream','openai-responses-sync','tudou-async'}:result['parameters']+=['quality']
    if purpose=='image' and protocol=='openai' and result.get('adapter') in {'openai','openai-json','openai-responses','openai-responses-stream','openai-responses-sync'}:result['parameters']+=['output_format']
    if purpose=='image' and protocol=='runninghub':
        if result.get('adapter')=='openapi':
            definition=next((d for d in settings.get('rh_model_definitions',[]) if model in {d.get('id'),d.get('name_en'),d.get('endpoint')}),None)
            if definition is None or any(f.get('fieldKey')=='quality' for f in definition.get('params',[])):result['parameters']+=['quality']
        else:
            for field in (entry or {}).get('fields',[]):
                if field.get('enabled') and str(field.get('fieldName','')).lower()=='quality':result['parameters'].append('quality')
    if purpose=='image' and settings.get('image_edit_route')=='chat':
        result.update(edit=False,edit_reason='image_edit_route=chat 的图片响应契约尚未实现；请选已有图片编辑适配器')
    return result

def capabilities(settings, credential_fields=()):
    available = set(credential_fields)
    result = {}
    for purpose, category in CATEGORIES.items():
        result[purpose] = {}
        for model in settings.get(category, []):
            cap = resolve(settings, model, purpose)
            if not settings.get('enabled', True):
                cap.update(executable=False, reason='Provider 已停用')
            elif cap['executable'] and not set(cap['credential_fields']) <= available:
                cap.update(executable=False, reason='缺少凭证：' + ', '.join(set(cap['credential_fields']) - available))
            cap['state'] = 'protocol_executable' if cap['executable'] else 'configured'
            result[purpose][model] = cap
    return result
