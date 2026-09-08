"""Two authenticated processes, private fake credentials, real local HTTP adapters only."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image

from instance_model_policy import GuardedClient, ModelPolicy
from test_instance_isolation import ROOT, clean_env, free_port, instance_env
import test_instance_isolation as isolation


def configuration(port):
    endpoint = f'http://127.0.0.1:{port}'
    return {'schema_version':1, 'mode':'mock', 'max_concurrent':2, 'providers':[
        {'id':'atelier-text', 'protocol':'gemini', 'base_url':endpoint+'/v1beta', 'credential_file':'credentials/gemini.key',
         'models':{'gemini-mock-vision':{'max_references':2,'max_reference_bytes':1048576,'timeout_seconds':1,
                                       'max_output_tokens':64,'max_text_chars':1000}}},
        {'id':'atelier-images', 'protocol':'kie', 'base_url':endpoint, 'upload_base_url':endpoint,
         'media_origins':[endpoint], 'credential_file':'credentials/kie.key',
         'models':{'gpt-image-2':{'max_references':2,'max_reference_bytes':1048576,'timeout_seconds':2,
                                 'max_images':2,'sizes':['1024x1024'],'resolutions':['1K'],
                                 'aspect_ratios':['1:1'],'output_formats':['']}}}]}


class ModelMock(BaseHTTPRequestHandler):
    def reply(self, payload, status=200, content_type='application/json', headers=None):
        data = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Set-Cookie','upstream-cookie=must-not-forward; Path=/')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def identity(self):
        value = self.headers.get('x-goog-api-key') or self.headers.get('Authorization','').removeprefix('Bearer ')
        return self.server.credentials.get(hashlib.sha256(value.encode()).hexdigest(), '')

    def do_POST(self):
        owner = self.identity()
        data = self.rfile.read(int(self.headers.get('Content-Length',0)))
        if not owner:
            return self.reply({'message':'invalid'},401)
        if ':generateContent' in self.path:
            payload = json.loads(data)
            self.server.calls.append(('llm',owner,payload))
            text = json.dumps(payload)
            if 'WAIT_CANCEL' in text or 'TIMEOUT' in text:
                time.sleep(2)
            if 'HTTP_ERROR' in text:
                return self.reply({'error':self.headers['x-goog-api-key']+' private upstream'},503)
            if 'TRICKLE' in text:
                body=b'{"candidates":[{"content":{"parts":[{"text":"slow chunks"}]}}]}'
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
                for index in range(0,len(body),5):
                    try:self.wfile.write(body[index:index+5]);self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError):break
                    time.sleep(.15)
                return
            return self.reply({'candidates':[{'content':{'parts':[{'text':'MOCK garment reference understood'}]}}]})
        if self.path == '/api/file-stream-upload':
            name = secrets.token_hex(8)+'.png'
            self.server.media[name] = (owner,self.server.image)
            self.server.calls.append(('upload',owner,{'contains_image':self.server.image in data,
                                                      'cookie_present':bool(self.headers.get('Cookie'))}))
            return self.reply({'code':200,'data':{'downloadUrl':self.server.origin+'/media/'+name}})
        if self.path == '/api/v1/jobs/createTask':
            payload = json.loads(data)
            task_id = secrets.token_hex(10)
            self.server.jobs[task_id] = {'owner':owner,'payload':payload,'queries':0}
            self.server.calls.append(('create',owner,payload))
            text = json.dumps(payload)
            same = sum(k == 'create' and p == payload for k, _, p in self.server.calls)
            if 'SUBMIT_DROP' in text or ('SECOND_DROP' in text and same == 2):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            return self.reply({'code':200,'data':{'taskId':task_id}})
        self.reply({},404)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == '/api/v1/jobs/recordInfo':
            task_id = parse_qs(path.query).get('taskId',[''])[0]
            job = self.server.jobs.get(task_id)
            if not job or job['owner'] != self.identity():
                return self.reply({},403)
            job['queries'] += 1
            self.server.calls.append(('query',job['owner'],{'task_id':task_id}))
            text = json.dumps(job['payload'])
            if 'POLL_DROP' in text and job['queries'] == 1:
                self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
            if 'WAIT_IMAGE' in text and not self.server.finish_waiting:
                return self.reply({'code':200,'data':{'state':'generating'}})
            result = self.server.origin+'/media/result.png'
            if 'EVIL_RESULT' in text:
                result = 'http://127.0.0.1:3000/assets/private.png'
            if 'REDIRECT_RESULT' in text:
                result = self.server.origin+'/media/redirect.png'
            return self.reply({'code':200,'data':{'state':'success','resultJson':json.dumps({'resultUrls':[result]})}})
        if path.path == '/media/redirect.png':
            return self.reply(b'',302,headers={'Location':'http://127.0.0.1:3000/'})
        if path.path.startswith('/media/'):
            self.server.calls.append(('media','',{'cookie':bool(self.headers.get('Cookie')),
                                                   'authorization':bool(self.headers.get('Authorization'))}))
            return self.reply(self.server.image,content_type='image/png')
        self.reply({},404)

    def log_message(self, *_args):
        pass


class ControlledModelTests(unittest.TestCase):
    stop = isolation.TwoProcessIsolationTests.stop.__func__
    request = isolation.TwoProcessIsolationTests.request.__func__
    ok = isolation.TwoProcessIsolationTests.ok
    canvas = isolation.TwoProcessIsolationTests.canvas

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='controlled-model-test-')
        self.root = Path(self.temp.name).resolve()
        self.roots = {n:self.root/n for n in ('A','B')}
        self.ports = {n:free_port() for n in ('A','B')}
        while self.ports['A'] == self.ports['B']:
            self.ports['B'] = free_port()
        self.cookies,self.csrf,self.procs,self.logs = {},{},{},{}
        self.passwords = {n:secrets.token_urlsafe(24) for n in ('A','B')}
        self.keys = {n:secrets.token_urlsafe(32) for n in ('A','B')}
        self.mock = ThreadingHTTPServer(('127.0.0.1',0),ModelMock)
        self.mock.calls,self.mock.jobs,self.mock.media = [],{},{}
        self.mock.credentials = {hashlib.sha256(v.encode()).hexdigest():n for n,v in self.keys.items()}
        image = BytesIO(); Image.new('RGB',(48,64),'blue').save(image,'PNG'); self.mock.image=image.getvalue()
        self.mock.origin=f'http://127.0.0.1:{self.mock.server_port}';self.mock.finish_waiting=False
        self.thread=threading.Thread(target=self.mock.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)
        for n in ('A','B'):
            args=[sys.executable,str(ROOT/'instance_admin.py'),'--data-root',str(self.roots[n]),'--instance-id',n]
            subprocess.run([*args,'init'],env=clean_env(),capture_output=True,check=True)
            subprocess.run([*args,'create','--username',n,'--password-stdin'],input=self.passwords[n].encode(),
                           env=clean_env(),capture_output=True,check=True)
            private=self.roots[n]/'.auth'
            (private/'credentials').mkdir(mode=0o700)
            for filename in ('gemini.key','kie.key'):
                path=private/'credentials'/filename;path.write_text(self.keys[n]);path.chmod(0o600)
            config=private/'model-access.json';config.write_text(json.dumps(configuration(self.mock.server_port)));config.chmod(0o600)
            self.start(n)

    def start(self,name):
        env=instance_env(self.roots[name],name,self.ports[name])
        env['INSTANCE_MOCK_UPSTREAMS']=f'127.0.0.1:{self.mock.server_port}'
        log=open(self.root/f'{name}.log','a+');self.logs[name]=log
        code="import main,uvicorn; uvicorn.run(main.app,host=main.INSTANCE_HOST,port=main.INSTANCE_PORT,log_level='warning',proxy_headers=False,ws='websockets-sansio')"
        self.procs[name]=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=log,stderr=log)
        deadline=time.monotonic()+12
        origin=f'http://127.0.0.1:{self.ports[name]}'
        while time.monotonic()<deadline:
            if self.procs[name].poll() is not None:
                raise AssertionError('Temporary instance startup failed: '+(self.root/f'{name}.log').read_text())
            try:
                with httpx.Client(trust_env=False,timeout=5) as client:
                    if client.get(origin+'/healthz').status_code==200:
                        response=client.post(origin+'/api/auth/login',headers={'Origin':origin},json={'username':name,'password':self.passwords[name]})
                        self.assertEqual(response.status_code,200)
                        self.cookies[name]=dict(client.cookies);self.csrf[name]=client.get(origin+'/api/auth/me').json()['csrf'];return
            except httpx.TransportError:
                pass
            time.sleep(.03)
        raise AssertionError('Temporary startup deadline')

    def cleanup(self):
        for n in ('A','B'):
            self.stop(n)
        self.mock.shutdown();self.mock.server_close();self.thread.join(timeout=3);self.temp.cleanup()

    def upload(self,name='A'):
        body=self.ok(name,'POST','/api/ai/upload',files={'files':('reference.png',self.mock.image,'image/png')})
        return body['files'][0]['url']

    def image_payload(self,prompt='garment',**kwargs):
        return {'provider_id':'atelier-images','model':'gpt-image-2','prompt':prompt,'size':'1024x1024',
                'aspect_ratio':'1:1','resolution':'1K','n':1,**kwargs}

    def wait_job(self,task,name='A'):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            data=self.ok(name,'GET','/api/canvas-image-tasks/'+task)
            if data['status'] in {'failed','canceled','succeeded'} and not data['local_wait_active']:
                return data
            time.sleep(.05)
        self.fail('Temporary model task deadline')

    def test_reference_llm_and_kie_complete_chain_is_private(self):
        ref=self.upload()
        text=self.ok('A','POST','/api/canvas-llm',json={'provider':'atelier-text','model':'gemini-mock-vision','message':'describe garment','images':[ref]})
        self.assertIn('garment',text['text'])
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(text['text'],reference_images=[{'url':ref,'name':'reference.png'}]))['task_id']
        result=self.wait_job(task);self.assertEqual(result['status'],'succeeded',result)
        url=result['result']['images'][0]
        self.assertEqual(self.request('A','GET',url).content,self.request('A','GET','/api/download-output',params={'url':url}).content)
        self.assertEqual(self.request('A','GET','/api/media-preview',params={'url':url}).status_code,200)
        canvas=self.canvas('A','model workflow')
        self.ok('A','PUT','/api/canvases/'+canvas['id'],json={'nodes':[{'id':'result','type':'image','images':[{'url':url}]}]})
        self.assertTrue(list((self.roots['A']/'.auth/reference-cache').glob('*.json')))
        self.assertTrue(list((self.roots['A']/'data/media_previews').glob('*')))
        self.assertFalse(list((self.roots['B']/'.auth/reference-cache').glob('*')))
        self.assertIn(url,json.dumps(self.ok('A','GET','/api/history')))
        self.assertNotIn(url,json.dumps(self.ok('B','GET','/api/history')))
        llm=next(p for kind,owner,p in self.mock.calls if kind=='llm')
        self.assertIn('inlineData',json.dumps(llm));self.assertEqual(llm['generationConfig']['maxOutputTokens'],64)
        self.assertTrue(any(kind=='upload' for kind,_,_ in self.mock.calls))
        self.assertFalse(any(p.get('cookie') or p.get('authorization') for kind,_,p in self.mock.calls if kind=='media'))

    def test_model_kind_identity_and_parameter_rejection(self):
        for changes in ({'provider_id':'kie'},{'model':'not-approved'},{'n':3},{'n':0},{'resolution':'4K'},
                        {'size':'4096x4096'},{'quality':'high'},{'operation':'upscale'},{'aspect_ratio':'16:9'},
                        {'resolution':'8K'}):
            self.assertIn(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload(**changes)).status_code,(400,403))
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload(prompt='')).status_code,422)
        self.assertEqual(self.request('A','POST','/api/canvas-llm',json={'provider':'atelier-images','model':'gpt-image-2','message':'wrong kind'}).status_code,403)
        payload=self.image_payload(base_url='http://127.0.0.1:3000',headers={'Authorization':'forged'},user_id='B',instance_id='B',role='admin')
        task=self.ok('A','POST','/api/canvas-image-tasks',json=payload,headers={'Authorization':'forged','X-User-ID':'B'})['task_id']
        self.assertEqual(self.wait_job(task)['status'],'succeeded')
        self.assertEqual([owner for kind,owner,_ in self.mock.calls if kind=='create'],['A'])

    def test_filtered_capabilities_and_batch_limit(self):
        schema=self.ok('A','GET','/api/image-params',params={'provider_id':'atelier-images','model':'gpt-image-2'})
        self.assertEqual(schema['provider_id'],'atelier-images')
        self.assertEqual(schema['reference_image_limit'],2)
        resolution=next(f for f in schema['fields'] if f['key']=='resolution')
        self.assertEqual([o['value'] for o in resolution['options']],['1K'])
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(n=2))['task_id']
        result=self.wait_job(task)
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(len(result['result']['images']),2)
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),2)

    def test_corrupt_reference_rejected_without_internal_traceback(self):
        image=bytearray(self.mock.image)
        offset=image.index(b'IDAT')
        length=int.from_bytes(image[offset-4:offset],'big')
        image[offset+4+length]^=1
        body=self.ok('A','POST','/api/ai/upload',files={'files':('corrupt.png',bytes(image),'image/png')})
        response=self.request('A','POST','/api/canvas-llm',json={'provider':'atelier-text','model':'gemini-mock-vision',
                              'message':'check invalid image','images':[body['files'][0]['url']]})
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json()['detail']['code'],'reference')
        self.assertNotIn('Traceback',(self.root/'A.log').read_text())

    def test_partial_batch_unknown_submit_retains_uncertainty_after_query(self):
        payload=self.image_payload('SECOND_DROP',n=2,request_id='partial-batch')
        task=self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id']
        result=self.wait_job(task)
        self.assertEqual(result['recovery'],'manual-reconcile')
        self.stop('A');self.start('A')
        self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        result=self.wait_job(task)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['recovery'],'manual-reconcile')
        self.assertEqual(len(result['result']['images']),1)
        self.assertEqual(self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id'],task)
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),2)

    def test_other_instance_cannot_read_reference_tasks_or_results(self):
        ref=self.upload()
        before=len(self.mock.calls)
        for value in (ref,str(self.roots['A']/'assets/input/private.png'),'file:///etc/passwd',f'http://127.0.0.1:{self.ports["A"]}{ref}'):
            response=self.request('B','POST','/api/canvas-image-tasks',json=self.image_payload(reference_images=[{'url':value}]))
            self.assertIn(response.status_code,(403,404))
        self.assertEqual(len(self.mock.calls),before)
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload())['task_id']
        result=self.wait_job(task)
        for method,suffix in (('GET',''),('DELETE',''),('POST','/refresh')):
            self.assertEqual(self.request('B',method,'/api/canvas-image-tasks/'+task+suffix).status_code,404)
        upstream=next(iter(self.mock.jobs))
        self.assertEqual(self.request('B','GET','/api/canvas-image-tasks/'+upstream).status_code,404)
        self.assertEqual(self.request('B','GET',result['result']['images'][0]).status_code,404)
        for method,path,payload in (('POST','/api/canvas-image-tasks',self.image_payload()),
                ('POST','/api/canvas-image-tasks/'+task+'/refresh',{}),
                ('DELETE','/api/canvas-image-tasks/'+task,None)):
            for headers in ({'X-CSRF-Token':''},{'Origin':f'http://127.0.0.1:{self.ports["B"]}'}):
                self.assertEqual(self.request('A',method,path,json=payload,headers=headers).status_code,403)

    def test_repeated_submissions_and_restart_do_not_create_twice(self):
        payload=self.image_payload(request_id='same-request')
        with ThreadPoolExecutor(3) as pool:
            tasks=list(pool.map(lambda _:self.request('A','POST','/api/canvas-image-tasks',json=payload).json()['task_id'],range(3)))
        self.assertEqual(len(set(tasks)),1)
        self.assertEqual(self.wait_job(tasks[0])['status'],'succeeded')
        self.stop('A');self.start('A')
        self.assertEqual(self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id'],tasks[0])
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json={**payload,'prompt':'changed'}).status_code,409)

    def test_poll_failure_refresh_queries_original_task_only(self):
        payload=self.image_payload('POLL_DROP')
        task=self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id']
        failed=self.wait_job(task);self.assertEqual(failed['status'],'failed');self.assertEqual(failed['recovery'],'query-existing')
        self.assertEqual(self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id'],task)
        self.stop('A');self.start('A')
        self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        self.assertEqual(self.wait_job(task)['status'],'succeeded')
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)

    def test_unknown_submit_is_not_retried_even_after_restart(self):
        payload=self.image_payload('SUBMIT_DROP',request_id='unknown')
        task=self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id']
        failed=self.wait_job(task);self.assertEqual(failed['status'],'failed');self.assertEqual(failed['recovery'],'manual-reconcile')
        self.stop('A');self.start('A')
        self.assertEqual(self.ok('A','POST','/api/canvas-image-tasks',json=payload)['task_id'],task)
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)

    def test_local_cancel_does_not_claim_cloud_cancellation_or_release_cloud_slot(self):
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('WAIT_IMAGE'))['task_id']
        deadline=time.monotonic()+3
        while not self.mock.jobs and time.monotonic()<deadline:time.sleep(.03)
        stopped=self.ok('A','DELETE','/api/canvas-image-tasks/'+task)
        self.assertFalse(stopped['upstream_cancel_supported']);self.assertEqual(stopped['cancel_scope'],'local-only')
        self.wait_job(task)
        other=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('WAIT_IMAGE two'))['task_id']
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload('third')).status_code,429)
        self.mock.finish_waiting=True
        self.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        self.assertEqual(self.wait_job(task)['status'],'succeeded')
        self.assertEqual(self.wait_job(other)['status'],'succeeded')

    def test_result_ssrf_and_redirects_fail_before_owner_access(self):
        for prompt in ('EVIL_RESULT','REDIRECT_RESULT'):
            task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(prompt))['task_id']
            result=self.wait_job(task);self.assertEqual(result['status'],'failed');self.assertEqual(result['error_code'],'download')
            self.assertNotIn('3000',json.dumps(result))

    def test_credentials_never_appear_in_catalog_errors_canvas_exports_or_logs(self):
        for endpoint in ('/api/providers','/api/config','/api/models'):
            raw=self.request('A','GET',endpoint).text
            self.assertFalse(any(key in raw for key in self.keys.values()),'credential leak')
            self.assertNotIn('base_url',raw);self.assertNotIn('credential_file',raw)
        response=self.request('A','POST','/api/canvas-llm',json={'provider':'atelier-text','model':'gemini-mock-vision','message':'HTTP_ERROR'})
        self.assertEqual(response.status_code,502);self.assertEqual(response.json()['detail']['code'],'upstream')
        self.assertFalse(any(key in response.text for key in self.keys.values()),'credential leak')
        self.assertEqual(self.request('A','GET','/api/config/token').status_code,403)
        for endpoint in ('/api/local-assets/caption','/api/local-assets/classify','/api/asset-library/items/classify'):
            self.assertEqual(self.request('A','POST',endpoint,json={}).status_code,403)
        canvas=self.canvas('A','safe export')
        exported=self.request('A','GET','/api/canvases/'+canvas['id']).text
        self.assertFalse(any(key in exported for key in self.keys.values()))
        ref=self.upload()
        archive=self.request('A','POST','/api/canvas-workflows/export',json={'nodes':[
            {'id':'image','type':'image','images':[{'url':ref}]},
            {'id':'prompt','type':'smart-prompt','llmProvider':'atelier-text','llmModel':'gemini-mock-vision'}]})
        self.assertEqual(archive.status_code,200)
        with zipfile.ZipFile(BytesIO(archive.content)) as zip_file:
            for name in zip_file.namelist():
                self.assertNotIn('.auth',name)
                content=zip_file.read(name)
                self.assertFalse(any(key.encode() in content for key in self.keys.values()),'export credential leak')
        for path in ('/assets/../.auth/model-access.json','/output/../.auth/credentials/kie.key',
                     '/api/storage-files/.auth/model-tasks.sqlite3'):
            self.assertIn(self.request('A','GET',path).status_code,(403,404))
        log=(self.root/'A.log').read_text()
        self.assertFalse(any(key in log for key in self.keys.values()),'credential log leak')
        self.assertNotIn(self.mock.origin,log)

    def test_credential_rotation_uses_private_admin_input_and_new_cache_namespace(self):
        reference=self.upload()
        self.wait_job(self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('before',reference_images=[{'url':reference}]))['task_id'])
        replacement=secrets.token_urlsafe(32)
        self.mock.credentials[hashlib.sha256(replacement.encode()).hexdigest()]='A'
        completed=subprocess.run([sys.executable,str(ROOT/'instance_admin.py'),'--data-root',str(self.roots['A']),
            '--instance-id','A','set-model-credential','--credential-name','kie.key','--credential-stdin'],
            input=replacement.encode(),env=clean_env(),capture_output=True,check=True)
        self.assertNotIn(replacement.encode(),completed.stdout+completed.stderr)
        self.assertEqual((self.roots['A']/'.auth/credentials/kie.key').stat().st_mode&0o777,0o600)
        self.wait_job(self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload('after',reference_images=[{'url':reference}]))['task_id'])
        self.assertEqual(sum(k=='upload' for k,_,_ in self.mock.calls),2)
        self.assertEqual(len(list((self.roots['A']/'.auth/reference-cache').glob('*.json'))),2)

    def test_llm_timeout_cancel_rerun_and_logged_out_rejection(self):
        ref=self.upload()
        payload={'provider':'atelier-text','model':'gemini-mock-vision','message':'TIMEOUT'}
        self.assertEqual(self.request('A','POST','/api/canvas-llm',json=payload).status_code,504)
        payload.update(message='WAIT_CANCEL',request_id='run-first')
        with ThreadPoolExecutor(1) as pool:
            waiting=pool.submit(self.request,'A','POST','/api/canvas-llm',json=payload)
            deadline=time.monotonic()+2
            while sum(k=='llm' for k,_,_ in self.mock.calls)<2 and time.monotonic()<deadline:time.sleep(.02)
            stopped=self.ok('A','POST','/api/canvas-llm/cancel',json={'request_id':'run-first'})
            self.assertFalse(stopped['upstream_cancel_confirmed']);self.assertEqual(waiting.result().status_code,499)
        payload.update(message='new reference interpretation',request_id='run-new')
        self.assertIn('garment',self.ok('A','POST','/api/canvas-llm',json=payload)['text'])
        self.ok('A','POST','/api/auth/logout')
        for endpoint in ('/api/canvas-llm','/api/canvas-image-tasks'):
            self.assertEqual(self.request('A','POST',endpoint,json=payload).status_code,401)
        self.assertEqual(self.request('A','GET',ref).status_code,401)

    def test_llm_total_deadline_cannot_be_extended_by_trickled_response(self):
        start=time.monotonic()
        response=self.request('A','POST','/api/canvas-llm',json={'provider':'atelier-text','model':'gemini-mock-vision','message':'TRICKLE'})
        self.assertEqual(response.status_code,504)
        self.assertLess(time.monotonic()-start,1.8)


class PolicyUnitTests(unittest.TestCase):
    def test_private_configuration_fails_closed_without_implicit_credentials(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();private=root/'.auth';private.mkdir(mode=0o700)
            credentials=private/'credentials';credentials.mkdir(mode=0o700)
            for filename in ('gemini.key','kie.key'):
                path=credentials/filename;path.write_text(secrets.token_urlsafe(24));path.chmod(0o600)
            paths=SimpleNamespace(data_root=root,upstreams={('127.0.0.1',43103)})
            policy=ModelPolicy(paths)
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:policy.allowed('gemini','any','llm')
            self.assertEqual(caught.exception.status_code,503)
            config=private/'model-access.json'
            config.write_text(json.dumps(configuration(43103)));config.chmod(0o600)
            self.assertEqual(len(ModelPolicy(paths).providers),2)
            for mutate in (lambda p:p.update(max_concurrent=0),lambda p:p.update(providers=[]),
                           lambda p:p['providers'][0].update(protocol='codex'),
                           lambda p:p['providers'][0].update(base_url='http://127.0.0.1:3000'),
                           lambda p:p['providers'][0].update(credential_file='../owner.key')):
                changed=configuration(43103);mutate(changed);config.write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError):ModelPolicy(paths)
            config.write_text(json.dumps(configuration(43103)));config.chmod(0o644)
            with self.assertRaises(RuntimeError):ModelPolicy(paths)
            config.chmod(0o600);(credentials/'gemini.key').chmod(0o644)
            with self.assertRaises(RuntimeError):ModelPolicy(paths)
            (credentials/'gemini.key').unlink();(credentials/'gemini.key').symlink_to(credentials/'kie.key')
            with self.assertRaises(RuntimeError):ModelPolicy(paths)

    def test_live_private_dns_and_foreign_media_origins_are_rejected(self):
        from types import SimpleNamespace
        provider={'base_url':'https://model.example','credential_file':'none','media_origins':['https://cdn.example']}
        policy=SimpleNamespace(mode='live',paths=SimpleNamespace(upstreams=set()),credential=lambda _:secrets.token_urlsafe(24))
        async def verify():
            client=GuardedClient(policy,provider)
            try:
                from fastapi import HTTPException
                with self.assertRaises(HTTPException):client.validate_media_url('file:///etc/passwd')
                with self.assertRaises(HTTPException):client.validate_media_url('http://127.0.0.1:3000/')
                with patch('instance_model_policy.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
                    with self.assertRaises(HTTPException):await client.get('https://cdn.example/image.png')
            finally:await client.aclose()
        asyncio.run(verify())
