"""Real isolated Instances against a local upstream, never paid APIs."""
import base64
import hashlib
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import test_instance_own_providers as fixtures

SETTINGS='/api/instance/provider-settings'
# Generated solid-color H.264 fixture (32x48, one second), no real user media.
MOCK_VIDEO_B64='AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAOwbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAtt0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAACAAAAAwAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAIAAABAAAAAAJTbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAoAAAAKABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAB/m1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAb5zdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAACAAMABIAAAASAAAAAAAAAABFUxhdmM2MS4xOS4xMDAgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2UnsBEAAAAMAQAAABQPEiWWAAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAABqAAAAagAAAABhzdHRzAAAAAAAAAAEAAAAKAAAEAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAYGN0dHMAAAAAAAAACgAAAAEAAAgAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAAAgAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAKAAAAAQAAADxzdHN6AAAAAAAAAAAAAAAKAAAC0wAAAA0AAAAMAAAADAAAAAwAAAATAAAADgAAAAwAAAAMAAAAEwAAABRzdGNvAAAAAAAAAAEAAAPgAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2MS43LjEwMAAAAAhmcmVlAAADWG1kYXQAAAKuBgX//6rcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMTkwIDdlZDc1M2IgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI0IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MTAgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAdZYiEABH//ufj/AptfMRxOnYY+vfW12z78GzLOU8AAAAJQZokbEEP/qqwAAAACEGeQniHfykhAAAACAGeYXRDfy4gAAAACAGeY2pDfy4hAAAAD0GaaEmoQWiZTAh3//6qsQAAAApBnoZFESw7/ykhAAAACAGepXRDfy4hAAAACAGep2pDfy4gAAAAD0GaqUmoQWyZTAhv//6pYA=='

class NetworkMock(fixtures.PersonalMock):
    def identity(self):
        value=getattr(self,'body_key','')
        auth=self.headers.get('Authorization','')
        if auth.startswith('HMAC-SHA256 Credential='):
            value=auth.split('Credential=',1)[1].split('/',1)[0]
        if value:return self.server.credentials.get(hashlib.sha256(value.encode()).hexdigest(),'')
        return super().identity()

    def do_POST(self):
        if urlsplit(self.path).path in {'/api/file-stream-upload','/api/v1/jobs/createTask'}:return super().do_POST()
        length=int(self.headers.get('Content-Length','0'))
        data=self.rfile.read(length)
        try: body=json.loads(data)
        except (ValueError,UnicodeDecodeError):
            body={}
            if 'multipart/form-data' in self.headers.get('Content-Type',''):
                from email.parser import BytesParser
                from email.policy import default
                message=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+data)
                for part in message.iter_parts():
                    if part.get_filename() is None:body[part.get_param('name',header='content-disposition')]=part.get_payload(decode=True).decode()
        self.body_key=body.get('apiKey','') if isinstance(body,dict) else ''
        owner=self.identity()
        path=urlsplit(self.path).path
        if path.startswith('/anonymous/'):
            self.server.calls.append(('anonymous-upload','',{'auth':bool(self.headers.get('Authorization'))}))
            return self.reply((self.server.origin+'/media/network.png').encode(),content_type='text/plain')
        if not owner:return self.reply({},401)
        if '/asset-api' in path:
            action=parse_qs(urlsplit(self.path).query)['Action'][0]
            self.server.calls.append(('signed-asset',owner,{'action':action,'body':body,'bearer':self.headers.get('Authorization','').startswith('Bearer')}))
            return self.reply({'Result':{'Items':[]}} if action=='ListAssetGroups' else {'Result':{'Id':'owned-group'}} if action=='CreateAssetGroup' else {'Result':{'Id':'owned-asset','Status':'Active'}})
        if '/uploads/' in path or path.endswith('/media/upload/binary'):
            self.server.calls.append(('network-upload',owner,{'path':path}))
            return self.reply({'data':{'url':self.server.origin+'/media/network.png'}})
        if path.endswith('/seedance2/private-avatar'):
            tid='mock-'+str(len(self.server.jobs));self.server.jobs[tid]=dict(owner=owner,video=False,avatar=True)
            self.server.calls.append(('avatar-submit',owner,body))
            return self.reply({'data':{'id':tid}})
        if path.endswith('/chat/completions') or (':generateContent' in path and 'IMAGE' not in json.dumps(body)):
            self.server.calls.append(('text',owner,body))
            if 'Personal API intent:' in json.dumps(body):
                return self.reply({'choices':[{'message':{'content':json.dumps({'action':'generate_image' if 'IMAGE_AGENT' in json.dumps(body) else 'chat'})}}]})
            if ':generateContent' in path:
                return self.reply({'candidates':[{'content':{'parts':[{'text':'MOCK garment'}]}}]})
            if body.get('stream'):
                key=self.headers.get('Authorization','').removeprefix('Bearer ')
                values=[key[:5],key[5:]] if 'ECHO_SECRET' in json.dumps(body) else ['MOCK ','garment']
                data=''.join('data: '+json.dumps({'choices':[{'delta':{'content':value}}]})+'\n\n' for value in values)+'data: [DONE]\n\n'
                return self.reply(data.encode(),content_type='text/event-stream')
            return self.reply({'choices':[{'message':{'content':'MOCK garment'}}]})
        if path.endswith('/query') or path.endswith('/outputs'):
            self.server.calls.append(('network-query',owner,{'path':path}))
            tid=body.get('taskId')
            job=self.server.jobs.get(tid)
            if not job or job['owner']!=owner:return self.reply({},403)
            url=self.server.origin+'/media/network.'+('mp4' if job['video'] else 'png')
            if path.endswith('/outputs'):return self.reply({'code':0,'data':[{'fileUrl':url}]})
            return self.reply({'taskId':tid,'status':'SUCCESS','results':[{'url':url}]})
        if path.endswith('/upload'):
            self.server.calls.append(('network-upload',owner,{}))
            return self.reply({'code':0,'data':{'fileName':'owned.png'}})
        self.server.calls.append(('network-submit',owner,{'path':path,'body':body,'multipart':b'Content-Disposition' in data,'credential_role':getattr(self.server,'key_roles',{}).get(hashlib.sha256((self.body_key or self.headers.get('Authorization','').removeprefix('Bearer ')).encode()).hexdigest(),'primary')}))
        if 'DROP_WITHOUT_RECEIPT' in json.dumps(body):
            import socket
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        if 'FAIL_SUBMIT' in json.dumps(body):return self.reply({'error':'rejected'},400)
        encoded=base64.b64encode(self.server.image).decode()
        if ':generateContent' in path:
            return self.reply({'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':encoded}}]}}]})
        if path.endswith('/responses'):
            if body.get('stream') or 'ASYNC_RESPONSE' in json.dumps(body):
                tid='mock-'+str(len(self.server.jobs));self.server.jobs[tid]=dict(owner=owner,video=False,response=True)
                if not body.get('stream'):return self.reply({'id':tid,'status':'queued'})
                created={'type':'response.created','response':{'id':tid,'status':'in_progress'}}
                completed={'type':'response.completed','response':{'id':tid,'status':'completed','output':[{'type':'image_generation_call','result':encoded}]}}
                data='data: '+json.dumps(created)+'\n\n'
                if 'DROP_AFTER_RECEIPT' not in json.dumps(body):data+='data: '+json.dumps(completed)+'\n\n'
                return self.reply(data.encode(),content_type='text/event-stream')
            return self.reply({'id':'response_owned','status':'completed','output':[{'type':'image_generation_call','result':encoded}]})
        if path.endswith('/async'):
            tid='mock-'+str(len(self.server.jobs));self.server.jobs[tid]=dict(owner=owner,video=False)
            return self.reply({'task_id':tid,'status':'queued'})
        if '/runninghub/' in path or '/apimart/' in path or '/volcengine' in path or '/videos' in path or '/video/create' in path or '/contents/generations/tasks' in path:
            tid='mock-'+str(len(self.server.jobs))
            video='video' in json.dumps(body).lower() or '/videos/generations' in path or '/video/create' in path or '/contents/generations/tasks' in path
            self.server.jobs[tid]=dict(owner=owner,video=video)
            if '/runninghub/' in path:
                return self.reply({'code':0,'data':{'taskId':tid}})
            return self.reply({'task_id':tid,'id':tid,'status':'queued'})
        if self.headers.get('X-ModelScope-Async-Mode'):
            tid='mock-'+str(len(self.server.jobs));self.server.jobs[tid]=dict(owner=owner,video=False,modelscope=True)
            return self.reply({'task_id':tid})
        return self.reply({'data':[{'b64_json':encoded}]})

    def do_GET(self):
        path=urlsplit(self.path).path
        if path.endswith(('/video/query','/agnesapi')):
            tid=next(iter(parse_qs(urlsplit(self.path).query).values()))[0]
            job=self.server.jobs.get(tid)
            if not job or job['owner']!=self.identity():return self.reply({},403)
            self.server.calls.append(('network-query',job['owner'],{'path':path}))
            return self.reply({'id':tid,'status':'completed','video_url':self.server.origin+'/media/network.mp4'})
        if path.startswith('/media/network.'):
            self.server.calls.append(('network-media','',{'authorization':bool(self.headers.get('Authorization')),'cookie':bool(self.headers.get('Cookie'))}))
            if getattr(self.server,'download_fail',False):return self.reply({},503)
            if path.endswith('.mp4'):return self.reply(base64.b64decode(MOCK_VIDEO_B64),content_type='video/mp4')
            return self.reply(self.server.image,content_type='image/png')
        tid=path.rsplit('/',1)[-1]
        if tid in self.server.jobs and tid.startswith('mock-'):
            job=self.server.jobs[tid]
            if job['owner']!=self.identity():return self.reply({},403)
            self.server.calls.append(('network-query',job['owner'],{'path':path}))
            url=self.server.origin+'/media/network.'+('mp4' if job['video'] else 'png')
            if job.get('avatar'):return self.reply({'data':{'id':tid,'status':'completed','asset_url':'asset://owned-avatar'}})
            if job.get('response'):return self.reply({'id':tid,'status':'completed','output':[{'type':'image_generation_call','result':base64.b64encode(self.server.image).decode()}]})
            if job['video']:return self.reply({'id':tid,'status':'completed','video_url':url,'result':{'video_url':url}})
            if '/midjourney/' in path:return self.reply({'id':tid,'status':'completed','image_urls':[url]})
            if job.get('modelscope'):return self.reply({'task_id':tid,'task_status':'SUCCEED','output_images':[url]})
            return self.reply({'id':tid,'status':'completed','data':[{'url':url}]})
        return super().do_GET()

class PersonalNetworkTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.OwnProviderTests()
        with patch.object(fixtures,'PersonalMock',NetworkMock):self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def save(self,protocol='openai',name='novel-model-v2030',purpose='image',mode='openai',owner='A',**extra):
        category={'llm':'chat_models','image':'image_models','video':'video_models'}[purpose]
        provider=dict(id='same-personal-id',name='My network',protocol=protocol,enabled=True,
                      base_url=self.f.mock.origin+('' if protocol=='volcengine' else '/'+protocol),api_key=self.f.keys[owner],
                      image_request_mode=mode,**{category:[name]})
        if protocol=='runninghub':
            provider['wallet_api_key']=self.f.keys[owner]
            provider['model_adapters']={name:'runninghub-openapi'}
        else:provider['clear_wallet_key']=True
        provider.update(extra)
        return self.f.ok(owner,'PUT',SETTINGS,json=[provider])['providers'][0]

    def image(self,model='novel-model-v2030',**extra):
        return self.f.ok('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model=model,prompt='garment',size='2048x3072',aspect_ratio='2:3',resolution='2K',**dict({'quality':'high'},**extra)))

    def test_generic_new_id_image_modes_and_native_protocols_execute(self):
        for protocol,mode in [('openai','openai'),('openai','openai-json'),('openai','openai-responses'),('gemini','openai'),('volcengine','openai'),('apimart','openai'),('openai','openai-video-proxy'),('runninghub','openai')]:
            with self.subTest(protocol=protocol,mode=mode):
                self.save(protocol=protocol,mode=mode)
                result=self.image(quality='auto' if protocol in {'gemini','apimart','volcengine'} or mode=='openai-video-proxy' else 'high')
                self.assertTrue(result['images'][0].startswith('/'))
                self.assertEqual(result['model'],'novel-model-v2030')
        self.assertEqual(len([c for c in self.f.mock.calls if c[0]=='network-submit']),8)
        self.assertTrue(all(not c[2]['authorization'] and not c[2]['cookie'] for c in self.f.mock.calls if c[0]=='network-media'))

    def test_multi_purpose_model_and_catalog_are_not_deduplicated(self):
        p=self.save(chat_models=['novel-model-v2030'],video_models=['novel-model-v2030'])
        self.assertTrue(all(p['capabilities'][kind]['novel-model-v2030']['executable'] for kind in ('llm','image','video')))
        catalog=self.f.ok('A','GET','/api/config')['api_providers']
        p=next(p for p in catalog if p['id']=='same-personal-id')
        for category in ('chat_models','image_models','video_models'):self.assertEqual(p[category],['novel-model-v2030'])
        self.assertEqual(self.f.ok('A','GET',SETTINGS)['providers'][0]['image_models'],p['image_models'])

    def test_text_protocol_override_and_user_token_parameter(self):
        for protocol in ('openai','gemini','apimart','volcengine','runninghub'):
            self.save(protocol=protocol,purpose='llm')
            result=self.f.ok('A','POST','/api/canvas-llm',json={'provider':'same-personal-id','model':'novel-model-v2030','message':'garment','max_output_tokens':16384})
            self.assertIn('garment',result['text'])
        self.save(purpose='llm',model_protocols={'novel-model-v2030':'gemini'})
        self.f.ok('A','POST','/api/canvas-llm',json={'provider':'same-personal-id','model':'novel-model-v2030','message':'garment'})
        texts=[c[2] for c in self.f.mock.calls if c[0]=='text']
        self.assertEqual(texts[0]['max_tokens'],16384)
        self.assertEqual(texts[1]['generationConfig']['maxOutputTokens'],16384)

    def test_video_async_and_original_job_reuse(self):
        for protocol in ('openai','apimart','volcengine','runninghub'):
            with self.subTest(protocol=protocol):
                self.save(protocol=protocol,purpose='video',name='novel-video-v2030')
                payload={'provider_id':'same-personal-id','model':'novel-video-v2030','prompt':'video garment','duration':8,'aspect_ratio':'9:16','resolution':'720p','request_id':'video-'+protocol}
                first=self.f.ok('A','POST','/api/canvas-video',json=payload)
                self.assertTrue(first['videos'][0].startswith('/'))
                second=self.f.ok('A','POST','/api/canvas-video',json=payload)
                self.assertEqual(first['task_id'],second['task_id'])
                self.assertEqual(self.f.request('B','GET','/api/canvas-image-tasks/'+first['task_id']).status_code,404)

    def test_absent_use_disabled_provider_and_missing_adapter_are_distinct(self):
        self.save(purpose='llm')
        r=self.f.request('A','POST','/api/online-image',json={'provider_id':'same-personal-id','model':'novel-model-v2030','prompt':'test'})
        self.assertEqual(r.status_code,403)
        p=self.save(enabled=False)
        self.assertIn('停用',p['capabilities']['image']['novel-model-v2030']['reason'])
        p=self.save(protocol='kie')
        self.assertIn('契约',p['capabilities']['image']['novel-model-v2030']['reason'])
        self.assertEqual([c for c in self.f.mock.calls if c[0]=='network-submit'],[])

    def test_a_b_identical_provider_and_model_have_private_keys(self):
        from instance_auth import AuthStore
        self.f.stop('B')
        AuthStore(self.f.roots['B'],'B').set_provider_permission('B',True)
        self.f.start('B')
        for owner in ('A','B'):
            self.save(purpose='llm',owner=owner)
            self.f.ok(owner,'POST','/api/canvas-llm',json={'provider':'same-personal-id','model':'novel-model-v2030','message':'garment'})
            public=json.dumps(self.f.ok(owner,'GET',SETTINGS))+json.dumps(self.f.ok(owner,'GET','/api/config'))
            for key in self.f.keys.values():self.assertNotIn(key,public)
        self.assertEqual([owner for kind,owner,_ in self.f.mock.calls if kind=='text'],['A','B'])

    def test_runninghub_app_workflow_and_video_use_owned_contracts(self):
        fields=[dict(nodeId='10',fieldName='text',type='text',role='prompt',enabled=True)]
        for kind,purpose in [('app','image'),('workflow','image'),('app','video'),('workflow','video')]:
            with self.subTest(kind=kind,purpose=purpose):
                model=kind+':2030'
                self.save(protocol='runninghub',name=model,purpose=purpose,model_adapters={},**{'rh_apps' if kind=='app' else 'rh_workflows':[dict(id='2030',fields=fields)]})
                if purpose=='image':result=self.image(model=model,quality='auto')
                else:result=self.f.ok('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model=model,prompt='video garment'))
                self.assertTrue(result['images' if purpose=='image' else 'videos'][0].startswith('/'))
        submits=[c[2]['body'] for c in self.f.mock.calls if c[0]=='network-submit']
        self.assertEqual(len(submits),4)
        self.assertEqual(submits[0]['nodeInfoList'][0]['fieldValue'],'garment')
        self.assertTrue(all('webappId' in b or 'workflowId' in b for b in submits))

    def test_image_edit_multipart_and_custom_same_origin_endpoints(self):
        self.save(image_generation_endpoint='/custom/generate',image_edit_endpoint='/custom/edit')
        ref=self.f.upload()
        generated=self.image()
        edited=self.image(operation='edit',reference_images=[dict(url=ref)])
        self.assertTrue(generated['images'] and edited['images'])
        submits=[c[2] for c in self.f.mock.calls if c[0]=='network-submit']
        self.assertEqual([c['path'] for c in submits],['/custom/generate','/custom/edit'])
        self.assertTrue(submits[-1]['multipart'])
        before=len(self.f.mock.calls)
        rejected=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',operation='edit'))
        self.assertEqual(rejected.status_code,400)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_download_recovery_only_queries_original_job_after_restart(self):
        self.save(protocol='apimart')
        self.f.mock.download_fail=True
        task=self.f.ok('A','POST','/api/canvas-image-tasks',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',request_id='download-recovery'))['task_id']
        first=self.f.wait_job(task)
        self.assertEqual(first['status'],'result_recovery_required')
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),1)
        self.f.stop('A');self.f.start('A')
        self.f.mock.download_fail=False
        self.f.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        result=self.f.wait_job(task)
        self.assertEqual(result['status'],'succeeded')
        self.f.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh')
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),1)
        history=self.f.ok('A','GET','/api/history')
        self.assertEqual(sum(h.get('task_id')==task for h in history),1)

    def test_purpose_adapter_and_protocol_do_not_overwrite_other_uses(self):
        p=self.save(chat_models=['novel-model-v2030'],video_models=['novel-model-v2030'],model_adapters={'image|novel-model-v2030':'openai-responses'},model_protocols={'llm|novel-model-v2030':'gemini'})
        self.assertTrue(all(c['novel-model-v2030']['executable'] for c in p['capabilities'].values()))
        self.assertEqual(p['capabilities']['llm']['novel-model-v2030']['protocol'],'gemini')
        self.assertEqual(p['capabilities']['image']['novel-model-v2030']['adapter'],'openai-responses')
        self.f.ok('A','POST','/api/canvas-llm',json=dict(provider='same-personal-id',model='novel-model-v2030',message='garment'))
        self.image()

    def test_platform_enum_validation_happens_before_material_upload(self):
        model='novel-model-v2030'
        self.save(protocol='runninghub',rh_model_definitions=[dict(name_en=model,endpoint=model,params=[dict(fieldKey='resolution',options=['2k','4k'])])])
        ref=self.f.upload()
        before=len(self.f.mock.calls)
        r=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model=model,prompt='garment',resolution='8k',reference_images=[dict(url=ref)]))
        self.assertEqual(r.status_code,400)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_modelscope_async_contract_uses_private_key_and_original_model(self):
        self.save(model_adapters={'image|novel-model-v2030':'modelscope-async'})
        result=self.image(quality='auto')
        self.assertTrue(result['images'][0].startswith('/'))
        submits=[c[2]['body'] for c in self.f.mock.calls if c[0]=='network-submit']
        self.assertEqual(submits[0]['model'],'novel-model-v2030')
        self.assertEqual((submits[0]['width'],submits[0]['height']),(2048,3072))
        self.assertEqual(sum(c[0]=='network-query' for c in self.f.mock.calls),1)

    def test_native_runninghub_nodes_validate_contract_and_owned_uploads(self):
        fields=[dict(nodeId='10',fieldName='text',type='text',enabled=True),dict(nodeId='20',fieldName='image',type='image',enabled=True,required=True)]
        self.save(protocol='runninghub',name='app:2030',model_adapters={},rh_apps=[dict(id='2030',fields=fields)])
        ref=self.f.upload()
        uploaded=self.f.ok('A','POST','/api/runninghub/upload-asset',json=dict(provider_id='same-personal-id',url=ref))['data']['fileName']
        payload=dict(provider_id='same-personal-id',webappId='2030',nodeInfoList=[dict(nodeId='10',fieldName='text',fieldValue='garment'),dict(nodeId='20',fieldName='image',fieldValue=uploaded)],request_id='native-rh')
        first=self.f.ok('A','POST','/api/runninghub/submit',json=payload)['data']['taskId']
        self.assertEqual(self.f.wait_job(first)['status'],'succeeded')
        self.assertEqual(self.f.ok('A','GET','/api/runninghub/query',params={'taskId':first})['data']['status'],'SUCCESS')
        self.assertEqual(self.f.request('B','GET','/api/runninghub/query',params={'taskId':first}).status_code,404)
        self.assertEqual(self.f.ok('A','POST','/api/runninghub/submit',json=payload)['data']['taskId'],first)
        payload['nodeInfoList'][1]['fieldValue']='other-user-file.png';payload['request_id']='forged-file'
        before=len(self.f.mock.calls)
        self.assertEqual(self.f.request('A','POST','/api/runninghub/submit',json=payload).status_code,403)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_platform_asset_upload_review_and_signed_credentials(self):
        self.save(protocol='apimart')
        ref=self.f.upload()
        lib=self.f.ok('A','GET','/api/asset-library')
        library=lib['libraries'][0] if 'libraries' in lib else lib['library']['libraries'][0]
        item=self.f.ok('A','POST','/api/asset-library/items',json=dict(library_id=library['id'],category_id=library['categories'][0]['id'],url=ref,name='mock avatar'))['item']
        path='/api/asset-library/items/'+item['id']
        payload=dict(provider_id='same-personal-id',library_id=library['id'])
        result=self.f.ok('A','POST',path+'/register-avatar',json=payload)
        self.assertEqual(result['item']['registrations']['apimart']['status'],'Processing')
        self.assertEqual(self.f.ok('A','POST',path+'/avatar-status',json=payload)['item']['registrations']['apimart']['status'],'Active')
        self.assertEqual(self.f.request('B','POST',path+'/avatar-status',json=payload).status_code,403)
        self.assertEqual(self.f.request('A','POST',path+'/register-avatar',json=payload).status_code,409)
        published=self.f.ok('A','POST','/api/cloud-video/upload',json=dict(url=item['url'],service='temp.sh'))
        self.assertTrue(published['url'].startswith(self.f.mock.origin))
        ak=self.f.keys['A']+'-ak';sk=self.f.keys['A']+'-sk'
        self.f.mock.credentials[hashlib.sha256(ak.encode()).hexdigest()]='A'
        self.save(protocol='volcengine',volcengine_access_key_id=ak,volcengine_secret_access_key=sk)
        payload['public_url']=published['url']
        result=self.f.ok('A','POST',path+'/register-avatar',json=payload)
        self.assertEqual(result['item']['registrations']['volcengine']['task_id'],'owned-asset')
        self.assertEqual(self.f.ok('A','POST',path+'/avatar-status',json=payload)['item']['registrations']['volcengine']['status'],'Active')
        signed=[c for c in self.f.mock.calls if c[0]=='signed-asset']
        self.assertEqual([c[2]['action'] for c in signed],['ListAssetGroups','CreateAssetGroup','CreateAsset','GetAsset'])
        self.assertTrue(all(not c[2]['bearer'] for c in signed))
        self.assertTrue(all(not c[2]['auth'] for c in self.f.mock.calls if c[0]=='anonymous-upload'))
        for secret in (ak,sk):self.assertNotIn(secret,json.dumps(self.f.ok('A','GET',SETTINGS)))

    def test_unsupported_quality_is_explicit_and_precedes_upload(self):
        self.save(protocol='gemini')
        ref=self.f.upload();before=len(self.f.mock.calls)
        response=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',quality='high',reference_images=[dict(url=ref)]))
        self.assertEqual(response.status_code,400);self.assertIn('quality',response.text)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_mock_success_does_not_claim_real_upstream_verification(self):
        self.save();self.image()
        for p in (self.f.ok('A','GET',SETTINGS)['providers'][0],self.f.ok('A','GET','/api/config')['api_providers'][-1]):
            cap=p['capabilities']['image']['novel-model-v2030']
            self.assertTrue(cap['mock_verified']);self.assertFalse(cap['verified'])

    def test_text_stream_native_compatibility_conversation_isolation_and_key_redaction(self):
        for protocol in ('openai','gemini','apimart','volcengine','runninghub'):
            self.save(protocol=protocol,purpose='llm')
            response=self.f.request('A','POST','/api/chat/stream',json=dict(provider='same-personal-id',model='novel-model-v2030',message='garment'))
            self.assertEqual(response.status_code,200)
            events=[json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]
            self.assertEqual(''.join(e.get('delta','') for e in events),'MOCK garment')
            conversation=events[-1]['conversation']['id']
            self.assertEqual(self.f.request('B','GET','/api/conversations/'+conversation).status_code,404)
        self.save(purpose='llm')
        response=self.f.request('A','POST','/api/chat/stream',json=dict(provider='same-personal-id',model='novel-model-v2030',message='ECHO_SECRET'))
        self.assertNotIn(self.f.keys['A'],response.text);self.assertIn('[redacted]',response.text)

    def test_async_modes_preserve_model_dimensions_prompt_and_original_receipt(self):
        for mode in ('tudou-async','openai-responses','openai-responses-stream','openai-responses-sync'):
            self.save(mode=mode)
            result=self.image()
            self.assertTrue(result['images'][0].startswith('/'))
            submit=next(c[2]['body'] for c in reversed(self.f.mock.calls) if c[0]=='network-submit')
            self.assertEqual(submit['model'],'novel-model-v2030')
            if mode.startswith('openai-responses'):
                self.assertEqual(submit['tools'][0]['size'],'2048x3072')
                self.assertEqual(submit['input'][0]['content'][0]['text'],'garment')
        self.save(mode='openai-responses-stream')
        response=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='DROP_AFTER_RECEIPT'))
        self.assertEqual(response.status_code,502)
        task=response.json()['detail']['task_id'];before=sum(c[0]=='network-submit' for c in self.f.mock.calls)
        self.f.stop('A');self.f.start('A')
        self.f.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh',json={})
        self.assertEqual(self.f.wait_job(task)['status'],'succeeded')
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),before)

    def test_midjourney_generations_edit_action_and_owned_parent(self):
        self.save(protocol='apimart',model_adapters={'image|novel-model-v2030':'midjourney'})
        first=self.f.ok('A','POST','/api/midjourney/submit',json=dict(provider_id='same-personal-id',prompt='garment',size='9:16',speed='fast'))
        task=self.f.wait_job(first['task_id']);self.assertEqual(task['status'],'succeeded')
        action=self.f.ok('A','POST','/api/midjourney/actions',json=dict(provider_id='same-personal-id',task_id=first['task_id'],action='upscale',index=2))
        self.assertEqual(self.f.wait_job(action['task_id'])['status'],'succeeded')
        ref=self.f.upload()
        edited=self.f.ok('A','POST','/api/midjourney/submit',json=dict(provider_id='same-personal-id',prompt='edit garment',mode='edit',reference_images=[dict(url=ref)]))
        self.assertEqual(self.f.wait_job(edited['task_id'])['status'],'succeeded')
        modal=self.f.ok('A','POST','/api/midjourney/modal',json=dict(provider_id='same-personal-id',task_id=edited['task_id'],mask_image=dict(url=ref)))
        self.assertEqual(self.f.wait_job(modal['task_id'])['status'],'succeeded')
        before=len(self.f.mock.calls)
        r=self.f.request('A','POST','/api/midjourney/actions',json=dict(provider_id='same-personal-id',task_id='forged-upstream-id',action='upscale',index=1))
        self.assertEqual(r.status_code,404)
        self.assertEqual(len(self.f.mock.calls),before)
        self.assertEqual(self.f.request('B','GET','/api/midjourney/tasks/'+first['task_id']+'?provider_id=same-personal-id').status_code,404)



    def test_optional_existing_video_adapters_keep_exact_model_id_and_params(self):
        from provider_capabilities import VIDEO_ADAPTERS
        for adapter in sorted(VIDEO_ADAPTERS):
            with self.subTest(adapter=adapter):
                self.save(protocol='apimart' if adapter=='apimart-veo31' else 'openai',purpose='video',name='future-video-exact',model_adapters={'video|future-video-exact':adapter})
                refs=[]
                if adapter=='tudou-kling':
                    ref=self.f.upload();self.f.ok('A','POST','/api/cloud-video/upload',json={'url':ref,'service':'temp.sh'});refs=[{'url':ref}]
                result=self.f.ok('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='future-video-exact',prompt='video garment',duration=5 if adapter=='yuli-native-video' else 8,aspect_ratio='9:16',resolution='' if adapter in {'yuli-native-video','tudou-sora2','tudou-kling'} else '720p',images=refs))
                self.assertTrue(result['videos'][0].startswith('/'))
                body=[c[2]['body'] for c in self.f.mock.calls if c[0]=='network-submit'][-1]
                self.assertEqual(body['model'],'future-video-exact')
                if 'duration' in body:self.assertEqual(body['duration'],8)
                if 'seconds' in body:self.assertEqual(body['seconds'],'8')
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),len(VIDEO_ADAPTERS))
        self.assertTrue(all(not c[2]['auth'] for c in self.f.mock.calls if c[0]=='anonymous-upload'))

    def test_kie_new_exact_id_existing_template_and_non_square_2k(self):
        self.save(base_url=self.f.mock.origin,model_protocols={'image|novel-model-v2030':'kie'},model_adapters={'image|novel-model-v2030':'gpt-image-2'},chat_models=['novel-model-v2030'])
        prompt=' \nKie exact garment prompt \n '
        result=self.f.ok('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt=prompt,size='2048x3072',aspect_ratio='2:3',resolution='2K',quality='auto',reference_images=[{'url':self.f.upload()}]))
        self.assertTrue(result['images'][0].startswith('/'))
        body=next(c[2] for c in self.f.mock.calls if c[0]=='create')
        self.assertEqual(body['model'],'novel-model-v2030')
        self.assertEqual(body['input']['aspect_ratio'],'2:3')
        self.assertEqual(body['input']['resolution'],'2K')
        self.assertEqual(body['input']['prompt'],prompt)
        self.f.ok('A','POST','/api/canvas-llm',json=dict(provider='same-personal-id',model='novel-model-v2030',message='garment'))
        before=len(self.f.mock.calls)
        for extra in ({'output_format':'webp'},{'adapter_parameters':{'unexpected':True}}):
            response=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',**extra))
            self.assertEqual(response.status_code,400);self.assertIn('契约',response.text)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_unknown_submission_without_id_never_resubmits_even_after_restart(self):
        self.save()
        response=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='DROP_WITHOUT_RECEIPT',request_id='uncertain-original'))
        self.assertEqual(response.status_code,502)
        task=response.json()['detail']['task_id']
        self.f.stop('A');self.f.start('A')
        self.f.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh',json={})
        status=self.f.wait_job(task)
        self.assertEqual(status['recovery'],'manual-reconcile')
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),1)

    def test_runninghub_saved_single_media_contract_rejects_before_upload(self):
        self.save(protocol='runninghub',rh_model_definitions=[dict(id='novel-model-v2030',endpoint='novel-model-v2030',params=[dict(fieldKey='imageUrl')])])
        ref=self.f.upload();before=len(self.f.mock.calls)
        response=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',reference_images=[{'url':ref},{'url':ref}]))
        self.assertEqual(response.status_code,400);self.assertEqual(len(self.f.mock.calls),before)
        cap=self.f.ok('A','GET',SETTINGS)['providers'][0]['capabilities']['image']['novel-model-v2030']
        self.assertNotIn('quality',cap['parameters'])

    def test_tudou_grok_edit_exact_model_and_owned_asset_uri_video(self):
        self.save(model_adapters={'image|novel-model-v2030':'tudou-grok-image'})
        result=self.image(quality='auto',operation='edit',reference_images=[{'url':self.f.upload()}])
        self.assertTrue(result['images'])
        self.assertEqual([c[2]['body']['model'] for c in self.f.mock.calls if c[0]=='network-submit'],['novel-model-v2030'])
        self.save(protocol='apimart',purpose='video',name='future-video-exact')
        lib=self.f.ok('A','GET','/api/asset-library')
        self.assertIsInstance(lib,dict)
        response=self.f.request('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='future-video-exact',prompt='video',trusted_asset=True,images=[{'url':'asset://other-user'}]))
        self.assertEqual(response.status_code,403)

    def test_existing_modelscope_cloud_routes_loras_and_original_task_recovery(self):
        self.save(mode='modelscope-async',ms_loras=[dict(id='future-lora',target_model='novel-model-v2030',strength=.7)])
        ref=self.f.upload()
        for path in ('/api/ms/generate','/api/angle/generate','/generate'):
            result=self.f.ok('A','POST',path,json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='  original garment  ',size='2048x3072',resolution='2048x3072',image_urls=[ref],loras={'future-lora':.7},request_id='route-'+path.replace('/','-')))
            self.assertTrue(result['url'].startswith('/'))
            body=next(c[2]['body'] for c in reversed(self.f.mock.calls) if c[0]=='network-submit')
            self.assertEqual(body['prompt'],'  original garment  ');self.assertEqual(body['loras'],{'future-lora':.7})
            self.f.ok('A','POST','/api/angle/poll_status',json={'task_id':result['task_id']})
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),3)
        self.assertEqual(self.f.request('B','POST','/api/angle/poll_status',json={'task_id':result['task_id']}).status_code,404)
        self.assertEqual(self.f.request('A','POST','/api/ms/generate',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment',api_key='unaccepted-inline-key')).status_code,400)

    def test_runninghub_primary_and_wallet_keys_are_distinct_and_user_owned(self):
        for owner in ('A','B'):
            from instance_auth import AuthStore
            if owner=='B':
                AuthStore(self.f.roots[owner],owner).set_provider_permission(owner,True);self.f.stop(owner);self.f.start(owner)
            wallet=self.f.keys[owner]+'-wallet'
            digest=hashlib.sha256(wallet.encode()).hexdigest();self.f.mock.credentials[digest]=owner
            self.f.mock.key_roles=getattr(self.f.mock,'key_roles',{})|{digest:'wallet'}
            self.save(protocol='runninghub',purpose='video',owner=owner,wallet_api_key=wallet,name='future-video')
            self.f.ok(owner,'POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='future-video',prompt='video',duration=8,aspect_ratio='9:16'))
            body=next(c for c in reversed(self.f.mock.calls) if c[0]=='network-submit');self.assertEqual(body[1],owner);self.assertEqual(body[2]['credential_role'],'wallet')
            self.assertNotIn(wallet,json.dumps(self.f.ok(owner,'GET',SETTINGS)))

    def test_owned_registered_avatar_is_accepted_only_with_original_provider_revision(self):
        self.save(protocol='apimart',purpose='video',name='future-video-exact')
        lib=self.f.ok('A','GET','/api/asset-library');library=lib.get('libraries',lib.get('library',{}).get('libraries'))[0]
        item=self.f.ok('A','POST','/api/asset-library/items',json=dict(library_id=library['id'],category_id=library['categories'][0]['id'],url=self.f.upload(),name='mock avatar'))['item']
        path='/api/asset-library/items/'+item['id'];payload=dict(provider_id='same-personal-id',library_id=library['id'])
        self.f.ok('A','POST',path+'/register-avatar',json=payload);self.f.ok('A','POST',path+'/avatar-status',json=payload)
        result=self.f.ok('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='future-video-exact',prompt='video garment',trusted_asset=True,images=[{'url':'asset://owned-avatar'}]))
        self.assertTrue(result['videos'])
        body=next(c[2]['body'] for c in reversed(self.f.mock.calls) if c[0]=='network-submit')
        self.assertIn('asset://owned-avatar',json.dumps(body))

    def test_explicit_video_pixel_dimensions_are_not_downgraded_by_presets(self):
        for adapter in ('tudou-grok-video','agnes-video'):
            self.save(purpose='video',name='future-video',model_adapters={'video|future-video':adapter})
            self.f.ok('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='future-video',prompt='video',duration=24,aspect_ratio='2:3',resolution='4K'))
            body=next(c[2]['body'] for c in reversed(self.f.mock.calls) if c[0]=='network-submit')
            self.assertEqual(body.get('size') or str(body['width'])+'x'+str(body['height']),'2160x3240')
            if adapter=='agnes-video':self.assertEqual(body['num_frames'],577)

    def test_original_large_kie_reference_bytes_are_not_recompressed(self):
        from io import BytesIO
        from PIL import Image
        import random
        rng=random.Random(17);data=bytes(rng.randrange(256) for _ in range(700*700*4))
        stream=BytesIO();Image.frombytes('RGBA',(700,700),data).save(stream,'PNG');original=stream.getvalue()
        self.assertGreater(len(original),1048576)
        self.f.mock.image=original
        self.save(protocol='kie',base_url=self.f.mock.origin,model_adapters={'image|novel-model-v2030':'gpt-image-2'})
        self.assertTrue(self.image(quality='auto',reference_images=[{'url':self.f.upload()}])['images'])
        self.assertTrue(next(c[2]['contains_image'] for c in self.f.mock.calls if c[0]=='upload'))

    def test_legacy_digest_upgrade_recovers_original_job_without_model_submission(self):
        import sqlite3
        from types import SimpleNamespace
        from instance_model_policy import ModelPolicy
        from instance_providers import legacy_task_provider_revision,task_provider_revision
        self.save(protocol='kie',name='gpt-image-2',base_url=self.f.mock.origin)
        self.f.mock.finish_waiting=False
        task=self.f.ok('A','POST','/api/canvas-image-tasks',json=dict(provider_id='same-personal-id',model='gpt-image-2',prompt='WAIT_IMAGE',request_id='legacy-owned-job'))['task_id']
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.f.ok('A','GET','/api/canvas-image-tasks/'+task)['has_upstream_task']:break
            time.sleep(.03)
        self.f.stop('A')
        paths=SimpleNamespace(data_root=self.f.roots['A'],port=self.f.ports['A'],upstreams={('127.0.0.1',self.f.mock.server_port)})
        policy=ModelPolicy(paths);provider=policy.providers['same-personal-id']
        legacy=legacy_task_provider_revision(policy,provider);current=task_provider_revision(policy,provider)
        self.assertNotEqual(legacy,current)
        with sqlite3.connect(self.f.roots['A']/'.auth/model-tasks.sqlite3') as db:
            job=json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(task,)).fetchone()[0]);job['provider_revision']=legacy
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(job),task))
        self.f.mock.finish_waiting=True;self.f.start('A')
        self.f.ok('A','POST','/api/canvas-image-tasks/'+task+'/refresh',json={})
        self.assertEqual(self.f.wait_job(task)['status'],'succeeded')
        self.assertEqual(sum(c[0]=='create' for c in self.f.mock.calls),1)
        with sqlite3.connect(self.f.roots['A']/'.auth/model-tasks.sqlite3') as db:self.assertEqual(json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(task,)).fetchone()[0])['provider_revision'],current)

    def test_legacy_digest_matches_golden_production_5449_compiler(self):
        import tempfile
        from types import SimpleNamespace
        from instance_providers import legacy_task_provider_revision
        # Golden produced by running the actual compiler at production 5449a692.
        stored=dict(id='legacy',name='Legacy API',protocol='kie',base_url='http://127.0.0.1:43219',enabled=True,models=[{'id':'gpt-image-2','purpose':'image'}],credential_file='credentials/personal-'+('1'*32)+'.key',revision='legacy-revision')
        with tempfile.TemporaryDirectory() as temporary:
            file=__import__('pathlib').Path(temporary)/'policy.json';file.write_text(json.dumps({'personal_providers':[stored]}))
            policy=SimpleNamespace(file=file,mode='mock',providers={},credential=lambda provider:'fake-legacy-key')
            self.assertEqual(legacy_task_provider_revision(policy,dict(stored,personal=True)),'3acf9724da6dd6473d9b3636afbada5072d4dd109f754d437c66c63a75b1c467')

    def test_explicit_new_request_nonce_can_repeat_and_original_nonce_stays_idempotent(self):
        self.save()
        first=self.image(request_id='intentional-first')
        second=self.image(request_id='intentional-second')
        self.assertNotEqual(first['task_id'],second['task_id'])
        self.assertEqual(self.image(request_id='intentional-first')['task_id'],first['task_id'])
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),2)

    def test_owned_local_asset_caption_uses_personal_multimodal_executor(self):
        self.save(purpose='llm')
        uploaded=self.f.ok('A','POST','/api/local-assets/upload',files={'files':('owned.png',self.f.mock.image,'image/png')})
        # Public media never uses a filesystem path or another Instance's credential.
        names=[item['file'] for item in uploaded['files']]
        result=self.f.ok('A','POST','/api/local-assets/caption',json=dict(names=names,provider='same-personal-id',model='novel-model-v2030',prompt='describe garment'))
        self.assertEqual(result['count'],1,result)
        call=next(c for c in self.f.mock.calls if c[0]=='text');self.assertEqual(call[1],'A')
        self.assertIn('image_url',json.dumps(call[2]));self.assertEqual(call[2]['model'],'novel-model-v2030')
        from instance_auth import AuthStore
        AuthStore(self.f.roots['B'],'B').set_provider_permission('B',True);self.f.stop('B');self.f.start('B');self.save(owner='B',purpose='llm')
        other=self.f.ok('B','POST','/api/local-assets/caption',json=dict(names=names,provider='same-personal-id',model='novel-model-v2030'))
        self.assertEqual(other['count'],0)

    def test_native_image_edit_contracts_keep_owned_references_and_new_model_id(self):
        ref=self.f.upload()
        for protocol,mode in [('gemini','openai'),('apimart','openai'),('volcengine','openai'),('runninghub','openai'),('openai','modelscope-async')]:
            with self.subTest(protocol=protocol,mode=mode):
                self.save(protocol=protocol,mode=mode)
                result=self.image(quality='auto',operation='edit',reference_images=[dict(url=ref)],request_id='edit-'+protocol)
                self.assertTrue(result['images'][0].startswith('/'))
                body=[c[2]['body'] for c in self.f.mock.calls if c[0]=='network-submit'][-1]
                self.assertIn('novel-model-v2030',json.dumps(body) if protocol not in {'gemini','runninghub'} else [c[2]['path'] for c in self.f.mock.calls if c[0]=='network-submit'][-1])
                self.assertTrue(any(k in json.dumps(body) for k in ('inlineData','image','reference')))
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),5)
        self.assertTrue(all(not c[2]['authorization'] for c in self.f.mock.calls if c[0]=='network-media'))

    def test_parameters_without_contract_and_conflicting_frame_roles_fail_before_upload(self):
        ref=self.f.upload()
        for adapter in ('tudou-sora2','tudou-kling'):
            self.save(purpose='video',model_adapters={'video|novel-model-v2030':adapter})
            before=len(self.f.mock.calls)
            r=self.f.request('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='video',resolution='4K'))
            self.assertEqual(r.status_code,400);self.assertIn('契约',r.text);self.assertEqual(len(self.f.mock.calls),before)
        self.save(protocol='volcengine',purpose='video')
        before=len(self.f.mock.calls)
        r=self.f.request('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='video',multimodal=True,images=[dict(url=ref,role='first_frame')]*2))
        self.assertEqual(r.status_code,400);self.assertEqual(len(self.f.mock.calls),before)

    def test_chat_agent_uses_private_text_and_image_executors_without_prompt_rewrite(self):
        self.save(chat_models=['novel-model-v2030'])
        result=self.f.ok('A','POST','/api/chat/agent',json=dict(provider='same-personal-id',model='novel-model-v2030',message='IMAGE_AGENT original garment',image_provider='same-personal-id',image_model='novel-model-v2030',size='2048x3072',aspect_ratio='2:3',resolution='2K'))
        self.assertEqual(result['agent']['action'],'generate_image')
        call=next(c for c in self.f.mock.calls if c[0]=='network-submit')
        self.assertEqual(call[1],'A');self.assertEqual(call[2]['body']['prompt'],'IMAGE_AGENT original garment')
        self.assertTrue(result['message']['image_url'].startswith('/'))
        self.assertEqual(sum(c[0]=='network-submit' for c in self.f.mock.calls),1)

    def test_adapter_extras_cannot_override_credentials_materials_or_prompt(self):
        self.save()
        ref=self.f.upload()
        for extra in ({'API_KEY':'fake-extra'},{'image_urls':['https://invalid.example/foreign.png']},{'Prompt':'replace prompt'},{'model':'replace-model'},{'loras':{'exact-lora':True}}):
            before=len(self.f.mock.calls)
            result=self.f.request('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='original prompt',images=[ref],adapter_parameters=extra))
            self.assertEqual(result.status_code,400)
            self.assertEqual(len(self.f.mock.calls),before)

    def test_native_protocol_overrides_do_not_inherit_openai_response_mode(self):
        for protocol in ('gemini','apimart','volcengine','runninghub'):
            with self.subTest(protocol=protocol):
                extra=dict(wallet_api_key=self.f.keys['A'],clear_wallet_key=False,model_adapters={'image|novel-model-v2030':'runninghub-openapi'}) if protocol=='runninghub' else {}
                saved=self.save(mode='openai-responses',model_protocols={'image|novel-model-v2030':protocol},chat_models=['novel-model-v2030'],video_models=['novel-model-v2030'],**extra)
                self.assertTrue(saved['capabilities']['image']['novel-model-v2030']['executable'])
                result=self.image(quality='auto')
                self.assertTrue(result['images'][0].startswith('/'))
                stored=self.f.ok('A','GET',SETTINGS)['providers'][0]
                self.assertEqual(stored['image_request_mode'],'openai-responses')
                self.assertEqual(stored['model_protocols']['image|novel-model-v2030'],protocol)

    def test_wrong_purpose_adapter_is_visible_and_rejected_without_network(self):
        for purpose,adapter in [('llm','tudou-sora2'),('image','agnes-video'),('video','openai-json')]:
            saved=self.save(purpose=purpose,model_adapters={purpose+'|novel-model-v2030':adapter})
            cap=saved['capabilities'][purpose]['novel-model-v2030']
            self.assertFalse(cap['executable'])
            self.assertEqual(cap['reason'],'未适配此图片请求模式' if purpose=='image' else '此用途没有所选适配器的接口契约')
            before=len(self.f.mock.calls)
            path='/api/canvas-llm' if purpose=='llm' else '/api/online-image' if purpose=='image' else '/api/canvas-video'
            payload=dict(provider='same-personal-id',model='novel-model-v2030',message='garment') if purpose=='llm' else dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='garment')
            self.assertEqual(self.f.request('A','POST',path,json=payload).status_code,403)
            self.assertEqual(len(self.f.mock.calls),before)
        saved=self.save(protocol='runninghub',name='app:2030',purpose='llm',rh_apps=[dict(id='2030',purpose='llm',fields=[dict(nodeId='1',fieldName='text',role='prompt',type='text',enabled=True)])])
        self.assertFalse(saved['capabilities']['llm']['app:2030']['executable'])
        self.assertIn('文本结果契约尚未实现',saved['capabilities']['llm']['app:2030']['reason'])
        before=len(self.f.mock.calls)
        self.assertEqual(self.f.request('A','POST','/api/canvas-llm',json=dict(provider='same-personal-id',model='app:2030',message='garment')).status_code,403)
        self.assertEqual(len(self.f.mock.calls),before)

    def test_runninghub_derived_video_use_does_not_remove_existing_image_use(self):
        fields=[dict(nodeId='10',fieldName='text',type='text',role='prompt',enabled=True)]
        saved=self.save(protocol='runninghub',name='workflow:2030',model_adapters={},rh_workflows=[dict(id='2030',purpose='video',fields=fields)])
        self.assertEqual(saved['image_models'],['workflow:2030'])
        self.assertEqual(saved['video_models'],['workflow:2030'])
        self.assertTrue(all(saved['capabilities'][purpose]['workflow:2030']['executable'] for purpose in ('image','video')))

    def test_future_volcengine_audio_does_not_inherit_seedance_preset_limits(self):
        import wave
        from io import BytesIO
        audio=BytesIO()
        with wave.open(audio,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000)
            wav.writeframes(b'\x00\x00'*8000*16)
        url=self.f.ok('A','POST','/api/ai/upload',files={'files':('reference.wav',audio.getvalue(),'audio/wav')})['files'][0]['url']
        self.save(protocol='volcengine',purpose='video')
        result=self.f.ok('A','POST','/api/canvas-video',json=dict(provider_id='same-personal-id',model='novel-model-v2030',prompt='audio garment',audios=[url],multimodal=True,duration=8,aspect_ratio='9:16',resolution='720p'))
        self.assertTrue(result['videos'][0].startswith('/'))
        call=next(c for c in self.f.mock.calls if c[0]=='network-submit')
        self.assertEqual(sum(item.get('type')=='audio_url' for item in call[2]['body']['content']),1)

if __name__=='__main__':unittest.main()
