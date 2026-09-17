"""Fake data only: atomic fence, streaming lifetime, unsafe evidence and rollback."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from starlette.responses import StreamingResponse, JSONResponse

from instance_maintenance import Maintenance, MaintenanceMiddleware, initialize, CURRENT_ACTIVITY, DETAIL, tracked_to_thread
from maintenance_routes import classify, NEW_MODEL, NEW_UPLOAD, NEW_REGISTRATION
from public_beta_maintenance import admin, caddy_barrier, inspect, set_phase, wait_for_drain, ledger_counts, coverage_blockers, reconcile


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mio-maint-test-')
        self.root = Path(self.tmp.name).resolve()/'control'
        initialize(self.root)
        self.gate = Maintenance(self.root)
        self.addCleanup(self.tmp.cleanup)

    def test_initial_state_is_draining_and_reopening_needs_explicit_action(self):
        self.assertIsNone(self.gate.admit('one', 'model'))
        set_phase(self.gate, 'open')
        lease = self.gate.admit('one', 'model')
        self.assertEqual(self.gate.status()['active'], 1)
        lease.finish()

    def test_idempotent_switch_and_process_reconstruction_preserve_state(self):
        original = self.gate.state()
        self.assertEqual(set_phase(self.gate, 'draining'), original)
        self.assertEqual(Maintenance(self.root).state(), original)
        set_phase(self.gate, 'open'); set_phase(self.gate, 'draining')
        self.assertIsNone(Maintenance(self.root).admit('one', 'upload'))

    def test_admission_and_draining_share_one_atomic_boundary(self):
        set_phase(self.gate, 'open')
        leases = []
        barrier = threading.Barrier(17)
        def request():
            barrier.wait()
            result = self.gate.admit('one', 'model')
            if result:
                leases.append(result)
        threads = [threading.Thread(target=request) for _ in range(16)]
        for thread in threads:
            thread.start()
        barrier.wait(); set_phase(self.gate, 'draining')
        for thread in threads:
            thread.join()
        self.assertEqual(self.gate.status()['active'], len(leases))
        self.assertIsNone(self.gate.admit('one', 'model'))
        for lease in leases:
            lease.finish()
        self.assertTrue(inspect(self.gate, [], seal=True)['restart_safe'])

    def test_accepted_parent_can_handoff_to_runner_after_draining(self):
        set_phase(self.gate, 'open'); parent = self.gate.admit('one', 'model')
        token = CURRENT_ACTIVITY.set(parent)
        try:
            set_phase(self.gate, 'draining'); child = self.gate.child('one')
        finally:
            CURRENT_ACTIVITY.reset(token)
        parent.finish()
        self.assertEqual(self.gate.status()['by_phase'], {'runner': 1})
        self.assertFalse(inspect(self.gate, [], seal=True)['restart_safe'])
        child.finish(); self.assertTrue(inspect(self.gate, [], seal=True)['restart_safe'])

    def test_gateway_receipt_handoff_requires_live_matching_activity(self):
        set_phase(self.gate, 'open'); parent = self.gate.admit('gateway', 'model')
        set_phase(self.gate, 'draining')
        child = self.gate.admit('one', 'model', parent=parent.id, delegated=True)
        self.assertIsNotNone(child)
        self.assertIsNone(self.gate.admit('one', 'upload', parent=parent.id, delegated=True))
        self.assertIsNone(self.gate.admit('one', 'model', parent='invented', delegated=True))
        child.finish(); parent.finish()
        self.assertIsNone(self.gate.admit('one', 'model', parent=parent.id, delegated=True))

    def test_seal_fences_new_recovery_and_local_writes(self):
        lease = self.gate.admit('one', 'recovery')
        self.assertFalse(inspect(self.gate, [], seal=True)['restart_safe'])
        lease.finish(); self.assertTrue(inspect(self.gate, [], seal=True)['restart_safe'])
        self.assertIsNone(self.gate.admit('one', 'read'))
        self.assertIsNone(self.gate.admit('one', 'local_write'))

    def test_drain_timeout_stops_upgrade_without_canceling_activity(self):
        lease = self.gate.admit('one', 'recovery')
        result = wait_for_drain(self.gate, [], .02)
        self.assertTrue(result['timed_out']); self.assertEqual(result['action'], 'stop_upgrade')
        self.assertEqual(self.gate.status()['active'], 1); lease.finish()

    def test_process_loss_is_retained_as_blocker_not_pruned(self):
        self.gate.admit('one', 'recovery')
        with patch('instance_maintenance.process_identity', return_value='different-process'):
            result = inspect(self.gate, [], seal=True)
        self.assertEqual(result['orphaned'], 1)
        self.assertIn('process_interrupted_requires_reconcile', result['blockers'])
        self.assertFalse(result['restart_safe'])

    def test_empty_journal_never_proves_old_process_coverage(self):
        dbfile = Path(self.tmp.name).resolve()/'gateway.sqlite3'
        with sqlite3.connect(dbfile) as db:
            db.execute('CREATE TABLE instances (instance_id,pid,process_started,status)')
            db.execute('INSERT INTO instances VALUES (?,?,?,?)', ('old', os.getpid(), self.gate.start, 'running'))
        self.assertEqual(coverage_blockers(self.gate, dbfile), ('legacy_or_unobserved_process',))

    def test_unknown_submit_and_pending_result_block_restart_without_echo(self):
        root = Path(self.tmp.name).resolve()/'instance'; (root/'.auth').mkdir(parents=True)
        (root/'.instance.json').write_text('{}')
        payload = {'submission_uncertain': True, 'outstanding': False,
                   'status': 'result_recovery_required', 'prompt': 'private-prompt', 'key': 'private-key'}
        with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
            db.execute('CREATE TABLE jobs(data)'); db.execute('INSERT INTO jobs VALUES (?)', (json.dumps(payload),))
        result = inspect(self.gate, [root], seal=True)
        self.assertEqual(result['uncertain_submissions'], 1); self.assertEqual(result['pending_results'], 1)
        self.assertFalse(result['restart_safe']); self.assertNotIn('private', json.dumps(result))

    def test_corrupt_ledger_is_blocker(self):
        root = Path(self.tmp.name).resolve()/'instance'; (root/'.auth').mkdir(parents=True)
        (root/'.instance.json').write_text('{}'); (root/'.auth/model-tasks.sqlite3').write_bytes(b'not-a-db')
        self.assertEqual(ledger_counts([root])['unreadable_ledgers'], 1)
        (root/'.auth/model-tasks.sqlite3').unlink()
        with sqlite3.connect(root/'.auth/model-tasks.sqlite3') as db:
            db.execute('CREATE TABLE jobs(data)');db.execute('INSERT INTO jobs VALUES (?)',('[]',))
        self.assertEqual(ledger_counts([root])['unreadable_ledgers'],1)

    def test_asset_unknown_and_processing_are_distinct_from_http_activity(self):
        root = Path(self.tmp.name).resolve()/'instance'; (root/'data').mkdir(parents=True)
        (root/'.instance.json').write_text('{}')
        (root/'data/asset_library.json').write_text(json.dumps({'libraries': [{'categories': [{'items': [
            {'registrations': {'avatar': {'status': 'SubmissionUnknown'}, 'volcengine': {'status': 'Processing'}}}]}]}]}))
        self.assertEqual(ledger_counts([root])['asset_processing'], 2)

    def test_admin_permission_and_sandbox_restriction(self):
        with patch('os.geteuid', return_value=1000), self.assertRaises(PermissionError):
            admin(self.root)
        with self.assertRaises(PermissionError):
            admin('/opt/real-production/control', sandbox=True)
        admin(self.root, sandbox=True)

    def test_reinitialization_cannot_clear_activity(self):
        lease = self.gate.admit('one', 'recovery')
        with self.assertRaises(ValueError):
            initialize(self.root)
        self.assertEqual(self.gate.status()['active'], 1); lease.finish()

    def test_unsafe_state_permissions_and_symlink_are_rejected(self):
        (self.root/'state.json').chmod(0o666)
        with self.assertRaises(RuntimeError):
            self.gate.admit('one', 'model')
        (self.root/'state.json').chmod(0o640)
        (self.root/'state.json').unlink(); (self.root/'state.json').symlink_to(self.root/'gate.lock')
        with self.assertRaises(RuntimeError):
            self.gate.state()

    def test_normal_abort_reopens_without_stopping_or_replaying_work(self):
        lease = self.gate.admit('one', 'recovery'); set_phase(self.gate, 'open')
        self.assertEqual(self.gate.status()['active'], 1); lease.finish()

    def test_llm_unknown_survives_local_release_and_reconstruction(self):
        identity = self.gate.uncertain_llm('one')
        result = inspect(Maintenance(self.root), [], seal=True)
        self.assertEqual(result['unknown_llm_responses'], 1)
        self.assertFalse(result['restart_safe'])
        self.gate.complete_llm(identity)
        self.assertTrue(inspect(self.gate, [], seal=True)['restart_safe'])

    def test_reconcile_requires_private_actual_remote_review_and_preserves_audit(self):
        identity=self.gate.uncertain_llm('one')
        evidence=Path(self.tmp.name).resolve()/'review.json'
        evidence.write_text(json.dumps(dict(schema=1,id=identity,original_remote_work_ended=True,original_ledgers_reviewed=True)))
        evidence.chmod(0o600)
        result=reconcile(self.gate,[],identity,evidence,uncertainty=True)
        self.assertFalse(result['task_ledger_modified']);self.assertFalse(result['generation_replayed'])
        with self.gate.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0],1)

    def test_reconcile_cannot_delete_live_activity(self):
        lease=self.gate.admit('one','recovery');evidence=Path(self.tmp.name).resolve()/'review.json'
        evidence.write_text(json.dumps(dict(schema=1,id=lease.id,original_remote_work_ended=True,original_ledgers_reviewed=True)))
        evidence.chmod(0o600)
        with self.assertRaises(ValueError):
            reconcile(self.gate,[],lease.id,evidence)
        self.assertEqual(self.gate.status()['active'],1);lease.finish()

    def test_reconcile_refuses_local_disconnect_claim(self):
        identity=self.gate.uncertain_llm('one');evidence=Path(self.tmp.name).resolve()/'review.json'
        evidence.write_text(json.dumps(dict(schema=1,id=identity,original_remote_work_ended=False,original_ledgers_reviewed=True)))
        evidence.chmod(0o600)
        with self.assertRaises(ValueError):
            reconcile(self.gate,[],identity,evidence,uncertainty=True)
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],1)

    def test_new_model_upload_and_registration_routes_never_whitelisted(self):
        for key in NEW_MODEL | NEW_UPLOAD | NEW_REGISTRATION:
            method, path = key.split(' ', 1); path = path.replace('{item_id}', 'owned')
            self.assertNotIn(classify(method, path), {'read', 'recovery', 'local_write', 'control'}, key)
        self.assertEqual(classify('GET', '/api/new-model-operation'), 'unreviewed')
        self.assertEqual(classify('POST', '/api/canvas-image-tasks/original/refresh'), 'recovery')

    def test_caddy_patch_changes_only_mio_and_is_idempotent(self):
        config = {'apps': {'http': {'servers': {'shared': {'routes': [
            {'match': [{'host': ['mio.test']}], 'handle': [{'handler': 'subroute', 'routes': [{'handle': [{'handler': 'reverse_proxy'}]}]}]},
            {'match': [{'host': ['sub.test']}], 'handle': [{'handler': 'subroute', 'routes': [{'handle': [{'handler': 'reverse_proxy'}]}]}]},
        ]}}}}}
        before = copy.deepcopy(config); patched = caddy_barrier(config, 'mio.test')
        self.assertEqual(config, before)
        self.assertEqual(patched['apps']['http']['servers']['shared']['routes'][1], config['apps']['http']['servers']['shared']['routes'][1])
        self.assertEqual(caddy_barrier(patched, 'mio.test'), patched)
        self.assertEqual(patched['apps']['http']['servers']['shared']['routes'][0]['handle'][0]['routes'][0]['handle'][0]['status_code'], 503)

    def test_caddy_shared_or_ambiguous_route_is_not_modified(self):
        config = {'apps': {'http': {'servers': {'shared': {'routes': [
            {'match': [{'host': ['mio.test', 'sub.test']}], 'handle': [{'handler': 'subroute'}]}]}}}}}
        with self.assertRaises(ValueError):
            caddy_barrier(config, 'mio.test')


class MaintenanceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mio-maint-asgi-')
        self.root = Path(self.tmp.name).resolve()/'control'; initialize(self.root); self.gate = Maintenance(self.root)
        set_phase(self.gate, 'open'); self.addCleanup(self.tmp.cleanup)

    async def test_sealed_startup_migration_is_counted_until_lifecycle_ready(self):
        set_phase(self.gate,'draining')
        self.assertTrue(inspect(self.gate,[],seal=True)['restart_safe'])
        entered,finish=asyncio.Event(),asyncio.Event()
        async def app(scope,receive,send):
            entered.set();await finish.wait()
            await tracked_to_thread(lambda: (self.root.parent/'migration-result').write_text('done'))
            await send({'type':'lifespan.startup.complete'})
        async def noop(*args):pass
        wrapped=MaintenanceMiddleware(app,root=self.root,instance='one')
        task=asyncio.create_task(wrapped({'type':'lifespan'},noop,noop));await entered.wait()
        self.assertEqual(self.gate.status()['by_phase'],{'local_write':1})
        self.assertFalse(inspect(self.gate,[],seal=True)['restart_safe'])
        finish.set();await task
        self.assertEqual((self.root.parent/'migration-result').read_text(),'done')
        self.assertTrue(inspect(self.gate,[],seal=True)['restart_safe'])

    async def test_accepted_runner_can_write_results_after_http_finishes_during_drain(self):
        from types import SimpleNamespace
        from instance_model_tasks import ControlledModels
        parent=self.gate.admit('one','image');token=CURRENT_ACTIVITY.set(parent)
        entered,finish=asyncio.Event(),asyncio.Event();writes=[]
        models=object.__new__(ControlledModels)
        models.paths=SimpleNamespace(maintenance=self.gate,instance_id='one')
        models.cancels={};models.runners={}
        async def original_work(*args,**kwargs):
            entered.set();await finish.wait()
            await tracked_to_thread(writes.append,'original-result')
        models.run=original_work
        try:
            models.launch({'id':'original','purpose':'image'})
            runner=models.runners['original'];await entered.wait()
            parent.finish();set_phase(self.gate,'draining')
            self.assertEqual(self.gate.status()['by_phase'],{'runner_image':1})
            finish.set();await runner;await asyncio.sleep(0)
            self.assertEqual(writes,['original-result'])
            self.assertTrue(inspect(self.gate,[],seal=True)['restart_safe'])
        finally:
            finish.set();CURRENT_ACTIVITY.reset(token)

    async def test_stream_headers_do_not_release_and_final_processing_remains_counted(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'first', 'more_body': True})
            entered.set(); await finish.wait()
            await send({'type': 'http.response.body', 'body': b'last', 'more_body': False})
            self.assertEqual(self.gate.status()['active'], 1)  # local processing after last byte
        wrapped = MaintenanceMiddleware(app, root=self.root, instance='one')
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            pass
        task = asyncio.create_task(wrapped({'type': 'http', 'method': 'POST', 'path': '/api/chat/stream'}, receive, send))
        await entered.wait(); set_phase(self.gate, 'draining')
        self.assertEqual(self.gate.status()['active'], 1); finish.set(); await task
        self.assertEqual(self.gate.status()['active'], 0)

    async def test_disconnect_and_exception_release_local_activity_only(self):
        entered = asyncio.Event()
        async def app(scope, receive, send):
            entered.set(); await asyncio.Event().wait()
        wrapped = MaintenanceMiddleware(app, root=self.root)
        async def noop(*args):
            pass
        task = asyncio.create_task(wrapped({'type': 'http', 'method': 'POST', 'path': '/api/chat/stream'}, noop, noop))
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.gate.status()['active'], 0)

    async def test_slow_receive_is_counted_and_gate_rejection_reads_no_body(self):
        entered, finish = asyncio.Event(), asyncio.Event(); bodies = []
        async def receive():
            entered.set(); await finish.wait(); bodies.append(1)
            return {'type': 'http.request', 'body': b'fake', 'more_body': False}
        async def app(scope, receive, send):
            await receive(); await JSONResponse({'ok': True})(scope, receive, send)
        wrapped = MaintenanceMiddleware(app, root=self.root)
        async def send(message):
            pass
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/runninghub/upload-asset'}
        task = asyncio.create_task(wrapped(scope, receive, send)); await entered.wait()
        set_phase(self.gate, 'draining'); self.assertEqual(self.gate.status()['by_phase'], {'upload': 1})
        response = []
        async def record(message):
            response.append(message)
        await wrapped(scope, receive, record)
        self.assertEqual(bodies, []); self.assertEqual(response[0]['status'], 503)
        self.assertEqual({k:json.loads(response[1]['body'])['detail'][k] for k in DETAIL}, DETAIL);self.assertRegex(json.loads(response[1]['body'])['detail']['event_id'],r'^[a-f0-9]{32}$')
        finish.set(); await task; self.assertEqual(self.gate.status()['active'], 0)

    async def test_missing_state_fails_closed_before_application_network_work(self):
        calls = []
        async def app(*args):
            calls.append('network')
        wrapped = MaintenanceMiddleware(app, root=self.root)
        async def noop(*args):
            pass
        for value in ([],{'schema':1,'phase':'open','epoch':'invalid'}, {'schema':1,'phase':[],'epoch':1}):
            (self.root/'state.json').write_text(json.dumps(value))
            response=[]
            async def record(message):response.append(message)
            await wrapped({'type':'http','method':'POST','path':'/api/canvas-llm'},noop,record)
            self.assertEqual(response[0]['status'],503)
        (self.root/'state.json').unlink()
        await wrapped({'type': 'http', 'method': 'POST', 'path': '/api/canvas-llm'}, noop, noop)
        self.assertEqual(calls, [])

    async def test_canceled_thread_waiter_does_not_release_running_write(self):
        parent = self.gate.admit('one', 'model'); token = CURRENT_ACTIVITY.set(parent)
        entered, finish = threading.Event(), threading.Event()
        def write():
            entered.set(); finish.wait(5)
        try:
            task = asyncio.create_task(tracked_to_thread(write))
            while not entered.is_set():
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            parent.finish(); set_phase(self.gate, 'draining')
            self.assertEqual(self.gate.status()['by_phase'], {'result': 1})
            self.assertFalse(inspect(self.gate, [], seal=True)['restart_safe'])
            finish.set()
            for _ in range(100):
                if self.gate.status()['active']==0:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(self.gate.status()['active'], 0)
        finally:
            finish.set(); CURRENT_ACTIVITY.reset(token)

    async def test_canceled_thread_future_keeps_receipt_until_actual_thread_finishes(self):
        parent=self.gate.admit('one','image');token=CURRENT_ACTIVITY.set(parent)
        entered,finish=threading.Event(),threading.Event();internal=[]
        original=asyncio.create_task
        def capture(coro):
            task=original(coro);internal.append(task);return task
        def write():
            entered.set();finish.wait(5)
        try:
            with patch('instance_maintenance.asyncio.create_task',side_effect=capture):
                waiter=original(tracked_to_thread(write))
                while not entered.is_set():await asyncio.sleep(.01)
            internal[0].cancel()
            with self.assertRaises(asyncio.CancelledError):await waiter
            parent.finish();set_phase(self.gate,'draining')
            self.assertEqual(self.gate.status()['by_phase'],{'result':1})
            self.assertFalse(inspect(self.gate,[],seal=True)['restart_safe'])
            finish.set()
            for _ in range(100):
                if self.gate.status()['active']==0:break
                await asyncio.sleep(.01)
            self.assertTrue(inspect(self.gate,[],seal=True)['restart_safe'])
        finally:
            finish.set();CURRENT_ACTIVITY.reset(token)
