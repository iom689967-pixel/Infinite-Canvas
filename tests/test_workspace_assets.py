import subprocess
import tempfile
import unittest
from pathlib import Path
from workspace_assets import ProgramAssets, program_assets, PRIVATE_CACHE, IMMUTABLE_CACHE, SHORT_CACHE

ROOT=Path(__file__).resolve().parents[1]

class WorkspaceAssetsTests(unittest.TestCase):
    def setUp(self):
        self.assets=program_assets(str(ROOT/'static'))

    def test_actual_prewarmer_scheduler_and_stable_iframe_identity(self):
        subprocess.run(['node','tests/test_workspace_loading.js'],check=True,capture_output=True)

    def test_parallel_reads_csrf_writes_and_shared_session(self):
        subprocess.run(['node','tests/test_instance_session.js'],check=True,capture_output=True)

    def test_only_exact_program_inventory_is_cacheable(self):
        for path in ['/api/auth/me','/api/canvases','/api/projects','/api/history','/api/conversations',
                     '/api/instance/provider-settings','/api/canvas-image-tasks/a','/assets/photo.png',
                     '/output/result.jpg','/static/runninghub/api_providers.json','/static/js/unknown.js',
                     '/static/js/../private.js','/static/api-settings.html','/static/smart-canvas.html']:
            self.assertEqual(self.assets.cache_control(path,'v='+self.assets.version,'GET',200),PRIVATE_CACHE,path)

    def test_immutable_requires_current_content_version(self):
        path='/static/js/theme.js'
        self.assertEqual(self.assets.cache_control(path,'v='+self.assets.version,'GET',200),IMMUTABLE_CACHE)
        for query in ('','v=old','v='+self.assets.version+'&v=old'):
            self.assertEqual(self.assets.cache_control(path,query,'GET',200),SHORT_CACHE)

    def test_failures_writes_and_cookies_never_public(self):
        for method,status,cookie in [('GET',401,False),('GET',403,False),('GET',500,False),('POST',200,False),('GET',200,True)]:
            self.assertEqual(self.assets.cache_control('/static/js/theme.js','v='+self.assets.version,method,status,cookie),PRIVATE_CACHE)

    def test_css_fonts_are_fingerprinted_and_etag_matches_representation(self):
        data,etag=self.assets.representation('/static/vendor/css/fonts.css')
        self.assertIn(('?v='+self.assets.version).encode(),data)
        import hashlib
        self.assertEqual(etag,'"'+hashlib.sha256(data).hexdigest()+'"')

    def test_html_rewrites_scripts_images_bootstrap_and_keeps_html_revalidatable(self):
        text='<head></head><script src="/static/js/instance-session.js"></script><img src="/static/images/logo.png?v=old"><iframe data-src="/static/canvas.html?v=old"></iframe>'
        result=self.assets.html(text)
        self.assertEqual(result.count('?v='+self.assets.version),2)
        self.assertIn('mio-asset-version',result)
        self.assertIn('/static/canvas.html?v=old',result)

    def test_same_bytes_same_version_and_dependency_changes_bust_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'js').mkdir();path=root/'js/theme.js';path.write_text('old')
            a=ProgramAssets(root);b=ProgramAssets(root)
            self.assertEqual(a.version,b.version)
            import os
            stat=path.stat();path.write_text('new');os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            self.assertNotEqual(a.version,ProgramAssets(root).version)

    def test_symlink_is_not_program_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'js').mkdir();private=root/'private';private.write_text('SECRET')
            (root/'js/theme.js').symlink_to(private)
            self.assertNotIn('/static/js/theme.js',ProgramAssets(root).files)
