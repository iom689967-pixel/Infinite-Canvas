"""Full UI resources and identity isolation through real authenticated ASGI processes."""
import json
from pathlib import Path
import re
import subprocess
import unittest
from types import SimpleNamespace

from instance_frontend import authenticated_html, frontend_context
from instance_access import WORKBENCH_STATIC, PUBLIC_STATIC
import test_instance_own_providers as provider_fixtures


class FrontendContextTests(unittest.TestCase):
    def test_namespace_is_user_and_instance_bound_without_secret_html(self):
        paths=SimpleNamespace(data_root=Path('/private/test-root'),instance_id='A')
        principal={'username':'alice','permissions':['manage_own_providers'],'csrf':'SECRET','token':'SECRET'}
        html=authenticated_html('<html><head><script>localStorage.getItem("prompt")</script></head></html>',paths,principal)
        self.assertNotIn('SECRET',html);self.assertNotIn('/private/test-root',html)
        self.assertLess(html.index('instance-storage.js'),html.index('localStorage.getItem'))
        self.assertEqual(authenticated_html(html,paths,principal),html)
        self.assertNotEqual(frontend_context(paths,principal)['storage_namespace'],frontend_context(paths,{**principal,'username':'bob'})['storage_namespace'])
        self.assertNotEqual(frontend_context(paths,principal)['storage_namespace'],frontend_context(SimpleNamespace(data_root=Path('/private/other'),instance_id='A'),principal)['storage_namespace'])

    def test_shared_origin_storage_lifecycle(self):
        subprocess.run(['node','tests/test_instance_storage.js'],check=True,capture_output=True)
        subprocess.run(['node','tests/test_instance_session.js'],check=True,capture_output=True)

    def test_legacy_workspace_is_only_a_redirect(self):
        html=Path('static/instance-workspace.html').read_text()
        self.assertIn('url=/',html);self.assertNotIn('<iframe',html)
        self.assertFalse(Path('static/instance-index.html').exists())

    def test_shared_translation_loader_resources_are_authenticated_workbench_assets(self):
        resources=re.findall(r"['\"](/static/js/[^'\"]+\.js)['\"]",Path('static/js/i18n.js').read_text())
        self.assertGreater(len(resources),5)
        for resource in resources:
            self.assertIn(resource,WORKBENCH_STATIC)
            self.assertNotIn(resource,PUBLIC_STATIC)


class FullWorkspaceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture=provider_fixtures.OwnProviderTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_same_full_home_and_authenticated_resource_inventory(self):
        f=self.fixture
        for name in ('A','B'):
            html=f.request(name,'GET','/').text
            self.assertIn('id="studioSidebar"',html)
            self.assertIn('id="frame-canvas"',html)
            self.assertIn('instance-context',html)
            self.assertNotIn('id="instance-frame"',html)
            for path in WORKBENCH_STATIC:
                response=f.request(name,'GET',path)
                self.assertEqual(response.status_code,200,path)
            me=f.ok(name,'GET','/api/auth/me')
            for endpoint in ('/api/canvases', '/api/config', '/api/providers', '/api/models'):
                self.assertEqual(f.request(name,'GET',endpoint).headers['x-instance-namespace'],me['storage_namespace'])
        self.assertNotEqual(f.ok('A','GET','/api/auth/me')['storage_namespace'],f.ok('B','GET','/api/auth/me')['storage_namespace'])
        self.assertIn('instance-storage.js',f.request('A','GET','/static/api-settings.html').text)
        self.assertEqual(f.request('B','GET','/static/api-settings.html').status_code,403)
        for name in ('A','B'):
            for method,path in [('GET','/api/app-info'),('POST','/api/update-rollback'),('PUT','/api/providers'),('GET','/api/comfyui/instances')]:
                self.assertEqual(f.request(name,method,path).status_code,403)

    def test_logout_blocks_all_private_pages_resources_and_media(self):
        import httpx
        f=self.fixture
        reference=f.upload('A')
        f.ok('A','POST','/api/auth/logout')
        for path in ['/', '/static/index.html','/static/canvas.html','/api/canvases','/api/instance/providers',reference]:
            self.assertEqual(f.request('A','GET',path).status_code,401,path)
        with httpx.Client(trust_env=False) as client:
            origin=f'http://127.0.0.1:{f.ports["A"]}'
            r=client.get(origin+'/',headers={'Accept':'text/html'})
            self.assertEqual(r.status_code,303);self.assertEqual(r.headers['location'],'/login')
            for path in PUBLIC_STATIC:
                self.assertEqual(client.get(origin+path).status_code,200)
