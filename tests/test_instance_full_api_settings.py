"""Full editor contract through real A/B processes; only local mock traffic."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import unittest
from instance_auth import AuthStore
import test_instance_own_providers as fixtures

API='/api/instance/provider-settings'
class CatalogMock(fixtures.PersonalMock):
    def do_GET(self):
        if self.path.endswith('/tasks/healthcheck_probe_do_not_submit'):
            self.server.calls.append(('probe',self.identity(),{}))
            if self.path.startswith('/async/'):
                return self.reply({'error':{'message':'invalid task id'}},400)
            return self.reply({},404)
        if self.path.endswith('/models') and '/redirect/' not in self.path:
            if not self.identity(): return self.reply({},401)
            self.server.calls.append(('discover',self.identity(),{}))
            if '/v1beta/' in self.path:
                return self.reply({'models':[{'name':'models/gemini-text'},{'name':'models/gemini-image'}]})
            return self.reply({'data':[{'id':'gpt-chat'},{'id':'gpt-image-2'},{'id':'veo-video'}]})
        return super().do_GET()

class FullSettingsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.OwnProviderTests()
        with patch.object(fixtures,'PersonalMock',CatalogMock): self.f.setUp()
        self.addCleanup(self.f.doCleanups)
    def item(self,protocol='kie',**changes):
        return dict(id='personal-full',name='Full UI',base_url=self.f.mock.origin,protocol=protocol,
                    image_request_mode='openai',image_edit_route='general',enabled=True,
                    image_models=['gpt-image-2','nano-banana-pro'] if protocol=='kie' else [],
                    chat_models=[],video_models=[],**changes)
    def save(self,item):return self.f.ok('A','PUT',API,json=[item])['providers'][0]
    def test_five_protocol_and_mode_combinations_persist_with_same_schema(self):
        for protocol,mode,image,chat in [('openai','openai',[],['gpt-chat']),('openai','openai-json',['gpt-image-2'],[]),('gemini','openai',['gemini-image'],['gemini-text']),('kie','openai',['gpt-image-2','nano-banana-pro'],[]),('apimart','openai-video-proxy',['gpt-image-2'],[])]:
            with self.subTest(protocol=protocol,mode=mode):
                item=self.item(protocol);item.update(image_request_mode=mode,image_models=image,chat_models=chat,video_models=['veo-video'] if protocol!='kie' else [],image_edit_route='chat',api_key=self.f.keys['A'])
                p=self.save(item)
                for k in ('protocol','image_request_mode','image_edit_route','image_models','chat_models','video_models'):self.assertEqual(p[k],item[k])
                self.assertTrue(p['has_key']);self.assertNotIn('api_key',p)
                self.assertNotIn(self.f.keys['A'],json.dumps(p))
                self.assertEqual(self.f.ok('A','GET',API)['providers'][0],p)
    def test_kie_restart_catalog_and_identity_are_stable(self):
        p=self.save(self.item(api_key=self.f.keys['A']))
        self.assertEqual(p['runnable_models'],['gpt-image-2','nano-banana-pro'])
        self.f.stop('A');self.f.start('A')
        self.assertEqual(self.f.ok('A','GET',API)['providers'][0],p)
        catalog=self.f.ok('A','GET','/api/models')
        self.assertIn('gpt-image-2',catalog['image_models'])
        self.assertEqual(self.f.mock.calls,[])
    def test_replace_preserve_clear_and_target_change_are_atomic(self):
        item=self.item(api_key=self.f.keys['A']);self.save(item);item.pop('api_key')
        self.assertTrue(self.save(item)['has_key'])
        changed=item|{'image_edit_route':'auto'}
        self.assertEqual(self.f.request('A','PUT',API,json=[changed]).status_code,409)
        self.assertTrue(self.f.ok('A','GET',API)['providers'][0]['has_key'])
        self.assertTrue(self.save(item|{'api_key':'replacement-fake-key'})['has_key'])
        self.assertFalse(self.save(item|{'clear_key':True})['has_key'])
        self.assertEqual(self.f.ok('A','GET',API)['providers'][0]['runnable_models'],[])
    def test_full_schema_auxiliary_credentials_and_fields_do_not_leak(self):
        item=self.item('volcengine',api_key=self.f.keys['A']);item.update(volcengine_access_key_id='fake-ak',volcengine_secret_access_key='fake-sk',volcengine_project_name='project',volcengine_region='cn-beijing',model_names={'gpt-chat':'Chat'},model_protocols={'gpt-chat':'openai'},chat_models=['gpt-chat'])
        p=self.save(item);self.assertTrue(p['has_volcengine_access_key']);self.assertTrue(p['has_volcengine_secret_key'])
        self.assertNotIn('fake-sk',json.dumps(p));self.assertEqual(p['volcengine_project_name'],'project')
        for k in ['api_key','volcengine_access_key_id','volcengine_secret_access_key']:item.pop(k)
        item['volcengine_project_name']='new-project'
        self.assertEqual(self.f.request('A','PUT',API,json=[item]).status_code,409)
    def test_shared_editor_served_without_simplified_script(self):
        html=self.f.request('A','GET','/static/api-settings.html').text
        self.assertIn('/static/js/api-settings.js',html);self.assertNotIn('/static/js/instance-api-settings.js',html)
        for ident in ['protocolInput','imageRequestModeInput','imageModelList','chatModelList','videoModelList','modelPickerOverlay']:self.assertIn('id="'+ident+'"',html)
        self.assertNotIn('ownModels',html)
    def test_ab_permissions_and_private_store_isolation(self):
        self.save(self.item(api_key=self.f.keys['A']))
        self.assertEqual(self.f.request('B','GET',API).status_code,403)
        self.f.stop('B');AuthStore(self.f.roots['B'],'B').set_provider_permission('B',True);self.f.start('B')
        self.assertEqual(self.f.ok('B','GET',API)['providers'],[])
        self.f.ok('B','PUT',API,json=[self.item(api_key=self.f.keys['B'])])
        self.assertTrue(self.f.ok('A','GET',API)['providers'][0]['has_key'])
        self.f.ok('B','PUT',API,json=[])
        self.assertTrue(self.f.ok('A','GET',API)['providers'][0]['has_key'])
        self.f.ok('A','POST','/api/auth/logout');self.assertEqual(self.f.request('A','GET',API).status_code,401)
    def test_csrf_origin_cli_and_cross_host_remain_denied(self):
        item=self.item(api_key=self.f.keys['A'])
        for headers in [{'Origin':'http://evil.example'},{'X-CSRF-Token':''}]:
            self.assertEqual(self.f.request('A','PUT',API,json=[item],headers=headers).status_code,403)
        self.assertEqual(self.f.request('A','PUT',API,json=[item|{'protocol':'codex'}]).status_code,403)
        self.assertEqual(self.f.request('A','PUT',API,json=[item|{'image_edit_endpoint':'https://evil.example/edits'}]).status_code,400)
        self.assertEqual(self.f.mock.calls,[])
    def test_readonly_discovery_classification_and_protocol_detection(self):
        self.save(self.item('openai',api_key=self.f.keys['A']))
        for action in ['test-connection','probe-async','fetch-models']:
            data=self.f.ok('A','POST',API+'/'+action,json={'provider_id':'personal-full','base_url':self.f.mock.origin,'protocol':'openai','image_request_mode':'openai-json'})
            self.assertTrue(data['ok']);self.assertEqual(data['protocol'],'openai');self.assertEqual(data['image_models'],['gpt-image-2']);self.assertEqual(data['chat_models'],['gpt-chat']);self.assertEqual(data['video_models'],['veo-video']);self.assertEqual(data['image_request_mode'],'openai-json')
        self.assertTrue(all(x[0] in {'discover','probe'} for x in self.f.mock.calls))
    def test_gemini_and_kie_discovery_are_readonly(self):
        self.save(self.item('gemini',api_key=self.f.keys['A']))
        data=self.f.ok('A','POST',API+'/fetch-models',json={'provider_id':'personal-full','base_url':self.f.mock.origin,'protocol':'gemini'})
        self.assertEqual(data['protocol'],'gemini');self.assertEqual(data['chat_models'],['gemini-text'])
        self.save(self.item('kie',api_key=self.f.keys['A']));before=len(self.f.mock.calls)
        data=self.f.ok('A','POST',API+'/fetch-models',json={'provider_id':'personal-full','base_url':self.f.mock.origin,'protocol':'kie'})
        self.assertEqual(data['image_models'],['gpt-image-2','nano-banana-pro']);self.assertEqual(len(self.f.mock.calls),before)
    def test_probe_does_not_forward_stored_key_after_target_change(self):
        self.save(self.item('openai',api_key=self.f.keys['A']))
        p={'provider_id':'personal-full','base_url':self.f.mock.origin+'/changed','protocol':'openai'}
        self.assertEqual(self.f.request('A','POST',API+'/fetch-models',json=p).status_code,409)
        self.assertEqual(self.f.mock.calls,[])
    def test_redirect_and_private_destination_probe_rejected(self):
        item=self.item('openai',api_key=self.f.keys['A']);item['base_url']=self.f.mock.origin+'/redirect';self.save(item)
        body={'provider_id':'personal-full','base_url':item['base_url'],'protocol':'openai'}
        self.assertEqual(self.f.request('A','POST',API+'/fetch-models',json=body).status_code,502)
        body.update(base_url='http://127.0.0.1:3000',api_key=self.f.keys['A'])
        self.assertEqual(self.f.request('A','POST',API+'/fetch-models',json=body).status_code,400)
    def test_failed_collection_save_does_not_change_or_orphan_secrets(self):
        self.save(self.item(api_key=self.f.keys['A']));directory=self.f.roots['A']/'.auth/credentials';before=set(directory.iterdir())
        invalid=self.item(api_key='uncommitted-fake-key');invalid['id']='second';bad=self.item();bad['protocol']='shell'
        self.assertEqual(self.f.request('A','PUT',API,json=[invalid,bad]).status_code,403)
        self.assertEqual(set(directory.iterdir()),before)
        self.assertEqual(self.f.ok('A','GET',API)['providers'][0]['id'],'personal-full')
    def test_browser_script_preserves_draft_and_failed_probe_protocol(self):
        import subprocess
        subprocess.run(['node','tests/test_api_settings_shared.js'],cwd=Path(__file__).resolve().parents[1],check=True,capture_output=True)

    def test_full_script_and_edit_fields_are_authenticated_resources(self):
        self.assertEqual(self.f.request('A','GET','/static/js/api-settings.js').status_code,200)
        self.assertEqual(self.f.request('B','GET','/static/js/api-settings.js').status_code,403)
        html=self.f.request('A','GET','/static/api-settings.html').text
        for field in ['imageEditRouteInput','providerEnabledInput','providerPrimaryInput','imageGenerationEndpointInput','imageEditEndpointInput']:
            self.assertIn('id="'+field+'"',html)
        self.f.ok('A','POST','/api/auth/logout')
        self.assertEqual(self.f.request('A','GET','/static/js/api-settings.js').status_code,401)

    def test_legacy_update_preserves_full_fields_and_auxiliary_key_cleanup(self):
        item=self.item('volcengine',api_key=self.f.keys['A'],volcengine_secret_access_key='fake-private-sk')
        item.update(image_request_mode='openai-json',image_edit_route='chat',volcengine_project_name='keep-project',model_names={'gpt-chat':'Keep Alias'})
        self.save(item)
        legacy=self.f.ok('A','GET','/api/instance/providers')['providers'][0]
        legacy={k:legacy[k] for k in ['id','name','base_url','protocol','models','enabled']};legacy['name']='Legacy renamed'
        self.f.ok('A','PUT','/api/instance/providers',json=legacy)
        restored=self.f.ok('A','GET',API)['providers'][0]
        for field in ['image_request_mode','image_edit_route','volcengine_project_name','model_names']:
            self.assertEqual(restored[field],item[field])
        self.assertTrue(restored['has_volcengine_secret_key'])
        self.f.ok('A','DELETE','/api/instance/providers',json={'id':item['id']})
        self.assertEqual(list((self.f.roots['A']/'.auth/credentials').glob('personal-*.key')),[])

    def test_official_kie_base_fake_key_static_catalog_zero_network(self):
        self.f.stop('A')
        config=self.f.roots['A']/'.auth/model-access.json'
        value=json.loads(config.read_text());value.update(mode='live',providers=[],personal_providers=[])
        config.write_text(json.dumps(value));self.f.start('A')
        item=self.item(api_key='fake-official-kie-key');item['base_url']='https://api.kie.ai'
        saved=self.save(item)
        for action in ['probe-async','fetch-models']:
            result=self.f.ok('A','POST',API+'/'+action,json={'provider_id':item['id'],'base_url':item['base_url'],'protocol':'kie'})
            self.assertTrue(result['ok']);self.assertEqual(result['image_models'],item['image_models'])
        self.f.stop('A');self.f.start('A')
        restored=self.f.ok('A','GET',API)['providers'][0]
        self.assertEqual(restored,saved);self.assertTrue(restored['has_key'])
        self.assertEqual(restored['runnable_models'],item['image_models']);self.assertEqual(self.f.mock.calls,[])

    def test_async_protocol_detection_uses_only_nonexistent_task_get(self):
        item=self.item('openai',api_key=self.f.keys['A']);item['base_url']+='/async';self.save(item)
        result=self.f.ok('A','POST',API+'/probe-async',json={'provider_id':item['id'],'base_url':item['base_url'],'protocol':'openai'})
        self.assertTrue(result['ok']);self.assertEqual(result['protocol'],'apimart')
        self.assertEqual([call[0] for call in self.f.mock.calls],['discover','probe'])

    def test_generic_404_does_not_prove_async_protocol(self):
        self.save(self.item('apimart',api_key=self.f.keys['A']))
        result=self.f.ok('A','POST',API+'/probe-async',json={'provider_id':'personal-full','base_url':self.f.mock.origin,'protocol':'apimart'})
        self.assertFalse(result['ok']);self.assertEqual(result['protocol'],'apimart')
        self.assertTrue(all(call[0] in {'discover','probe'} for call in self.f.mock.calls))
