"""One producer owns HTTP/context lifetime; response disconnect always joins it."""
import asyncio
import contextlib
import json
import uuid
import anyio
from fastapi import HTTPException
from starlette.responses import StreamingResponse
from instance_executor import Execution
from instance_model_policy import secret_variants
from llm_contracts import parse_response,SSEText,ContractError
from model_budgets import for_execution
from model_diagnostics import Diagnostic

class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            # Starlette can cancel stream_response while body_iterator is
            # suspended at yield. Close explicitly; never rely on GC finalizers.
            with anyio.CancelScope(shield=True):
                async with asyncio.timeout(5):
                    close=getattr(self.body_iterator,'aclose',None)
                    if close:await close()

async def stream_personal_chat(models,payload,request,user_id=''):
    queue=asyncio.Queue(maxsize=1)
    async def produce():
        execution=None;active=False;text='';carry='';conversation=None;assistant=None;owner=None;finished=False
        diagnostic=Diagnostic()
        async def emit(value):await queue.put(models.app.sse_event(value))
        def save_conversation():
            try:models.app.save_conversation(owner,conversation)
            except Exception:raise diagnostic.error('local_save',phase='save') from None
        def persist_incomplete(category):
            if conversation is None:return
            if assistant and assistant in conversation.get('messages',[]):conversation['messages'].remove(assistant)
            if text:
                conversation.setdefault('messages',[]).append(dict(id=uuid.uuid4().hex,role='assistant',content=text,created_at=models.app.now_ms(),model=payload.model,incomplete=True,status=category))
            conversation['last_error']=dict(category=category,event_id=diagnostic.event_id)
            conversation['updated_at']=models.app.now_ms();save_conversation()
        try:
            if payload.mode=='image':raise HTTPException(400,'图片聊天请使用图片任务入口')
            if payload.ms_model:raise HTTPException(400,'请使用个人 Provider 的精确模型')
            provider,limits=models.policy.allowed(payload.provider,payload.model,'llm')
            models.check_capacity()
            models.references([r.url for r in payload.reference_images],limits)
            owner=models.app.safe_user_id(user_id,request)
            conversation=models.app.load_conversation(owner,payload.conversation_id) if payload.conversation_id else models.app.new_conversation(owner,models.app.display_title(payload.message))
            messages=[{'role':m['role'],'content':m['content']} for m in conversation.get('messages',[]) if m.get('role') in {'user','assistant'} and isinstance(m.get('content'),str) and not m.get('incomplete')]
            if sum(len(m['content']) for m in messages)+len(payload.message)+len(payload.system_prompt)>limits['max_text_chars']:raise HTTPException(400,'文本超过工作区安全上限')
            execution=Execution(models,provider,payload.model,'llm');diagnostic=execution.diagnostic
            models.llm_active+=1;active=True
            user_message=dict(id=uuid.uuid4().hex,role='user',content=payload.message,attachments=[r.model_dump() for r in payload.reference_images],created_at=models.app.now_ms())
            conversation.setdefault('messages',[]).append(user_message);conversation['updated_at']=models.app.now_ms();save_conversation()
            await emit({'type':'meta','conversation':conversation})
            secrets=[]
            for p in models.policy.providers.values():
                for field in p.get('secret_refs',{}) or {'api_key':p.get('credential_file','')}:
                    try:secrets.extend(secret_variants(models.policy.credential(p,field)))
                    except HTTPException:pass
            guard=max([len(s) for s in secrets] or [1])-1
            async def delta(value,*,flush=False):
                nonlocal carry,text
                carry+=value
                for secret in secrets:carry=carry.replace(secret,'[redacted]')
                if flush or len(carry)>guard:
                    chunk=carry if flush or not guard else carry[:-guard]
                    carry='' if flush or not guard else carry[-guard:]
                    if chunk:text+=chunk;await emit({'type':'delta','delta':chunk})
            budget=for_execution('llm',limits['timeout_seconds'])
            with execution.activate():
                from llm_contracts import compose_messages, build_request
                messages=compose_messages(payload.system_prompt,messages,payload.message,
                    [models.app.reference_to_data_url(r.model_dump()) for r in payload.reference_images])
                url,body=build_request(execution.provider['protocol'],provider['base_url'],payload.model,messages,stream=True,
                    max_output_tokens=getattr(payload,'max_output_tokens',None),temperature=getattr(payload,'temperature',None))
                headers=({'x-goog-api-key':execution.key()} if execution.provider['protocol']=='gemini' else {'Authorization':'Bearer '+execution.key()}) | {'Content-Type':'application/json'};client=execution.client();usage=None
                async with asyncio.timeout(budget.total):
                    if execution.provider['protocol']=='gemini':
                        response=await client.post(url,headers=headers,json=body);diagnostic.provider_status=response.status_code;response.raise_for_status()
                        result=parse_response(response.json());usage=result.usage
                        await delta(result.text,flush=True)
                    else:
                        async with client.stream('POST',url,headers=headers,json=body,guarded_live=True) as response:
                            diagnostic.provider_status=response.status_code
                            if response.is_error:
                                await response.aread();raise diagnostic.http_error(response)
                            if 'text/event-stream' not in response.headers.get('content-type',''):
                                result=parse_response(json.loads(await response.aread()));usage=result.usage
                                await delta(result.text,flush=True)
                            else:
                                parser=SSEText()
                                async for line in response.aiter_lines():
                                    value=parser.line(line)
                                    if value:await delta(value)
                                    if parser.complete:break
                                result=parser.finish();usage=result.usage
                                await delta('',flush=True)
            execution.finish_maintenance_submission()
            assistant=dict(id=uuid.uuid4().hex,role='assistant',content=text,created_at=models.app.now_ms(),model=payload.model,raw_usage=usage,status='completed')
            conversation['messages'].append(assistant);conversation.pop('last_error',None);conversation['updated_at']=models.app.now_ms()
            save_conversation()
            models.policy.mark_verified(provider,payload.model,'llm');finished=True
            await emit({'type':'done','conversation':conversation,'message':assistant})
        except asyncio.CancelledError:
            if not finished:
                try:persist_incomplete('cancelled')
                except HTTPException:pass # Safe save diagnostic already recorded; preserve cancellation.
            raise
        except Exception as exc:
            if execution and isinstance(exc,ContractError) and exc.complete:execution.finish_maintenance_submission()
            error=diagnostic.from_exception(exc)
            if not finished:
                try:persist_incomplete(error.detail['category'])
                except HTTPException as save_error:error=save_error
            await emit({'type':'error','detail':error.detail})
        finally:
            if active:models.llm_active-=1
            if execution:
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(5):await execution.close()
    task=asyncio.create_task(produce())
    try:
        while True:
            get=asyncio.create_task(queue.get())
            try:
                done,_=await asyncio.wait({get,task},return_when=asyncio.FIRST_COMPLETED)
                if get in done:yield get.result()
                elif not queue.empty():yield queue.get_nowait()
                else:
                    await task
                    break
            finally:
                get.cancel()
                with anyio.CancelScope(shield=True):await asyncio.gather(get,return_exceptions=True)
    finally:
        task.cancel()
        with anyio.CancelScope(shield=True):
            async with asyncio.timeout(5):await asyncio.gather(task,return_exceptions=True)

async def await_with_disconnect(coroutine,request):
    """Nonstream requests also stop local waiting when their client disconnects."""
    task=asyncio.create_task(coroutine)
    async def watch():
        while True:
            if (await request.receive())['type']=='http.disconnect':
                task.cancel();return
    watcher=asyncio.create_task(watch())
    try:return await task
    finally:
        watcher.cancel();task.cancel()
        with anyio.CancelScope(shield=True):await asyncio.gather(watcher,task,return_exceptions=True)
