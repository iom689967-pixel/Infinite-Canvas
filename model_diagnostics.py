"""Safe model errors: no raw response, user content, URL or credential is logged."""
from contextvars import ContextVar
import json
import logging
import time
import uuid
import httpx
from fastapi import HTTPException

ISSUED_EVENTS = ContextVar('issued_model_events', default=None)

def issued_detail(detail):
    if not isinstance(detail,dict):return False
    base={k:v for k,v in detail.items() if k!='task_id'}
    events=ISSUED_EVENTS.get() or {}
    return base==events.get(base.get('event_id'))

MESSAGES = {
    'capacity':'当前实例并发已满或有待确认任务，请先查询原任务。',
    'resource_limit':'请求超出工作区资源保护范围。',
    'reference':'参考素材不属于本工作区或格式超出保护范围。',
    'conflict':'请求标识已使用，不能替换原请求内容。',
    'site_auth':'本站登录已失效，请重新登录。',
    'permission':'当前 Provider、模型用途或凭证未获授权，请检查个人 API 设置。',
    'upstream_auth':'上游 API 鉴权失败，请检查该 Provider 的授权；本站登录仍有效。',
    'routing_unavailable':'中转当前没有可调度的上游账号；未自动重试。',
    'upstream_unavailable':'Provider 服务暂时不可用，是否已提交仍需核对；未自动重试。',
    'rate_limit':'上游请求限流，请稍后手动重试。',
    'invalid_parameters':'上游拒绝了模型或参数，请检查配置；未自动重试。',
    'network':'上游网络连接中断，远端状态可能待确认；未自动重试。',
    'timeout':'模型请求超过等待预算，远端状态可能待确认；未自动重试。',
    'response_structure':'上游响应结构不符合所选协议，未标记成功。',
    'response_parse':'无法解析上游响应，未标记成功。',
    'business_error':'上游返回业务错误，未标记成功。',
    'refused':'上游明确拒绝本次内容请求。',
    'empty_result':'上游返回空结果，未标记成功。',
    'incomplete_stream':'上游流未提供可靠的完成标记；部分内容不是完整结果。',
    'local_save':'结果未能保存到本工作区；请恢复原任务，不要重新生成。',
    'maintenance':'工作区维护中，暂时不能提交新任务，请稍后再试。',
    'cancelled':'本地等待已取消，远端任务状态仍可能待确认；不会重新提交。',
}
LEGACY_CATEGORY={'not_configured':'permission','not_allowed':'permission','limits':'resource_limit','reference':'reference','download':'local_save','task_incomplete':'local_save','upstream':'upstream_unavailable','busy':'capacity','conflict':'conflict','storage_full':'local_save','server_storage_full':'local_save'}

class Diagnostic:
    def __init__(self):
        # Never derive correlation/ownership from a client request_id or header.
        self.event_id=uuid.uuid4().hex
        self.started=time.monotonic()
        self.provider_status=None
    def error(self,category,*,status=502,code=None,phase='response',provider_status=None):
        if category not in MESSAGES:category='response_structure'
        if phase not in {'validation','submit','response','parse','save','stream','cancel','timeout'}:phase='response'
        if provider_status is not None:self.provider_status=provider_status if type(provider_status) is int and 100<=provider_status<=599 else None
        detail=dict(code=code or category,category=category,message=MESSAGES[category],event_id=self.event_id,
                    canvas_status=status,provider_status=self.provider_status,upstream_status=None,
                    phase=phase,upstream_correlation='not_confirmed')
        events=ISSUED_EVENTS.get()
        if events is not None:events[self.event_id]=dict(detail)
        logging.getLogger('mio.model').warning('%s',json.dumps(dict(detail,elapsed_ms=round((time.monotonic()-self.started)*1000)),ensure_ascii=False))
        error=HTTPException(status_code=status,detail=detail,headers={'X-Mio-Event-Id':self.event_id})
        error._mio_safe_error=True
        return error
    def from_exception(self,exc):
        from llm_contracts import ContractError
        if isinstance(exc,ContractError):return self.error(exc.category,phase='parse')
        if isinstance(exc,HTTPException):
            if getattr(exc,'_mio_safe_error',False):return exc
            code=exc.detail.get('code') if isinstance(exc.detail,dict) else None
            category='maintenance' if code=='maintenance' else LEGACY_CATEGORY.get(code,'permission' if exc.status_code in {401,403} else 'invalid_parameters' if exc.status_code<500 else 'response_structure')
            return self.error(category,status=exc.status_code,code=code,phase='validation')
        if isinstance(exc,(TimeoutError,httpx.TimeoutException)):return self.error('timeout',status=504,phase='timeout')
        if isinstance(exc,httpx.HTTPStatusError):return self.http_error(exc.response)
        if isinstance(exc,httpx.HTTPError):return self.error('network',phase='response')
        if isinstance(exc,(ValueError,UnicodeError)):return self.error('response_parse',phase='parse')
        return self.error('response_structure',phase='parse')
    def http_error(self,response):
        status=response.status_code
        category='upstream_unavailable'
        if status in {401,403}:category='upstream_auth'
        elif status==429:category='rate_limit'
        elif 400<=status<500 and status!=408:category='invalid_parameters'
        elif status==408:category='timeout'
        # Only machine-readable routing codes count as routing evidence. A bare
        # 503 or an arbitrary prose message cannot prove no remote submission.
        try:
            data=response.json();error=data.get('error',{}) if isinstance(data,dict) else {}
            code=error.get('code') if isinstance(error,dict) else None
            if status==503 and code in {'no_available_accounts','no_available_upstream','routing_unavailable'}:category='routing_unavailable'
        except (ValueError,TypeError):pass
        return self.error(category,provider_status=status,phase='submit' if category=='routing_unavailable' else 'response')

def safe_failure(code,status=403):
    return Diagnostic().error(LEGACY_CATEGORY.get(code,code),status=status,code=code,phase='validation')


def task_diagnostic(detail, *, status=502):
    """Reissue safe persisted metadata only after callers verify task ownership."""
    import re
    d=Diagnostic()
    if isinstance(detail,dict):
        if isinstance(detail.get('event_id'),str) and re.fullmatch(r'[a-f0-9]{32}',detail['event_id']):d.event_id=detail['event_id']
        return d.error(detail.get('category','response_structure'),status=status,code='task_incomplete',phase=detail.get('phase','response'),provider_status=detail.get('provider_status'))
    return d.error('response_structure',status=status,code='task_incomplete')
