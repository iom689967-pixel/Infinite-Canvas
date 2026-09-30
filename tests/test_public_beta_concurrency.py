"""Ten-slot acceptance with real isolated workers and loopback-only held upstreams."""
import asyncio
import base64
from collections import Counter
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from instance_model_policy import ModelPolicy
from public_beta import create_app
from public_beta_store import BetaConfig
from test_instance_isolation import free_port
from test_personal_network_adapters import NetworkMock


class HeldMock(NetworkMock):
    def do_POST(self):
        kinds = {'/openai/images/generations': 'image',
                 '/openai/videos': 'video', '/openai/videos/generations': 'video',
                 '/openai/chat/completions': 'llm'}
        kind = kinds.get(self.path.replace('/openai/v1/', '/openai/', 1))
        if not kind:
            return self.reject('unexpected route')
        owner = self.identity()
        if not owner:
            return self.reply({}, 401)
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tag = body['messages'][-1]['content'] if kind == 'llm' else body['prompt']
        if isinstance(tag, list):
            tag = ''.join(part['text'] for part in tag if part['type'] == 'text')
        with self.server.condition:
            self.server.accepted.append((owner, kind, tag))
            self.server.held += 1
            self.server.peak_held = max(self.server.peak_held, self.server.held)
            released = self.server.condition.wait_for(
                lambda: self.server.release_all or tag in self.server.released, timeout=50)
            self.server.held -= 1
            if not released:
                self.server.timed_out += 1
        if not released:
            return self.reply({}, 504)
        if kind == 'llm':
            return self.reply({'choices': [{'message': {'content': 'MOCK completed'}}]})
        if kind == 'video':
            with self.server.condition:
                tid = 'mock-' + str(len(self.server.jobs))
                self.server.jobs[tid] = dict(owner=owner, video=True)
            return self.reply({'id': tid, 'task_id': tid, 'status': 'queued'})
        return self.reply({'data': [{'b64_json': base64.b64encode(self.server.image).decode()}]})


class ConcurrencyConfigTests(unittest.TestCase):
    def test_default_environment_and_explicit_integer_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            env = {'PUBLIC_BETA_ROOT': str(root/'gateway'),
                   'PUBLIC_BETA_INSTANCES_ROOT': str(root/'instances'),
                   'PUBLIC_BETA_BACKUP_ROOT': str(root/'backups')}
            with patch.dict(os.environ, env, clear=True):
                cfg = BetaConfig.from_env()
                self.assertEqual(cfg.concurrency, 10)
                self.assertEqual(cfg.max_running_instances, 4)
                for value in range(1, 11):
                    with patch.dict(os.environ, {'MAX_CONCURRENT_GENERATIONS': str(value)}):
                        self.assertEqual(BetaConfig.from_env().concurrency, value)
                for value in ('0', '11', '-1', '1.5', 'True'):
                    with patch.dict(os.environ, {'MAX_CONCURRENT_GENERATIONS': value}):
                        with self.assertRaises(ValueError):
                            BetaConfig.from_env()
            for value in (True, 1.5, '10', 0, 11):
                with self.assertRaises(ValueError):
                    BetaConfig(root/'gateway', root/'instances', concurrency=value)

    def test_both_layers_validate_without_silent_clamping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'.auth').mkdir(mode=0o700)
            model = root/'.auth/model-access.json'
            budget = root/'.auth/public-beta.json'
            paths = SimpleNamespace(data_root=root, public_beta=True, port=free_port())
            def write(first, second):
                model.write_text(json.dumps(dict(schema_version=1, mode='live',
                    max_concurrent=first, providers=[], personal_providers=[])))
                budget.write_text(json.dumps(dict(max_concurrent_generations=second)))
                model.chmod(0o600)
                budget.chmod(0o600)
            for value in range(1, 11):
                write(value, value)
                self.assertEqual(ModelPolicy(paths).max_concurrent, value)
            for first, second in ((10, 2), (8, 10), (10, 10)):
                write(first, second)
                self.assertEqual(ModelPolicy(paths).max_concurrent, min(first, second))
            for bad in (0, 11, -1, True, 1.5, '10'):
                for first, second in ((bad, 10), (10, bad)):
                    write(first, second)
                    with self.assertRaises(RuntimeError):
                        ModelPolicy(paths)


class TenSlotAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mio-ten-slots-')
        self.root = Path(self.temp.name).resolve()
        self.mock = ThreadingHTTPServer(('127.0.0.1', 0), HeldMock)
        self.mock.condition = threading.Condition()
        self.mock.accepted, self.mock.released = [], set()
        self.mock.release_all = False
        self.mock.held = self.mock.peak_held = self.mock.timed_out = 0
        self.mock.calls, self.mock.jobs, self.mock.media = [], {}, {}
        self.mock.credentials = {hashlib.sha256(('synthetic-key-'+n).encode()).hexdigest(): n for n in ('A', 'B')}
        image = BytesIO()
        Image.new('RGB', (32, 48), 'blue').save(image, 'PNG')
        self.mock.image = image.getvalue()
        self.mock.origin = f'http://127.0.0.1:{self.mock.server_port}'
        threading.Thread(target=self.mock.serve_forever, daemon=True).start()
        self.config = BetaConfig(self.root/'gateway', self.root/'instances', port=free_port(),
            min_free_disk=0, mock_upstreams=f'127.0.0.1:{self.mock.server_port}',
            register_limit=100, login_limit=100)
        self.app = create_app(self.config)
        self.supervisor, self.store = self.app.state.supervisor, self.app.state.store
        self.clients, self.accounts, self.inflight = {}, {}, []
        self.password = 'Only-synthetic-registration-42'

    async def asyncTearDown(self):
        self.release()
        if self.inflight:
            await asyncio.gather(*self.inflight, return_exceptions=True)
        for client in self.clients.values():
            await client.aclose()
        await asyncio.to_thread(self.supervisor.close)
        await asyncio.to_thread(self.mock.shutdown)
        self.mock.server_close()
        self.temp.cleanup()

    def release(self, tag=None):
        with self.mock.condition:
            if tag is None:
                self.mock.release_all = True
            else:
                self.mock.released.add(tag)
            self.mock.condition.notify_all()

    async def enter(self, owner):
        c = self.clients[owner]
        c.headers['X-CSRF-Token'] = (await c.get('/api/beta/me')).json()['csrf']
        r = await c.post('/api/beta/enter')
        self.assertEqual(r.status_code, 200, r.text)
        c.headers['X-CSRF-Token'] = (await c.get('/api/auth/me')).json()['csrf']

    async def account(self, owner, enter=True):
        token = await asyncio.to_thread(self.supervisor.register, 'user-'+owner.lower(),
                                       self.password, self.password, 'fixture-'+owner)
        uid = self.store.principal(token)['id']
        self.accounts[owner] = uid
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url=self.config.origin, headers={'Origin': self.config.origin}, timeout=60)
        c.cookies.set('mio_beta_session', token)
        self.clients[owner] = c
        if enter:
            await self.enter(owner)
        return Path(self.store.instance(uid)['data_root'])

    async def provider(self, owner):
        body = dict(id='mock', name='Mock only', protocol='openai', enabled=True,
            base_url=self.mock.origin+'/openai', api_key='synthetic-key-'+owner,
            image_request_mode='openai-json', image_models=['mock-image'],
            chat_models=['mock-text'], video_models=['mock-video'])
        r = await self.clients[owner].put('/api/instance/provider-settings', json=[body])
        self.assertEqual(r.status_code, 200, r.text)

    async def effective(self, owner):
        r = await self.clients[owner].get('/api/instance/providers')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['max_concurrent']

    async def submit(self, owner, kind, tag):
        if kind == 'llm':
            return await self.clients[owner].post('/api/canvas-llm', json={
                'provider': 'mock', 'model': 'mock-text', 'message': tag})
        body = dict(provider_id='mock', model='mock-'+kind, prompt=tag, request_id=tag)
        if kind == 'image':
            body.update(n=1, size='1024x1024')
        else:
            body.update(duration=8, aspect_ratio='9:16', resolution='720p')
        return await self.clients[owner].post('/api/canvas-'+kind+'-tasks', json=body)

    async def until(self, predicate, seconds=30):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            await asyncio.sleep(.025)
        self.fail('Bounded mock condition was not reached; unmatched=' +
                  str(getattr(self.mock, 'unmatched', [])))

    async def completed(self, owner, task_id):
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            r = await self.clients[owner].get('/api/canvas-image-tasks/'+task_id)
            self.assertEqual(r.status_code, 200, r.text)
            job = r.json()
            if job['status'] == 'succeeded':
                return job
            self.assertNotIn(job['status'], ('failed', 'canceled', 'result_recovery_required'), job)
            await asyncio.sleep(.04)
        self.fail('Mock result did not finish writing')

    def resources(self):
        result = {}
        for owner, uid in self.accounts.items():
            pid = self.store.instance(uid)['pid']
            rss = int(subprocess.check_output(['ps', '-p', str(pid), '-o', 'rss='], text=True).strip())
            entry = {'rss_kib': rss}
            fd = Path('/proc')/str(pid)/'fd'
            if fd.exists():
                targets = []
                for p in fd.iterdir():
                    try:
                        targets.append(os.readlink(p))
                    except FileNotFoundError:
                        pass
                entry['open_fds'] = len(targets)
                entry['socket_fds'] = sum(t.startswith('socket:') for t in targets)
            result[owner] = entry
        result['mock_held_connections'] = self.mock.held
        return result

    async def test_ten_mixed_slots_per_account_race_release_results_and_restart(self):
        roots = {}
        for owner in ('A', 'B'):
            roots[owner] = await self.account(owner)
            for filename, field in (('model-access.json', 'max_concurrent'),
                                    ('public-beta.json', 'max_concurrent_generations')):
                self.assertEqual(json.loads((roots[owner]/'.auth'/filename).read_text())[field], 10)
            await self.provider(owner)
            self.assertEqual(await self.effective(owner), 10)
        baseline = self.resources()
        jobs = {'A': [], 'B': []}
        # Twenty simultaneous image submissions: exactly ten can reserve; no await
        # splits the existing single-worker admission check and ledger insert.
        results = await asyncio.gather(*(self.submit('A', 'image', 'race-'+str(i)) for i in range(20)))
        self.assertEqual(Counter(r.status_code for r in results), {200: 10, 429: 10})
        jobs['A'] = [r.json()['task_id'] for r in results if r.status_code == 200]
        await self.until(lambda: len(self.mock.accepted) == 10)
        self.assertEqual(self.mock.held, 10)
        accepted_tags = {tag for _, _, tag in self.mock.accepted}
        self.assertEqual(len(accepted_tags), 10)
        before = len(self.mock.accepted)
        for kind in ('image', 'video', 'llm'):
            r = await self.submit('A', kind, 'rejected-'+kind)
            self.assertEqual(r.status_code, 429, r.text)
            self.assertEqual(r.json()['detail']['code'], 'busy')
        self.assertEqual(len(self.mock.accepted), before)
        # B holds four images, three videos and three LLM connections independently.
        for i, kind in enumerate(['image']*4+['video']*3):
            r = await self.submit('B', kind, 'mixed-'+str(i))
            self.assertEqual(r.status_code, 200, r.text)
            jobs['B'].append(r.json()['task_id'])
        llms = [asyncio.create_task(self.submit('B', 'llm', 'mixed-llm-'+str(i))) for i in range(3)]
        self.inflight.extend(llms)
        await self.until(lambda: len(self.mock.accepted) == 20)
        self.assertEqual(Counter(owner for owner, _, _ in self.mock.accepted), {'A': 10, 'B': 10})
        self.assertEqual(Counter(kind for owner, kind, _ in self.mock.accepted if owner == 'B'),
                         {'image': 4, 'video': 3, 'llm': 3})
        peak = self.resources()
        for kind in ('image', 'video', 'llm'):
            r = await self.submit('B', kind, 'mixed-rejected-'+kind)
            self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(len(self.mock.accepted), 20)
        # Release exactly one confirmed image, then replace that one slot with LLM.
        first_index = next(i for i, r in enumerate(results) if r.status_code == 200)
        self.release('race-'+str(first_index))
        await self.completed('A', results[first_index].json()['task_id'])
        replacement = asyncio.create_task(self.submit('A', 'llm', 'replacement'))
        self.inflight.append(replacement)
        await self.until(lambda: len(self.mock.accepted) == 21)
        r = await self.submit('A', 'image', 'still-full')
        self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(len(self.mock.accepted), 21)
        self.release()
        for response in await asyncio.gather(*self.inflight):
            self.assertEqual(response.status_code, 200, response.text)
        for owner in ('A', 'B'):
            for task_id in jobs[owner]:
                await self.completed(owner, task_id)
        written = {}
        for owner, root in roots.items():
            with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
                rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM jobs')]
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(len(rows), len(jobs[owner]))
            self.assertTrue(all(r['status'] == 'succeeded' and not r['outstanding'] for r in rows))
            media = [p for p in root.rglob('*') if p.is_file() and p.suffix in {'.png', '.mp4'}]
            self.assertGreaterEqual(len(media), len(jobs[owner]))
            self.assertTrue(all(p.stat().st_size > 0 for p in media))
            written[owner] = {'succeeded_jobs': len(rows), 'media_files': len(media),
                              'media_bytes': sum(p.stat().st_size for p in media), 'sqlite_integrity': 'ok'}
        # Both full and legacy Provider saves must preserve the administrator limit.
        for owner in ('A', 'B'):
            await self.provider(owner)
            item = (await self.clients[owner].get('/api/instance/providers')).json()['providers'][0]
            item.pop('has_key')
            item['models'] = [{k: m[k] for k in ('id', 'purpose')} for m in item['models']]
            r = await self.clients[owner].put('/api/instance/providers', json=item)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(await self.effective(owner), 10)
            await asyncio.to_thread(self.supervisor.stop, self.accounts[owner])
            await self.enter(owner)
            self.assertEqual(await self.effective(owner), 10)
        after = self.resources()
        self.assertEqual(self.mock.held, 0)
        self.assertEqual(self.mock.timed_out, 0)
        self.assertEqual(getattr(self.mock, 'unmatched', []), [])
        self.assertFalse(any('upload' in kind for kind, _, _ in self.mock.calls))
        evidence = dict(baseline=baseline, peak=peak, after_restart=after,
            peak_held_mock_connections=self.mock.peak_held, result_writes=written,
            accepted_submissions=len(self.mock.accepted), rejected_upstream_submissions=0,
            real_model_calls=0, real_upstream_uploads=0, real_emails=0)
        output = os.environ.get('MIO_CONCURRENCY_EVIDENCE')
        if output:
            Path(output).write_text(json.dumps(evidence, indent=2)+'\n')

    async def test_existing_disabled_account_and_old_occupancy_survive_limit_change(self):
        self.config.concurrency = 2
        root = await self.account('A')
        await self.provider('A')
        self.assertEqual(await self.effective('A'), 2)
        r = await self.submit('A', 'image', 'original-unknown')
        self.assertEqual(r.status_code, 200)
        await self.until(lambda: len(self.mock.accepted) == 1)
        self.release()
        await self.completed('A', r.json()['task_id'])
        await asyncio.to_thread(self.supervisor.account_status, 'user-a', False)
        # Synthetic prior unknown, never a production record. Startup must retain it.
        with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
            row = db.execute('SELECT id,data FROM jobs').fetchone()
            job = json.loads(row[1])
            job.update(status='canceled', outstanding=True, submission_uncertain=True)
            db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(job), row[0]))
        before = {}
        for filename, field in (('model-access.json', 'max_concurrent'),
                                ('public-beta.json', 'max_concurrent_generations')):
            p = root/'.auth'/filename
            data = json.loads(p.read_text())
            before[filename] = dict(data)
            data[field] = 10
            p.write_text(json.dumps(data))
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual({k: v for k, v in data.items() if k != field},
                             {k: v for k, v in before[filename].items() if k != field})
        self.config.concurrency = 10
        await asyncio.to_thread(self.supervisor.account_status, 'user-a', True)
        token = self.store.session(self.accounts['A'])
        self.clients['A'].cookies.clear()
        self.clients['A'].cookies.set('mio_beta_session', token)
        await self.enter('A')
        self.assertEqual(await self.effective('A'), 10)
        with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
            self.assertEqual(json.loads(db.execute('SELECT data FROM jobs').fetchone()[0]), job)
        with self.mock.condition:
            self.mock.release_all = False
        responses = await asyncio.gather(*(self.submit('A', 'image', 'old-slot-'+str(i)) for i in range(10)))
        self.assertEqual(Counter(r.status_code for r in responses), {200: 9, 429: 1})
        await self.until(lambda: len(self.mock.accepted) == 10)
        self.release()
        for response in responses:
            if response.status_code == 200:
                await self.completed('A', response.json()['task_id'])
        with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
            self.assertEqual(json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]), job)
        self.assertEqual(len(self.mock.accepted), 10)  # Original work was never replayed.

    async def test_simultaneous_mixed_admissions_cannot_exceed_ten(self):
        await self.account('A')
        await self.provider('A')
        kinds = ['image', 'video', 'llm']*10
        requests = [asyncio.create_task(self.submit('A', kind, 'mixed-race-'+str(i)))
                    for i, kind in enumerate(kinds)]
        self.inflight.extend(requests)
        await self.until(lambda: len(self.mock.accepted) >= 10)
        await self.until(lambda: sum(t.done() for t in requests) +
                         sum(kind == 'llm' for _, kind, _ in self.mock.accepted) == 30)
        self.assertEqual(len(self.mock.accepted), 10)
        self.assertEqual(self.mock.held, 10)
        self.release()
        responses = await asyncio.gather(*requests)
        self.assertEqual(Counter(r.status_code for r in responses), {200: 10, 429: 20})
        for kind, r in zip(kinds, responses):
            if r.status_code == 200 and kind != 'llm':
                await self.completed('A', r.json()['task_id'])
        self.assertEqual(len(self.mock.accepted), 10)
        self.assertEqual(self.mock.timed_out, 0)
