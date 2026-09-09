"""Offline contract fixtures, NOT captured real upload responses; never create tasks."""
import contextlib
from io import BytesIO, StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import httpx
from PIL import Image

from instance_model_policy import GuardedClient
from instance_reference_diagnostics import ReferenceDiagnostics
from providers.kie.uploads import KieReferenceUploadCache, prepare_kie_references


class ReferenceDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, fmt='PNG', field='downloadUrl', invalid=False, redirect=False, malformed=False):
        calls = []
        buffer = BytesIO(); Image.new('RGB', (32, 32), 'green').save(buffer, fmt)
        image = buffer.getvalue()
        media_type = 'image/png' if fmt == 'PNG' else 'image/jpeg'
        host = 'http://127.0.0.1:43219'
        provider = {'personal':True, 'base_url':host, 'upload_base_url':host, 'credential_file':'fake'}
        policy = SimpleNamespace(mode='mock', paths=SimpleNamespace(upstreams={('127.0.0.1',43219)}), credential=lambda p:'fake-secret')
        client = GuardedClient(policy, provider)
        await client.client.aclose()
        def respond(request):
            calls.append((request.method, request.url.path))
            if request.method == 'POST':
                if redirect:
                    return httpx.Response(302, headers={'Location':'http://169.254.169.254/secret'})
                if malformed:
                    return httpx.Response(200, headers={'Content-Type':'text/html'}, text='<secret>')
                data = {field:host+'/media.png?signature=private-value'} if field else {}
                return httpx.Response(200, json={'success':True, 'code':200, 'data':data})
            return httpx.Response(200, headers={'Content-Type':'text/html' if invalid else media_type}, content=b'not-image-secret' if invalid else image)
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False)
        diagnostic = ReferenceDiagnostics(); client.reference_diagnostic = diagnostic
        log = StringIO(); error = None; urls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'private-filename'; path.write_bytes(image)
            with contextlib.redirect_stdout(log):
                try:
                    urls, _ = await prepare_kie_references('fake-secret', [{'url':'/assets/input/test','name':'private-filename'}],
                        resolve_local_path=lambda u:str(path), max_bytes=1048576,
                        cache=KieReferenceUploadCache(Path(directory)/'cache.json'), http_client=client)
                except Exception as exc:
                    error = exc; diagnostic.failed(exc)
            await client.aclose()
        self.assertFalse(any('createTask' in path for _,path in calls))
        for sensitive in ('fake-secret','private-filename','private-value','<secret>','not-image-secret','Authorization','Cookie',directory):
            self.assertNotIn(sensitive, log.getvalue())
        return urls, error, calls, log.getvalue()

    async def test_png_jpeg_and_both_documented_url_fields(self):
        for fmt in ('PNG','JPEG'):
            for field in ('downloadUrl','fileUrl'):
                urls,error,calls,log = await self.exercise(fmt,field)
                self.assertIsNone(error)
                self.assertEqual(len(urls),1)
                self.assertIn('"has_'+('download_url' if field=='downloadUrl' else 'file_url')+'": true',log)

    async def test_missing_field_and_non_json_are_explicit_stages(self):
        for kwargs,stage in [({'field':''},'extract'),({'malformed':True},'json')]:
            _,error,_,log=await self.exercise(**kwargs)
            self.assertIsNotNone(error)
            last=json.loads(log.splitlines()[-1]);self.assertEqual(last['stage'],stage)

    async def test_invalid_media_and_private_redirect_do_not_create(self):
        for kwargs in ({'invalid':True},{'redirect':True}):
            _,error,calls,log=await self.exercise(**kwargs)
            self.assertIsNotNone(error)
            if kwargs.get('redirect'):
                self.assertEqual(len(calls),1)
                self.assertEqual(json.loads(log.splitlines()[-1])['stage'],'redirect')

    async def test_diagnostic_rejects_untrusted_fields(self):
        out=StringIO();d=ReferenceDiagnostics()
        with contextlib.redirect_stdout(out):
            d('response', http_status=200, media_type='secret-value', payload='secret-value', url='secret-value')
            d('secret-value')
            d.failed(type('SecretType', (Exception,), {})('secret-value'))
        self.assertNotIn('secret-value',out.getvalue()); self.assertNotIn('SecretType',out.getvalue())
