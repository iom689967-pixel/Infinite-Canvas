"""Synthetic original tasks, authenticated isolated workers, loopback Kie only."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_instance_models as models
import test_instance_result_recovery as recovery
import test_public_beta_concurrency as concurrency


class DetachedMock(recovery.RecoveryMock):
    def do_GET(self):
        path=urlsplit(self.path)
        if path.path=='/api/v1/jobs/recordInfo':
            identity=parse_qs(path.query)['taskId'][0]
            job=self.server.jobs.get(identity)
            if not job or self.identity()!=job['owner']:return self.reply({},403)
            mode=getattr(self.server,'result_mode','')
            if mode in {'query401','query403','query404','query_timeout','running','unknown'}:
                self.server.calls.append(('query',job['owner'],{'task_id':identity}))
                if mode.startswith('query'):
                    if mode=='query_timeout':time.sleep(2.5);return self.reply({},504)
                    return self.reply({},int(mode[-3:]))
                return self.reply({'code':200,'data':{'state':'generating' if mode=='running' else 'unrecognized'}})
            if 'MULTI_RESULT' in json.dumps(job['payload']):
                self.server.calls.append(('query',job['owner'],{'task_id':identity}))
                urls=[self.server.origin+'/media/first.png',self.server.origin+'/media/second.png']
                if getattr(self.server,'short_results',False):urls=urls[:1]
                return self.reply({'code':200,'data':{'state':'success','resultJson':json.dumps({'resultUrls':urls})}})
        if path.path=='/media/second.png' and getattr(self.server,'partial_fail',False):
            self.server.calls.append(('download_failed','','second'))
            return self.reply({},503)
        if path.path=='/media/result.png' and getattr(self.server,'fail_second_result',False) and sum(c[0]=='media' for c in self.server.calls)==1:
            self.server.calls.append(('download_failed','','second-task'))
            return self.reply({},503)
        if path.path.startswith('/media/') and getattr(self.server,'result_mode','')=='invalid_media':
            self.server.calls.append(('download_failed','','invalid_media'))
            return self.reply(b'not an image',content_type='image/png')
        return super().do_GET()


class DetachedRecoveryTests(unittest.TestCase):
    for _name in ('start','stop','cleanup','request','ok','canvas','image_payload','wait_job','recover'):
        locals()[_name]=getattr(recovery.RecoveryTests,_name)

    def setUp(self):
        replacement=patch.object(models,'ModelMock',DetachedMock)
        replacement.start();self.addCleanup(replacement.stop)
        models.ControlledModelTests.setUp(self)

    def begin(self,mode='dns',prompt='garment',n=1):
        self.mock.result_mode=mode
        canvas=self.canvas('A','synthetic-recovery')
        self.ok('A','PUT','/api/canvases/'+canvas['id'],json={'nodes':[{'id':'original-node','type':'smart-image','generationHistory':[{'id':'original-attempt','status':'running'}]},{'id':'similar-node','type':'smart-image','generationHistory':[]}]})
        self.binding={'canvas_id':canvas['id'],'node_id':'original-node','generation_id':'original-attempt'}
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(prompt,n=n,**self.binding))['task_id']
        return task,self.wait_job(task)

    def job(self,task):
        with sqlite3.connect(self.roots['A']/'.auth/model-tasks.sqlite3') as db:
            return json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(task,)).fetchone()[0])

    def seed_flags(self,task,**changes):
        # Only malformed/unknown synthetic fixture state, never force a free slot.
        self.assertNotEqual(changes.get('outstanding'),False)
        with sqlite3.connect(self.roots['A']/'.auth/model-tasks.sqlite3') as db:
            job=json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(task,)).fetchone()[0])
            job.update(changes)
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(job),task))

    def remove_binding(self,kind='node'):
        url='/api/canvases/'+self.binding['canvas_id']
        if kind in {'canvas','purged'}:
            self.ok('A','DELETE',url+('/purge' if kind=='purged' else ''))
        else:
            nodes=self.ok('A','GET',url)['canvas']['nodes']
            if kind=='node':nodes=[n for n in nodes if n['id']!='original-node']
            else:nodes[0]['generationHistory']=[]
            self.ok('A','PUT',url,json={'nodes':nodes})

    def canvases(self):
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.roots['A']/'data/canvases').glob('*.json')}

    def media(self):return list((self.roots['A']/'assets/output').glob('*'))

    def assert_complete(self,task,result,binding='detached'):
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['binding_status'],binding)
        self.assertEqual(result['result']['binding_status'],binding)
        job=self.job(task);self.assertFalse(job['outstanding']);self.assertFalse(job['submission_uncertain'])
        self.assertEqual(job['local_result_status'],'completed');self.assertEqual(job['upstream_status'],'success');self.assertEqual(job['recovery'],'')
        self.assertTrue(all(e['remote_done'] and e['done'] for e in job['upstream']))
        history=self.ok('A','GET','/api/history')
        self.assertEqual(sum(h.get('task_id')==task for h in history),1)
        self.assertEqual(result['binding'],self.binding)
        self.assertTrue(all(p.stat().st_size>0 for p in self.media()))

    def check_detached(self,kind):
        task,_=self.begin();self.assertTrue(self.job(task)['outstanding']);self.remove_binding(kind)
        snapshot=self.canvases();queries=len([c for c in self.mock.calls if c[0]=='query'])
        result=self.recover(task);self.assert_complete(task,result)
        self.assertEqual(self.canvases(),snapshot)
        self.assertEqual(len([c for c in self.mock.calls if c[0]=='query']),queries+1)
        self.assertEqual(len(self.media()),1)
        self.assertIn('不会自动写入其他节点',result['recovery_message'])
        return task,result

    def test_attached_success_keeps_original_binding_and_canvas(self):
        task,_=self.begin();snapshot=self.canvases();result=self.recover(task)
        self.assert_complete(task,result,'attached');self.assertEqual(self.canvases(),snapshot)
        self.assertEqual(result['recovery_message'],'')

    def test_missing_node_detaches_without_using_similar_node(self):self.check_detached('node')
    def test_missing_generation_detaches(self):self.check_detached('generation')
    def test_deleted_canvas_detaches_without_restore(self):self.check_detached('canvas')
    def test_purged_canvas_detaches_without_recreation(self):self.check_detached('purged')

    def test_repeat_and_restart_complete_refresh_have_zero_extra_io(self):
        task,_=self.check_detached('node');before=list(self.mock.calls);files=self.media();snapshot=self.canvases()
        for _ in range(3):self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        self.stop('A');self.start('A');result=self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        self.assertFalse(result['local_wait_active']);self.assert_complete(task,result)
        self.assertEqual(self.mock.calls,before);self.assertEqual(self.media(),files);self.assertEqual(self.canvases(),snapshot)

    def test_submission_uncertain_rejected_without_query_or_release(self):
        task,_=self.begin();self.remove_binding();self.seed_flags(task,submission_uncertain=True)
        before=list(self.mock.calls)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,409)
        self.assertEqual(self.mock.calls,before);self.assertTrue(self.job(task)['outstanding']);self.assertEqual(self.media(),[])

    def test_missing_id_or_unregistered_batch_rejected_without_queries(self):
        task,_=self.begin();self.remove_binding();original=self.job(task);before=list(self.mock.calls)
        for changes in ({'upstream':[]},{'upstream':[dict(original['upstream'][0],id='')]},{'params':dict(original['params'],n=2)}):
            with self.subTest(changes=list(changes)):
                self.seed_flags(task,**{k:original[k] for k in ('params','upstream')})
                self.seed_flags(task,**changes)
                self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,409)
                self.assertEqual(self.mock.calls,before);self.assertTrue(self.job(task)['outstanding'])

    def test_running_unknown_and_failed_query_keep_occupancy(self):
        task,_=self.begin();self.remove_binding();snapshot=self.canvases()
        for mode in ('running','unknown','query401','query403','query404','query_timeout'):
            with self.subTest(mode=mode):
                self.mock.result_mode=mode
                self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh');result=self.wait_job(task)
                self.assertNotEqual(result['status'],'succeeded');self.assertTrue(self.job(task)['outstanding'])
                self.assertEqual(self.ok('A','GET','/api/history'),[]);self.assertEqual(self.media(),[]);self.assertEqual(self.canvases(),snapshot)
        self.assertEqual(sum(c[0]=='create' for c in self.mock.calls),1)

    def test_rotated_provider_revision_rejects_detached_query(self):
        task,_=self.begin();self.remove_binding();self.stop('A')
        path=self.roots['A']/'.auth/credentials/kie.key';path.write_text('new-synthetic-key')
        self.start('A');before=list(self.mock.calls)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,409)
        self.assertTrue(self.job(task)['outstanding']);self.assertEqual(self.mock.calls,before)

    def test_b_cannot_recover_or_read_detached_a_result(self):
        task,_=self.begin();self.remove_binding();before=list(self.mock.calls)
        self.assertEqual(self.request('B','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,404)
        self.assertEqual(self.mock.calls,before);result=self.recover(task)
        self.assertEqual(self.request('B','GET',result['result']['images'][0]).status_code,404)
        self.assertEqual(self.ok('B','GET','/api/history'),[])

    def test_ledger_owner_mismatch_is_not_detached_authorization(self):
        task,_=self.begin();self.remove_binding();self.seed_flags(task,owner='not-current-subject');before=list(self.mock.calls)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,404)
        self.assertTrue(self.job(task)['outstanding']);self.assertEqual(self.mock.calls,before)

    def test_invalid_download_retains_outstanding_through_restart(self):
        task,_=self.begin();self.remove_binding();self.mock.result_mode='invalid_media'
        self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh');result=self.wait_job(task)
        self.assertEqual(result['status'],'result_recovery_required');self.assertTrue(self.job(task)['outstanding'])
        self.stop('A');self.start('A');self.assertTrue(self.job(task)['outstanding']);self.assertEqual(self.media(),[])
        self.assert_complete(task,self.recover(task))

    def test_partial_download_only_retries_missing_original_result(self):
        self.mock.partial_fail=True
        task,result=self.begin('',prompt='MULTI_RESULT');self.remove_binding();snapshot=self.canvases()
        self.assertEqual(result['status'],'result_recovery_required');self.assertTrue(self.job(task)['outstanding'])
        self.assertEqual(len(self.job(task)['upstream'][0]['local']),1);files=self.media();self.assertEqual(len(files),1)
        self.stop('A');self.start('A');self.assertTrue(self.job(task)['outstanding'])
        self.mock.partial_fail=False;result=self.recover(task);self.assert_complete(task,result)
        self.assertEqual(len(self.media()),2);self.assertTrue(files[0].exists());self.assertEqual(self.canvases(),snapshot)
        self.assertEqual(sum(c[0]=='media' for c in self.mock.calls),2)

    def test_all_registered_subtasks_must_finish_before_release(self):
        self.mock.fail_second_result=True
        task,result=self.begin('',n=2);self.remove_binding();job=self.job(task)
        self.assertEqual(len(job['upstream']),2);self.assertTrue(job['upstream'][0]['done']);self.assertFalse(job['upstream'][1]['done'])
        self.assertTrue(job['outstanding']);self.assertEqual(len(self.media()),1)
        queries=[c[2]['task_id'] for c in self.mock.calls if c[0]=='query']
        self.mock.fail_second_result=False;result=self.recover(task);self.assert_complete(task,result)
        self.assertEqual([c[2]['task_id'] for c in self.mock.calls if c[0]=='query'][len(queries):],[job['upstream'][1]['id']])
        self.assertEqual(len(self.media()),2);self.assertEqual(sum(c[0]=='create' for c in self.mock.calls),2)

    def test_shortened_upstream_result_list_cannot_release_partial_download(self):
        self.mock.partial_fail=True
        task,_=self.begin('',prompt='MULTI_RESULT');self.remove_binding();self.mock.short_results=True
        self.mock.result_mode='';self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh');self.wait_job(task)
        self.assertTrue(self.job(task)['outstanding']);self.assertEqual(len(self.media()),1)
        self.assertEqual(self.job(task)['upstream'][0]['result_count'],2)

    def test_history_failure_keeps_reservation_and_reuses_local_file(self):
        task,_=self.begin();self.remove_binding();path=self.roots['A']/'history.json'
        if path.exists():path.unlink()
        path.mkdir()
        self.mock.result_mode='';self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh');self.wait_job(task)
        self.assertTrue(self.job(task)['outstanding']);files=self.media();self.assertEqual(len(files),1)
        self.assertEqual(self.ok('A','GET','/api/canvas-image-tasks/'+task)['recovery_message'],'')
        self.stop('A');self.start('A');self.assertTrue(self.job(task)['outstanding']);before=list(self.mock.calls)
        path.rmdir();result=self.recover(task);self.assert_complete(task,result)
        self.assertEqual(self.media(),files);self.assertEqual(self.mock.calls,before)

    def test_cancelled_generation_keeps_terminal_state_while_history_completes(self):
        task,_=self.begin();url='/api/canvases/'+self.binding['canvas_id'];nodes=self.ok('A','GET',url)['canvas']['nodes']
        nodes[0]['generationHistory'][0]['status']='cancelled';self.ok('A','PUT',url,json={'nodes':nodes});snapshot=self.canvases()
        self.assert_complete(task,self.recover(task),'attached');self.assertEqual(self.canvases(),snapshot)
        self.assertEqual(self.ok('A','GET',url)['canvas']['nodes'][0]['generationHistory'][0]['status'],'cancelled')

    def test_detached_reservation_recovery_reopens_tenth_slot(self):
        self.stop('A');path=self.roots['A']/'.auth/model-access.json';config=json.loads(path.read_text())
        config['max_concurrent']=10;path.write_text(json.dumps(config));self.start('A')
        task,_=self.begin();self.remove_binding();self.mock.result_mode=''
        jobs=[self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('WAIT_IMAGE '+str(i)))['task_id'] for i in range(9)]
        self.assertEqual(sum(self.job(i)['outstanding'] for i in [task,*jobs]),10)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload('eleventh')).status_code,429)
        result=self.recover(task);self.assert_complete(task,result)
        self.assertEqual(sum(self.job(i)['outstanding'] for i in [task,*jobs]),9)
        replacement=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('WAIT_IMAGE replacement'))['task_id']
        self.assertEqual(sum(self.job(i)['outstanding'] for i in [task,*jobs,replacement]),10)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload('still-full')).status_code,429)
        # The freed slot is admitted through normal create, never a forced ledger release.
        self.assertFalse(self.job(task)['outstanding']);self.assertTrue(self.job(replacement)['outstanding'])


class BetaKieMock(concurrency.HeldMock):
    def do_POST(self):
        if self.path!='/api/v1/jobs/createTask':return self.reject('recovery must never upload or use another route')
        owner=self.identity()
        if not owner:return self.reply({},401)
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        identity='synthetic-original-'+str(len(self.server.jobs))
        self.server.jobs[identity]=dict(owner=owner,original=body['input']['prompt']=='original-result')
        self.server.calls.append(('create',owner,identity))
        return self.reply({'code':200,'data':{'taskId':identity}})

    def do_GET(self):
        path=urlsplit(self.path)
        if path.path=='/api/v1/jobs/recordInfo':
            identity=parse_qs(path.query)['taskId'][0];job=self.server.jobs.get(identity)
            if not job or self.identity()!=job['owner']:return self.reply({},403)
            self.server.calls.append(('query',job['owner'],identity))
            data={'state':'generating'}
            if job['original']:data={'state':'success','resultJson':json.dumps({'resultUrls':[self.server.origin+'/media/original.png']})}
            return self.reply({'code':200,'data':data})
        if path.path=='/media/original.png':
            if not self.server.recover_results:
                self.server.calls.append(('download_failed','',''))
                return self.reply({},503)
            self.server.calls.append(('download','',''))
            return self.reply(self.server.image,content_type='image/png')
        return self.reject('unexpected recovery GET')


class BetaDetachedRecoveryAcceptance(unittest.IsolatedAsyncioTestCase):
    for _name in ('asyncSetUp','asyncTearDown','release','enter','account','effective','until'):
        locals()[_name]=getattr(concurrency.TenSlotAcceptance,_name)

    async def test_public_beta_ten_slots_detached_recovery_and_new_tenth_task(self):
        self.mock.RequestHandlerClass=BetaKieMock;self.mock.recover_results=False
        root=await self.account('A');c=self.clients['A']
        settings=[dict(id='mock-kie',name='Synthetic Kie',protocol='kie',enabled=True,base_url=self.mock.origin,
                       api_key='synthetic-key-A',image_models=['gpt-image-2'])]
        r=await c.put('/api/instance/provider-settings',json=settings);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(await self.effective('A'),10)
        for file,field in (('model-access.json','max_concurrent'),('public-beta.json','max_concurrent_generations')):
            self.assertEqual(json.loads((root/'.auth'/file).read_text())[field],10)
        r=await c.post('/api/canvases',json={'name':'synthetic-recovery','kind':'smart'});self.assertEqual(r.status_code,200,r.text)
        canvas=r.json()['canvas'];url='/api/canvases/'+canvas['id']
        r=await c.put(url,json={'nodes':[{'id':'original','type':'smart-image','generationHistory':[{'id':'generation','status':'running'}]}]});self.assertEqual(r.status_code,200)
        payload=dict(provider_id='mock-kie',model='gpt-image-2',prompt='original-result',n=1,size='1024x1024',aspect_ratio='1:1',resolution='1K',request_id='original-fixture',canvas_id=canvas['id'],node_id='original',generation_id='generation')
        r=await c.post('/api/canvas-image-tasks',json=payload);self.assertEqual(r.status_code,200,r.text);original=r.json()['task_id']
        async def wait_original(status):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                value=(await c.get('/api/canvas-image-tasks/'+original)).json()
                if value['status']==status and not value['local_wait_active']:return value
                await asyncio.sleep(.025)
            self.fail('bounded public-beta recovery condition not reached')
        await wait_original('result_recovery_required')
        r=await c.put(url,json={'nodes':[]});self.assertEqual(r.status_code,200)
        canvas_file=root/'data/canvases'/(canvas['id']+'.json');before_canvas=canvas_file.read_bytes()
        def occupancy():
            with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
                return sum(json.loads(row[0])['outstanding'] for row in db.execute('SELECT data FROM jobs'))
        for i in range(9):
            body={k:v for k,v in payload.items() if k not in {'canvas_id','node_id','generation_id'}}
            body.update(prompt='held-'+str(i),request_id='held-'+str(i))
            r=await c.post('/api/canvas-image-tasks',json=body);self.assertEqual(r.status_code,200,r.text)
        await self.until(lambda:len(self.mock.jobs)==10)
        self.assertEqual(occupancy(),10)
        body.update(prompt='over-limit',request_id='over-limit')
        r=await c.post('/api/canvas-image-tasks',json=body);self.assertEqual(r.status_code,429)
        before_posts=sum(call[0]=='create' for call in self.mock.calls)
        self.mock.recover_results=True
        r=await c.post('/api/canvas-image-tasks/'+original+'/refresh',json={});self.assertEqual(r.status_code,200)
        result=await wait_original('succeeded');self.assertEqual(result['binding_status'],'detached')
        self.assertEqual(occupancy(),9);self.assertEqual(sum(call[0]=='create' for call in self.mock.calls),before_posts)
        body.update(prompt='new-tenth',request_id='new-tenth')
        r=await c.post('/api/canvas-image-tasks',json=body);self.assertEqual(r.status_code,200,r.text)
        await self.until(lambda:len(self.mock.jobs)==11);self.assertEqual(occupancy(),10)
        body.update(prompt='still-full',request_id='still-full')
        r=await c.post('/api/canvas-image-tasks',json=body);self.assertEqual(r.status_code,429)
        self.assertEqual(sum(call[0]=='create' for call in self.mock.calls),before_posts+1)
        self.assertEqual(sum(call[0]=='download' for call in self.mock.calls),1)
        before_queries=sum(call[0]=='query' and call[2]=='synthetic-original-0' for call in self.mock.calls)
        r=await c.post('/api/canvas-image-tasks/'+original+'/refresh',json={});self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['local_wait_active'])
        self.assertEqual(sum(call[0]=='query' and call[2]=='synthetic-original-0' for call in self.mock.calls),before_queries)
        history=(await c.get('/api/history')).json();self.assertEqual(sum(h.get('task_id')==original for h in history),1)
        self.assertEqual(canvas_file.read_bytes(),before_canvas)
        self.assertEqual(len(list((root/'assets/output').glob('*'))),1)
