"""Controlled Canvas calls using existing Gemini/Kie adapters and a private durable ledger."""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import HTTPException
from PIL import Image

from instance_auth import PRINCIPAL
from instance_model_policy import GuardedClient, ModelPolicy, failure
from providers.kie.client import KieClient, KieAPIError
from providers.kie.models import build_create_payload, KieValidationError
from providers.kie.tasks import poll_task, KieTaskCancelled, KieTaskError, task_status
from providers.kie.uploads import KieReferenceUploadCache, prepare_kie_references


class ControlledModels:
    def __init__(self, paths, application):
        self.paths, self.app = paths, application
        self.policy = ModelPolicy(paths)
        self.database = paths.data_root / '.auth/model-tasks.sqlite3'
        self.runners, self.cancels = {}, {}
        self.llm_active = 0
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, fingerprint TEXT, nonce TEXT, data TEXT, updated REAL)')
            # No createTask is ever replayed after a process crash.
            for row in db.execute('SELECT id,data FROM jobs').fetchall():
                job = json.loads(row['data'])
                if job['status'] in {'queued', 'preparing', 'submitting', 'submitted', 'generating', 'waiting', 'queuing'}:
                    job.update(status='failed', recovery=self.recovery(job),
                               error='服务重启，提交状态待确认；不会自动重新提交', outstanding=bool(job.get('outstanding')))
                    db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(job), row['id']))
        os.chmod(self.database, 0o600)

    @contextmanager
    def db(self):
        if self.database.is_symlink():
            raise RuntimeError('Private task ledger cannot be a symlink')
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, job):
        with self.db() as db:
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (json.dumps(job), time.time(), job['id']))

    def owned(self, task_id):
        with self.db() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=? AND owner=?', (task_id, PRINCIPAL.get()['subject'])).fetchone()
        if not row:
            raise HTTPException(404, '本实例没有此任务')
        return json.loads(row['data'])

    def outstanding(self):
        with self.db() as db:
            return sum(bool(json.loads(row['data']).get('outstanding')) for row in db.execute('SELECT data FROM jobs'))

    def check_capacity(self):
        if self.outstanding() + self.llm_active >= self.policy.max_concurrent:
            raise failure('busy', 429)

    @staticmethod
    def recovery(job):
        if job.get('submission_uncertain'):
            return 'manual-reconcile'
        return 'query-existing' if job['upstream'] else ''

    def references(self, urls, limits):
        if len(urls) > limits['max_references']:
            raise failure('limits')
        paths = []
        for url in urls:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not url.startswith(('/assets/', '/output/', '/api/storage-files/')):
                raise failure('reference')
            try:
                path = Path(self.paths.user_path(self.app.output_file_from_url(url)))
                if not path.is_file() or path.stat().st_size > limits['max_reference_bytes']:
                    raise ValueError()
                with Image.open(path) as image:
                    if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width*image.height > 32_000_000:
                        raise ValueError()
                    image.verify()
                paths.append(str(path))
            except (OSError, ValueError, TypeError, SyntaxError, Image.DecompressionBombError):
                raise failure('reference') from None
        return paths

    async def llm(self, payload):
        provider, limits = self.policy.allowed(payload.provider, payload.model, 'llm')
        if payload.videos or payload.ms_model or len(payload.message) + len(payload.system_prompt) > limits['max_text_chars']:
            raise failure('limits')
        messages = []
        for item in payload.messages:
            if set(item) - {'role', 'content'} or item.get('role') not in {'user', 'assistant'} or not isinstance(item.get('content'), str):
                raise failure('limits')
            messages.append({'role': item['role'], 'content': item['content']})
        if sum(len(m['content']) for m in messages) + len(payload.message) + len(payload.system_prompt) > limits['max_text_chars']:
            raise failure('limits')
        self.references(payload.images, limits)
        self.check_capacity()
        self.llm_active += 1
        client = GuardedClient(self.policy, provider, timeout=min(self.app.CANVAS_LLM_TIMEOUT, limits['timeout_seconds']))
        try:
            if payload.system_prompt:
                messages.insert(0, {'role': 'system', 'content': payload.system_prompt})
            content = [{'type': 'text', 'text': payload.message}]
            for url in payload.images:
                content.append({'type': 'image_url', 'image_url': {'url': self.app.reference_to_data_url({'url':url}, max_size=1536)}})
            messages.append({'role': 'user', 'content': content})
            url, body = self.app.chat_upstream_request(provider, provider['base_url'], payload.model, messages)
            body['generationConfig'] = {'maxOutputTokens': limits['max_output_tokens'], 'candidateCount': 1}
            credential = self.policy.credential(provider)
            client.used_credential = credential
            # httpx read timeouts alone reset for each chunk; also bound total waiting time.
            async with asyncio.timeout(min(self.app.CANVAS_LLM_TIMEOUT, limits['timeout_seconds'])):
                response = await client.post(url, headers={'x-goog-api-key': credential, 'Content-Type':'application/json'}, json=body)
            response.raise_for_status()
            text = self.app.text_from_chat_response(response.json())
            return {'text': self.policy.redact(text, used_credential=credential), 'model':payload.model, 'raw_usage':None}
        except (TimeoutError, httpx.TimeoutException):
            raise failure('timeout', 504) from None
        except httpx.HTTPStatusError:
            raise failure('upstream', 502) from None
        except httpx.HTTPError:
            raise failure('network', 502) from None
        except (ValueError, KeyError, TypeError):
            raise failure('upstream', 502) from None
        finally:
            self.llm_active -= 1
            await client.aclose()

    def validate_image(self, payload):
        provider, limits = self.policy.allowed(payload.provider_id, payload.model, 'image')
        if not 1 <= payload.n <= limits['max_images'] or payload.operation or payload.resolution_type or payload.quality not in {'', 'auto'}:
            raise failure('limits')
        try:
            _, adapted = build_create_payload(payload.model, payload.prompt, [r.url for r in payload.reference_images],
                                               payload.aspect_ratio, payload.resolution, payload.output_format)
        except KieValidationError:
            raise failure('limits', 400) from None
        checks = {'sizes': payload.size, 'resolutions': adapted['requested_resolution'],
                  'aspect_ratios': adapted['requested_aspect_ratio'], 'output_formats': adapted['output_format']}
        if any(value not in limits[key] for key, value in checks.items()):
            raise failure('limits')
        self.references([r.url for r in payload.reference_images], limits)
        return provider, limits

    def create(self, payload):
        self.validate_image(payload)
        owner = PRINCIPAL.get()['subject']
        params = payload.model_dump()
        nonce = params.pop('request_id', '')
        fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            for row in db.execute('SELECT * FROM jobs WHERE owner=? ORDER BY updated DESC', (owner,)):
                job = json.loads(row['data'])
                if nonce and row['nonce'] == nonce:
                    if row['fingerprint'] != fingerprint:
                        raise failure('conflict', 409)
                    return {'task_id':job['id'], 'status':job['status'], 'reused':True}
                if row['fingerprint'] == fingerprint and (job['outstanding'] or row['updated'] > time.time()-60):
                    return {'task_id':job['id'], 'status':job['status'], 'reused':True}
        self.check_capacity()
        job = {'id':'canvas_img_'+uuid.uuid4().hex, 'owner':owner, 'status':'queued', 'params':params,
               'provider_id':payload.provider_id, 'model':payload.model, 'upstream':[], 'result':None,
               'error':'', 'outstanding':True, 'recovery':'', 'submission_uncertain':False, 'created_at':time.time()}
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (job['id'],owner,fingerprint,nonce,json.dumps(job),time.time()))
        self.launch(job)
        return {'task_id':job['id'], 'status':'queued', 'reused':False}

    def launch(self, job, *, query_only=False):
        cancel = asyncio.Event()
        self.cancels[job['id']] = cancel
        self.runners[job['id']] = asyncio.create_task(self.run(job, cancel, query_only=query_only))
        # A queued task can be canceled before its coroutine reaches its finally block.
        def cleanup(task):
            if self.runners.get(job['id']) is task:
                self.runners.pop(job['id'], None)
                self.cancels.pop(job['id'], None)
        self.runners[job['id']].add_done_callback(cleanup)

    def public(self, job):
        return {k:job.get(k) for k in ('id','status','provider_id','model','result','error','recovery','created_at')} | {
            'has_upstream_task':bool(job['upstream']), 'upstream_cancel_supported':False,
            'local_wait_active':job['id'] in self.runners and not self.runners[job['id']].done(),
            'cancel_scope':job.get('cancel_scope',''), 'error_code':job.get('error_code','')}

    def get(self, task_id):
        return self.public(self.owned(task_id))

    def refresh(self, task_id):
        job = self.owned(task_id)
        self.policy.allowed(job['provider_id'], job['model'], 'image')
        if task_id not in self.runners and job['upstream'] and job['status'] != 'succeeded':
            if not job['outstanding']:
                self.check_capacity()
                job['outstanding'] = True
                self.save(job)
            job['status'] = 'submitted'
            self.save(job)
            # A refresh can only query existing IDs; it cannot create remaining batch images.
            self.launch(job, query_only=True)
        return self.public(job)

    def cancel(self, task_id):
        job = self.owned(task_id)
        cancel = self.cancels.get(task_id)
        if cancel:
            cancel.set()
        hard = job['status'] in {'queued', 'preparing'} and not job['upstream']
        if hard and task_id in self.runners:
            self.runners[task_id].cancel()
        if job['status'] != 'succeeded':
            job.update(status='canceled', cancel_scope='pre-submit' if hard else 'local-only',
                       error='已在提交前停止' if hard else '已停止本地等待；供应商未确认取消，可能继续计算或计费')
            if hard:
                job['outstanding'] = False
            self.save(job)
        return self.public(job) | {'task_id':task_id, 'message':job['error']}

    async def run(self, job, cancel, *, query_only=False):
        client = None
        submitted = False
        try:
            provider, limits = self.policy.allowed(job['provider_id'], job['model'], 'image')
            client = GuardedClient(self.policy, provider, timeout=120)
            credential = self.policy.credential(provider)
            client.used_credential = credential
            kie = KieClient(credential, base_url=provider['base_url'], http_client=client)
            params = job['params']
            references = params['reference_images']
            urls = []
            if not query_only:
                job['status'] = 'preparing'; self.save(job)
                self.references([r['url'] for r in references], limits)
                # Separate cache namespace for provider + credential rotation; never share upload ownership.
                namespace = hashlib.sha256((provider['id']+credential).encode()).hexdigest()[:24]
                cache = KieReferenceUploadCache(self.paths.data_root/f'.auth/reference-cache/{namespace}.json', url_validator=client.validate_media_url)
                urls, _audits = await prepare_kie_references(credential, references,
                    resolve_local_path=lambda value:self.paths.user_path(self.app.output_file_from_url(value)),
                    max_bytes=limits['max_reference_bytes'], cache=cache, http_client=client)
            count = len(job['upstream']) if query_only else params['n']
            for index in range(count):
                if cancel.is_set():
                    raise KieTaskCancelled('local stop')
                if index >= len(job['upstream']):
                    body, _ = build_create_payload(job['model'], params['prompt'], urls,
                        params['aspect_ratio'], params['resolution'], params['output_format'])
                    job.update(status='submitting', submission_uncertain=True); self.save(job)
                    submitted = True  # Durable uncertainty precedes every potentially billable call.
                    upstream_id, _ = await kie.create_task(body)
                    job['upstream'].append({'id':upstream_id, 'local':[], 'done':False})
                    job.update(status='submitted', submission_uncertain=False); self.save(job)
                entry = job['upstream'][index]
                if entry['done']:
                    continue
                async def status(value, _raw):
                    job['status'] = value if value in {'waiting','queuing','generating'} else 'submitted'
                    self.save(job)
                result = await poll_task(kie, entry['id'], timeout_seconds=limits['timeout_seconds'],
                    initial_interval=.05 if self.policy.mode == 'mock' else 2.5, cancel_event=cancel, on_status=status)
                entry['remote_done'] = True; self.save(job)
                for url in result['resultUrls'][:1]:
                    if cancel.is_set():
                        raise KieTaskCancelled('local stop')
                    response = await client.get(url)
                    response.raise_for_status()
                    with Image.open(BytesIO(response.content)) as image:
                        if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height > 32_000_000:
                            raise failure('download', 502)
                        image.load()
                        normalized = BytesIO(); image.convert('RGB').save(normalized, 'PNG')
                    local = await self.app.save_ai_image_to_output({'type':'b64','value':base64.b64encode(normalized.getvalue()).decode()}, prefix='controlled_')
                    entry['local'] = [local]
                entry['done'] = True; self.save(job)
            images = [url for entry in job['upstream'] for url in entry['local']]
            result = {'images':images, 'image_items':[self.app.image_output_meta(url) for url in images],
                      'prompt':params['prompt'], 'timestamp':job['created_at'], 'type':'online',
                      'model':job['model'], 'provider_id':job['provider_id'], 'task_id':job['id'], 'params':params}
            if job.get('submission_uncertain'):
                # Querying known batch IDs must not erase an unknown later submission.
                job.update(status='failed', result=result, outstanding=True, recovery='manual-reconcile',
                           error='已保存已知任务结果；另有提交状态不明，需管理员核对，未自动补交')
            else:
                self.app.save_to_history(result)
                job.update(status='succeeded', result=result, outstanding=False, error='', recovery='')
        except (asyncio.CancelledError, KieTaskCancelled):
            # Never claim provider cancellation: these adapters have no cancellation API.
            job.update(status='canceled', cancel_scope='local-only' if submitted or job['upstream'] else 'pre-submit',
                       error='已停止本地连接或等待；未获得供应商取消/退款确认',
                       outstanding=bool(submitted or job['upstream']), recovery=self.recovery(job))
        except Exception as exc:
            known = bool(job['upstream'])
            code = 'timeout' if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else 'network' if isinstance(exc, httpx.HTTPError) else 'upstream'
            if isinstance(exc, KieAPIError) and exc.code in {'timeout', 'network'}:
                code = exc.code
            if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
                code = exc.detail['code']
            outstanding = submitted or known
            terminal_failure = isinstance(exc, KieTaskError) and task_status(exc.raw) == 'fail'
            if isinstance(exc, KieTaskError) and task_status(exc.raw) in {'fail','success'}:
                outstanding = False
            if known and not job.get('submission_uncertain') and all(entry.get('remote_done') for entry in job['upstream']):
                outstanding = False
            job.update(status='failed', error=failure(code,502).detail['message'], error_code=code, outstanding=outstanding,
                       recovery='' if terminal_failure else self.recovery(job))
        finally:
            self.save(job)
            if client:
                await client.aclose()
            self.runners.pop(job['id'], None)
            self.cancels.pop(job['id'], None)
