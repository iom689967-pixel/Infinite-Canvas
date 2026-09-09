"""Real loopback HTTP/1.1 fixtures through the production GuardedClient reader."""
import asyncio
import contextlib
import gzip
from io import StringIO
import json
from types import SimpleNamespace
import unittest

import httpx

from instance_model_policy import GuardedClient
from instance_reference_diagnostics import ReferenceDiagnostics
from providers.kie.uploads import _upload_normalized_image, KieReferenceError


class ResponseReaderTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode, *, upload=False):
        body = json.dumps({'code':200,'success':True,'data':{'downloadUrl':'https://cdn.example/image.png?signature=SECRET'}}).encode()
        handlers = set()
        async def serve(reader, writer):
            task = asyncio.current_task(); handlers.add(task)
            try:
                header = await reader.readuntil(b'\r\n\r\n')
                length = next((int(line.split(b':',1)[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
                await reader.readexactly(length)
                wire = gzip.compress(body) if mode in {'gzip','bad-gzip'} else body
                if mode == 'bad-gzip': wire = b'not-gzip-SECRET'
                if mode == 'oversize': wire = gzip.compress(b'x'*(32*1024*1024+1))
                fields = [b'HTTP/1.1 200 OK', b'Content-Type: application/json',b'Connection: close']
                if mode in {'gzip','bad-gzip','oversize'}: fields.append(b'Content-Encoding: gzip')
                if mode == 'chunked': fields.append(b'Transfer-Encoding: chunked')
                elif mode != 'no-length': fields.append(b'Content-Length: '+str(len(wire)+10 if mode=='interrupted' else len(wire)).encode())
                writer.write(b'\r\n'.join(fields)+b'\r\n\r\n'); await writer.drain()
                if mode == 'timeout': await asyncio.sleep(1)
                if mode == 'chunked':
                    for chunk in [wire[:10],wire[10:]]:
                        writer.write(hex(len(chunk))[2:].encode()+b'\r\n'+chunk+b'\r\n')
                    writer.write(b'0\r\n\r\n')
                else: writer.write(wire)
                await writer.drain()
            except (ConnectionError, asyncio.CancelledError): pass
            finally:
                writer.close()
                with contextlib.suppress(ConnectionError): await writer.wait_closed()
                handlers.discard(task)
        server = await asyncio.start_server(serve,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]; host=f'http://127.0.0.1:{port}'
        policy=SimpleNamespace(mode='mock',paths=SimpleNamespace(upstreams={('127.0.0.1',port)}),credential=lambda p:'KEY-SECRET')
        provider={'personal':True,'base_url':host,'upload_base_url':host}
        client=GuardedClient(policy,provider,timeout=.1 if mode=='timeout' else 5)
        diagnostic=ReferenceDiagnostics();client.reference_diagnostic=diagnostic
        output=StringIO(); result=None; error=None
        try:
            with contextlib.redirect_stdout(output):
                try:
                    if upload:
                        result=await _upload_normalized_image(client,'KEY-SECRET',b'fixture',{'extension':'.png','mime_type':'image/png'},index=1,filename='SECRET')
                    else:
                        result=await client.post(host+'/response?token=SECRET',headers={'Authorization':'Bearer KEY-SECRET'},content=b'PAYLOAD-SECRET')
                except Exception as exc:
                    error=exc;diagnostic.failed(exc)
        finally:
            await client.aclose();server.close();await server.wait_closed()
            for task in list(handlers): task.cancel()
            if handlers: await asyncio.gather(*list(handlers),return_exceptions=True)
        logs=output.getvalue()
        for secret in ['KEY-SECRET','PAYLOAD-SECRET','signature=SECRET','token=SECRET',host]: self.assertNotIn(secret,logs)
        return result,error,logs

    async def success(self, mode):
        response,error,logs=await self.exercise(mode)
        self.assertIsNone(error)
        self.assertEqual(response.json()['code'],200)
        self.assertTrue(response.is_closed)
        self.assertNotIn('content-encoding',response.headers)
        self.assertNotIn('transfer-encoding',response.headers)
        self.assertEqual(int(response.headers['content-length']),len(response.content))
        return response,logs

    async def test_content_length_json(self): await self.success('length')
    async def test_chunked_json(self): await self.success('chunked')
    async def test_gzip_json(self): await self.success('gzip')
    async def test_no_content_length_json(self): await self.success('no-length')
    async def test_small_json(self): await self.success('small')
    async def test_interrupted_body(self):
        _,error,logs=await self.exercise('interrupted',upload=True)
        self.assertIsInstance(error,KieReferenceError)
        self.assertIsInstance(error.__cause__,httpx.RemoteProtocolError)
    async def test_timeout(self):
        _,error,_=await self.exercise('timeout',upload=True)
        self.assertIsInstance(error,KieReferenceError)
        self.assertIsInstance(error.__cause__,httpx.ReadTimeout)
    async def test_size_limit(self):
        _,error,_=await self.exercise('oversize')
        self.assertEqual(error.status_code,502)
    async def test_upload_json_after_read(self):
        result,error,_=await self.exercise('gzip',upload=True)
        self.assertIsNone(error)
        self.assertEqual(result[1],200)
    async def test_exception_classes_only(self):
        _,_,logs=await self.exercise('interrupted',upload=True)
        self.assertIn('httpx.RemoteProtocolError',logs)
        self.assertIn('httpcore.RemoteProtocolError',logs)
        self.assertNotIn('peer closed',logs)
    async def test_invalid_compression(self):
        _,error,logs=await self.exercise('bad-gzip',upload=True)
        self.assertIsInstance(error.__cause__,httpx.DecodingError)
    async def test_metadata(self):
        response,logs=await self.success('length')
        self.assertIn('"headers_received": true',logs)
        self.assertIn('"body_read_started": true',logs)
        self.assertEqual(response.http_version,'HTTP/1.1')
