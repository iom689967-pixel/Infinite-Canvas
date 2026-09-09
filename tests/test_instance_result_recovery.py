"""Real local HTTP, isolated authenticated A/B processes; never a public provider."""
import json
import socket
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
from fastapi import HTTPException
from instance_model_policy import GuardedClient
from instance_auth import AuthStore
import test_instance_models as models


class RecoveryMock(models.ModelMock):
    def do_GET(self):
        path = urlsplit(self.path)
        mode = getattr(self.server, 'result_mode', '')
        if path.path == '/api/v1/jobs/recordInfo' and mode in {'dns', 'upstream_failed', 'missing_result'}:
            task = parse_qs(path.query)['taskId'][0]
            job = self.server.jobs.get(task)
            if not job or self.identity() != job['owner']: return self.reply({}, 403)
            self.server.calls.append(('query', job['owner'], {'task_id': task}))
            if mode == 'upstream_failed':
                return self.reply({'code':200, 'data':{'state':'fail','failMsg':'fixture failure'}})
            if mode == 'missing_result':
                return self.reply({'code':200, 'data':{'state':'success'}})
            return self.reply({'code':200, 'data':{'state':'success', 'resultJson':json.dumps({'resultUrls':['https://198.18.1.32/result.png']})}})
        if path.path == '/media/result.png' and mode:
            self.server.calls.append(('result_failure', '', {'mode':mode}))
            if mode == 'timeout':
                time.sleep(2.5)
            elif mode == 'interrupted':
                self.send_response(200);self.send_header('Content-Length','1000');self.end_headers()
                self.wfile.write(b'partial');self.wfile.flush();self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            elif mode == 'redirect':
                return self.reply(b'',302,headers={'Location':'http://127.0.0.1:3000/private'})
        return super().do_GET()


