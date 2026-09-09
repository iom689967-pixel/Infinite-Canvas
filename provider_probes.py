"""Shared, read-only Provider detection and model classification orchestration.

Only GET catalog/query endpoints are used. No generation/empty POST probes, raw
upstream bodies, URLs or credentials are returned to the settings UI.
"""
import re
from urllib.parse import urlsplit

MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}')

async def probe_settings(app, client, base_url, credential, protocol, image_mode, *, action='fetch-models'):
    base = base_url.rstrip('/')
    host = (urlsplit(base).hostname or '').lower()
    if protocol == 'kie' or host == 'api.kie.ai':
        # Public root reachability is distinct from Key validity and task submission.
        checks = {'key_configured':bool(credential), 'whitelist':True, 'read_only':True}
        status, ok = 200, bool(credential)
        message = 'Kie 适配目录；未验证 Key 有效性、未连接生成接口'
        if action == 'test-connection':
            response = await client.get(base, headers={'Accept':'text/html,application/json'})
            status = response.status_code
            ok = status < 500 and not 300 <= status < 400
            checks['reachable'] = ok
            message = 'Kie 公共地址可访问；未验证 Key 有效性' if ok else 'Kie 公共地址验证未通过'
        return app.kie_settings_model_payload(ok=ok, status=status, status_code=status,
            message=message, checks=checks)
    if protocol not in {'openai', 'gemini', 'apimart', 'volcengine', 'runninghub'}:
        raise ValueError('Unsupported network protocol')
    if not credential:
        raise ValueError('Credential required')
    response = await client.get(app.upstream_models_url(base, protocol),
                               headers=app.upstream_model_headers(credential, protocol))
    status = response.status_code
    try:
        raw = response.json()
    except (ValueError, TypeError):
        raw = {}
    # Native Gemini catalogs identify themselves, even through a compatible gateway.
    if isinstance(raw, dict) and not raw.get('data') and any(isinstance(v, dict) and str(v.get('name','')).startswith('models/gemini-') for v in (raw.get('models') if isinstance(raw.get('models'),list) else [])):
        protocol = 'gemini'
    if status in {404, 405} and action == 'probe-async' and protocol == 'openai':
        alternate = await client.get(app.upstream_models_url(base, 'gemini'),
                                    headers=app.upstream_model_headers(credential, 'gemini'))
        if alternate.status_code == 200 and 'json' in alternate.headers.get('content-type',''):
            candidate = alternate.json()
            if isinstance(candidate,dict) and isinstance(candidate.get('models'),list):
                protocol, response, status, raw = 'gemini', alternate, 200, candidate
    grouped, ids = app.parse_upstream_models(raw, protocol)
    if protocol == 'runninghub':
        registry = app.runninghub_registry_items_from_raw(raw)
        if registry:
            payload = app.runninghub_registry_payload(registry)
            grouped = {k:payload[k+'_models'] for k in ('image','chat','video')}
            ids = payload['all']
    if protocol == 'gemini' and 200 <= status < 300:
        grouped, ids, _ = await app.supplement_gemini_gateway_models(client, base, credential, grouped, ids)
    grouped, ids = app.apply_agnes_model_defaults(base, grouped, ids)
    grouped = app.apply_locked_recommended_model_rules(base, grouped)
    ids = [v for v in ids if MODEL_ID.fullmatch(v) and credential not in v][:200]
    grouped = {k:[v for v in values if v in ids] for k,values in grouped.items()}
    ok = 200 <= status < 300 and bool(ids)
    if action != 'fetch-models' and status not in {401,403} and (
            protocol in {'apimart','volcengine'} or (protocol == 'openai' and action == 'probe-async')):
        url = (app.volcengine_task_probe_url(base) if protocol == 'volcengine' else
               (base if base.endswith('/v1') else base+'/v1')+'/tasks/healthcheck_probe_do_not_submit')
        task = await client.get(url, headers=app.upstream_model_headers(credential, protocol))
        try:
            body = task.json()
        except (ValueError, TypeError):
            body = {}
        error = str(body.get('error', body.get('message',''))).lower() if isinstance(body,dict) else ''
        task_evidence = task.status_code in {400,404} and 'task' in error and any(
            phrase in error for phrase in ('not found','notfound','不存在','invalid task id'))
        if task_evidence:
            protocol = 'volcengine' if protocol == 'volcengine' else 'openai' if app.is_tudou_base_url(base) else 'apimart'
            ok, status = True, task.status_code
        elif protocol in {'apimart','volcengine'} and action == 'probe-async':
            ok, status = False, task.status_code
    mode = app.detect_image_request_mode(base,ids) or app.normalize_image_request_mode(image_mode)
    if app.is_tudou_base_url(base): mode = 'tudou-async'
    return {'ok':ok,'status':status,'status_code':status,'protocol':protocol,
            'image_request_mode':mode,'total':len(ids),'model_count':len(ids),'all':ids,
            'image_models':grouped['image'],'chat_models':grouped['chat'],'video_models':grouped['video'],
            'message':'只读目录验证通过；请保存协议与模型' if ok else '只读探测未能确认协议；请检查地址、权限或手动配置',
            'checks':{'read_only':True}}