class RecoveryTests(unittest.TestCase):
    for _name in ('start','stop','cleanup','request','ok','canvas','upload','image_payload'):
        locals()[_name] = models.ControlledModelTests.__dict__[_name]

    def setUp(self):
        replacement=patch.object(models,'ModelMock',RecoveryMock);replacement.start();self.addCleanup(replacement.stop)
        models.ControlledModelTests.setUp(self)

    def wait_job(self, task):
        until=time.monotonic()+8
        while time.monotonic()<until:
            value=self.ok('A','GET','/api/canvas-image-tasks/'+task)
            if value['status'] in {'failed','succeeded','result_recovery_required'} and not value['local_wait_active']: return value
            time.sleep(.03)
        self.fail('mock recovery deadline')

    def begin(self, mode='dns', provider_id='atelier-images'):
        self.mock.result_mode=mode
        canvas=self.canvas('A','recovery')
        self.ok('A','PUT','/api/canvases/'+canvas['id'],json={'nodes':[{'id':'original-node','type':'smart-image','generationHistory':[{'id':'original-attempt','status':'running'}]}]})
        self.binding={'canvas_id':canvas['id'],'node_id':'original-node','generation_id':'original-attempt'}
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(provider_id=provider_id, **self.binding))['task_id']
        return task,self.wait_job(task)

    def recover(self, task):
        before=sum(c[0]=='create' for c in self.mock.calls)
        self.mock.result_mode=''
        self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        result=self.wait_job(task)
        self.assertEqual(sum(c[0]=='create' for c in self.mock.calls),before)
        self.assertEqual(result['phase'],'completed_local');self.assertEqual(result['error_code'],'')
        self.assertEqual({p['task_id'] for k,_,p in self.mock.calls if k=='query'},set(self.mock.jobs))
        return result

    def test_success_without_recovery(self):
        _,r=self.begin('');self.assertEqual(r['phase'],'completed_local');self.assertEqual(r['upstream_status'],'success')

    def test_upstream_failure_is_distinct_from_local_recovery(self):
        _,r=self.begin('upstream_failed')
        self.assertEqual(r['phase'],'upstream_failed');self.assertEqual(r['status'],'failed')
        self.assertEqual(r['recovery'],'')

    def test_upstream_success_is_durable_before_result_parse(self):
        t,r=self.begin('missing_result');self.assertEqual(r['status'],'result_recovery_required')
        self.assertEqual(r['upstream_status'],'success');self.recover(t)

    def test_dns_reject_preserves_success_and_recovers_original(self):
        t,r=self.begin();self.assertEqual(r['status'],'result_recovery_required');self.assertEqual(r['upstream_status'],'success')
        self.assertEqual(r['local_result_status'],'pending');self.assertEqual(r['binding'],self.binding)
        self.assertNotIn(next(iter(self.mock.jobs)),json.dumps(r));self.assertNotIn(self.keys['A'],json.dumps(r))
        self.recover(t)

    def test_timeout_preserves_success(self):
        t,r=self.begin('timeout');self.assertEqual(r['status'],'result_recovery_required');self.assertEqual(r['error_code'],'timeout');self.recover(t)

    def test_interrupted_body_preserves_success(self):
        t,r=self.begin('interrupted');self.assertEqual(r['status'],'result_recovery_required');self.recover(t)

    def test_recovery_still_rejects_redirect(self):
        t,_=self.begin();self.mock.result_mode='redirect';before=len(self.mock.jobs)
        self.ok('A','POST','/api/canvas-image-tasks/'+t+'/refresh');r=self.wait_job(t)
        self.assertEqual(r['status'],'result_recovery_required');self.assertEqual(len(self.mock.jobs),before)
        self.recover(t)

    def test_process_restart_preserves_binding_and_recovery_state(self):
        t,_=self.begin();self.stop('A');self.start('A');r=self.wait_job(t)
        self.assertEqual(r['status'],'result_recovery_required');self.assertEqual(r['binding'],self.binding);self.recover(t)

    def test_restart_does_not_hide_an_unknown_later_batch_submission(self):
        t,_=self.begin();self.stop('A')
        with sqlite3.connect(self.roots['A']/'.auth/model-tasks.sqlite3') as db:
            job=json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(t,)).fetchone()[0])
            job.update(status='submitting',submission_uncertain=True,outstanding=True)
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(job),t))
        before=len(self.mock.calls);self.start('A');r=self.wait_job(t)
        self.assertEqual(r['status'],'failed');self.assertEqual(r['recovery'],'manual-reconcile')
        self.assertEqual(len(self.mock.calls),before)

    def test_double_click_and_completed_recovery_are_idempotent(self):
        t,_=self.begin();self.mock.result_mode=''
        with ThreadPoolExecutor(4) as pool:
            statuses=list(pool.map(lambda _:self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,range(4)))
        self.assertEqual(statuses,[200]*4);r=self.wait_job(t);self.assertEqual(r['phase'],'completed_local')
        before=len(self.mock.calls)
        for _ in range(3):self.ok('A','POST','/api/canvas-image-tasks/'+t+'/refresh')
        self.assertEqual(len(self.mock.calls),before);self.assertEqual(len(self.mock.jobs),1)
        history=self.ok('A','GET','/api/history');self.assertEqual(sum(x.get('task_id')==t for x in history),1)
        self.assertEqual(self.request('A','GET','/api/media-preview',params={'url':r['result']['images'][0]}).status_code,200)

    def test_b_cannot_recover_or_read_a_task(self):
        t,_=self.begin();before=len(self.mock.calls)
        self.assertEqual(self.request('B','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,404)
        self.assertEqual(self.request('B','GET','/api/canvas-image-tasks/'+t).status_code,404);self.assertEqual(len(self.mock.calls),before)

    def test_binding_override_body_rejected(self):
        t,_=self.begin();before=len(self.mock.calls)
        for values in ({'canvas_id':'other'},{'node_id':'other'},{'task_id':'other'},{'provider_id':'other'},{'owner':'B'}):
            self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh',json=values).status_code,400)
        self.assertEqual(len(self.mock.calls),before)

    def test_deleted_node_cannot_take_over_result(self):
        t,_=self.begin();self.ok('A','PUT','/api/canvases/'+self.binding['canvas_id'],json={'nodes':[]})
        before=len(self.mock.calls);self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,409);self.assertEqual(len(self.mock.calls),before)

    def test_invalid_initial_binding_rejected_before_create(self):
        for values in ({'canvas_id':'missing','node_id':'other'},{'node_id':'other'}):
            self.assertIn(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload(**values)).status_code,(400,404))
        self.assertFalse(self.mock.jobs)

    def test_history_write_failure_reuses_saved_result(self):
        path=self.roots['A']/'history.json'
        if path.exists(): path.unlink()
        path.mkdir()
        t,r=self.begin('');self.assertEqual(r['status'],'result_recovery_required')
        path.rmdir()
        result=self.recover(t)
        self.assertEqual(len(result['result']['images']),1)
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)

    def test_deleted_provider_recovery_fails_closed(self):
        t,_=self.begin();self.stop('A')
        path=self.roots['A']/'.auth/model-access.json';config=json.loads(path.read_text())
        config['providers']=config['providers'][:1];path.write_text(json.dumps(config));self.start('A')
        before=len(self.mock.calls)
        self.assertEqual(self.request('A','GET','/api/canvas-image-tasks/'+t).status_code,200)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,409)
        self.assertEqual(len(self.mock.calls),before)

    def test_rotated_credential_does_not_query_with_new_key(self):
        t,_=self.begin();self.stop('A')
        path=self.roots['A']/'.auth/credentials/kie.key';path.write_text('rotated-fixture-only');self.start('A')
        before=len(self.mock.calls)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,409)
        self.assertEqual(len(self.mock.calls),before)

    def test_disabled_personal_provider_preserves_task_but_denies_recovery(self):
        self.stop('A');AuthStore(self.roots['A'],'A').set_provider_permission('A',True);self.start('A')
        item={'id':'personal','name':'Fixture','protocol':'kie','base_url':self.mock.origin,'enabled':True,
              'models':[{'id':'gpt-image-2','purpose':'image'}]}
        self.ok('A','PUT','/api/instance/providers',json=item | {'api_key':self.keys['A']})
        t,r=self.begin(provider_id='personal');self.assertEqual(r['status'],'result_recovery_required')
        self.ok('A','PUT','/api/instance/providers',json=item | {'enabled':False})
        before=len(self.mock.calls)
        self.assertEqual(self.request('A','GET','/api/canvas-image-tasks/'+t).status_code,200)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,409)
        self.assertEqual(len(self.mock.calls),before)

    def test_recovery_logs_do_not_contain_private_inputs(self):
        t,_=self.begin();self.recover(t)
        log=(self.root/'A.log').read_text()
        forbidden=[self.keys['A'],self.passwords['A'],self.mock.origin,'garment','Authorization','Cookie',*self.mock.jobs]
        self.assertFalse(any(value in log for value in forbidden),'private recovery data in log')

    def test_logout_denies_recovery_and_media(self):
        t,_=self.begin();r=self.recover(t);self.ok('A','POST','/api/auth/logout')
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+t+'/refresh').status_code,401)
        self.assertEqual(self.request('A','GET',r['result']['images'][0]).status_code,401)


class QueryOnlyTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_transport_blocks_mutations_before_any_network_or_credentials(self):
        # Intentionally no policy, credentials or HTTP transport: rejection must come first.
        client=GuardedClient.__new__(GuardedClient);client.query_only=True
        for method in ('POST','PUT','PATCH','DELETE'):
            with self.assertRaises(HTTPException) as error:
                await client.request(method,'https://mock.invalid/api/v1/jobs/createTask')
            self.assertEqual(error.exception.status_code,403)
