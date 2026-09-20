"""Synthetic records only. Privilege/process boundary is exercised as root in CI."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from instance_maintenance import Maintenance, initialize
from public_beta_maintenance import inspect, set_phase, main
import maintenance_interruption as command


class InterruptionTests(unittest.TestCase):
    record_count = 4

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.control=self.root/'control';initialize(self.control)
        self.gate=Maintenance(self.control);self.iid='1'*32
        self.instances=self.root/'instances';self.data=self.instances/self.iid
        (self.data/'.auth').mkdir(parents=True)
        (self.data/'.instance.json').write_text(json.dumps({'instance_id':self.iid,'data_root':str(self.data)}))
        self.db=self.root/'gateway.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE instances (instance_id TEXT,data_root TEXT,pid INTEGER,process_started TEXT,status TEXT)')
            db.execute('INSERT INTO instances VALUES (?,?,?,?,?)',(self.iid,str(self.data),self.gate.pid,self.gate.start,'running'))
        with self.gate.db() as db:
            for iid in ('gateway',self.iid):db.execute('INSERT INTO processes VALUES (?,?,?)',(iid,self.gate.pid,self.gate.start))
        for _ in range(self.record_count):self.gate.uncertain_llm(self.iid)
        self.before=self.rows();now=time.time()
        self.approval=dict(schema=1,window_id='a'*32,epoch=1,source_revision='a'*40,target_revision='b'*40,
            created_at=now-1,expires_at=now+300,remote_status_and_cost_unknown=True,
            records=[dict(id=r['id'],fingerprint=command.fingerprint(r)) for r in self.before])
        self.file=self.root/'approval.json';self.save()
        self.real_running_source=command.running_source
        # Unit seams are not exposed in CLI. The complete old-process rehearsal
        # uses actual uid0, procfs, clean Git releases and unmodified f598 code.
        for seam,value in [('public_beta_maintenance.admin',lambda *a:None),
                           ('maintenance_interruption.running_source',lambda *a:None),
                           ('maintenance_interruption.revision',lambda p:'a'*40 if p==self.root else 'b'*40)]:
            mock=patch(seam,value);mock.start();self.addCleanup(mock.stop)

    def rows(self):
        with self.gate.db() as db:return [dict(r) for r in db.execute('SELECT * FROM uncertainties ORDER BY id')]

    def save(self):
        self.file.write_text(json.dumps(self.approval));self.file.chmod(0o600)

    def run_command(self,timeout=0):
        return command.seal_for_interruption(self.control,self.db,self.instances,self.file,self.root,Path(__file__).parent,timeout)

    def test_exact_records_seal_but_default_status_still_unsafe(self):
        result=self.run_command();self.assertTrue(result['interruption_authorized']);self.assertTrue(result['local_quiescent'])
        self.assertEqual(result['historical_unknowns_retained'],self.record_count);self.assertFalse(result['restart_safe'])
        self.assertEqual(self.gate.state(),{'schema':1,'phase':'sealed','epoch':2})
        self.assertEqual(self.rows(),self.before)
        status=inspect(self.gate,[self.data]);self.assertFalse(status['restart_safe'])
        self.assertIn('llm_remote_status_requires_reconcile',status['blockers'])
        audit=json.loads((self.control/'interruption-audit'/('a'*32+'.json')).read_text())
        self.assertEqual(audit['status'],'committed');self.assertEqual(audit['approval'],self.approval)
        self.assertEqual(audit['historical_unknowns_retained'],self.record_count)
        self.assertFalse(audit['restart_safe']);self.assertTrue(audit['remote_status_and_cost_unknown'])

    def test_same_count_different_id_or_fingerprint_denied(self):
        for field in ('id','fingerprint'):
            old=self.approval['records'][0][field];self.approval['records'][0][field]='0'*len(old);self.save()
            result=self.run_command();self.assertFalse(result['interruption_authorized'])
            self.assertIn('historical_unknown_set_mismatch',result['blockers'])
            self.approval['records'][0][field]=old
        self.assertEqual(self.gate.state()['phase'],'draining');self.assertEqual(self.rows(),self.before)

    def test_extra_unknown_is_never_implicitly_approved(self):
        self.gate.uncertain_llm(self.iid);result=self.run_command()
        self.assertFalse(result['interruption_authorized']);self.assertEqual(result['historical_unknowns_retained'],self.record_count+1)
        self.assertIn('historical_unknown_set_mismatch',result['blockers'])
        self.assertEqual(self.gate.state()['phase'],'draining')

    def test_missing_approved_record_is_not_a_subset_authorization(self):
        self.approval['records'].pop();self.save()
        result=self.run_command();self.assertFalse(result['interruption_authorized'])
        self.assertIn('historical_unknown_set_mismatch',result['blockers'])
        self.assertEqual(self.rows(),self.before)

    def test_missing_actual_record_is_not_authorized(self):
        # Synthetic fixture mutation only: approved must also not be a superset.
        with self.gate.db() as db:db.execute('DELETE FROM uncertainties WHERE id=?',(self.before[0]['id'],))
        remaining=self.rows();result=self.run_command()
        self.assertFalse(result['interruption_authorized'])
        self.assertIn('historical_unknown_set_mismatch',result['blockers'])
        self.assertEqual(self.rows(),remaining)

    def test_invalid_record_sets_rejected_without_audit_or_state_change(self):
        original=self.approval['records']
        invalid=[[],None,{},'all',[original[0],original[0]],
            [original[0],dict(id=original[0]['id'],fingerprint='f'*64)],
            [original[0],dict(id='f'*32,fingerprint=original[0]['fingerprint'])],
            [dict(id='*',fingerprint='f'*64)],[dict(id='a'*32)],
            [dict(id='a'*32,fingerprint='b'*64,ignore_all=True)],
            [dict(id='A'*32,fingerprint='b'*64)],
            [dict(id='a'*32,fingerprint='b'*63)],
            [dict(id=int('1'*32),fingerprint='b'*64)],
            [dict(id='a'*32,fingerprint=int('1'*64))]]
        for records in invalid:
            with self.subTest(records=records):
                self.approval['records']=records;self.save()
                with self.assertRaises(ValueError):self.run_command()
                self.assertEqual(self.gate.state()['phase'],'draining');self.assertEqual(self.rows(),self.before)
                self.assertFalse((self.control/'interruption-audit').exists())
        self.approval['records']=original

    def test_record_limit_and_private_file_size_boundaries(self):
        # Independent boundary expectation, not derived from the implementation.
        self.approval['records']=[dict(id=f'{i:032x}',fingerprint=f'{i:064x}') for i in range(512)]
        command.validate_approval(self.approval,self.gate.state(),time.time())
        self.approval['records'].append(dict(id=f'{512:032x}',fingerprint=f'{512:064x}'))
        with self.assertRaises(ValueError):command.validate_approval(self.approval,self.gate.state(),time.time())
        self.approval['records']=[dict(id=r['id'],fingerprint=command.fingerprint(r)) for r in self.before]
        raw=json.dumps(self.approval).encode();self.file.write_bytes(raw+b' '*(65536-len(raw)))
        self.assertEqual(command.private_json(self.file)[0],self.approval)
        self.file.write_bytes(raw+b' '*(65537-len(raw)))
        with self.assertRaises(ValueError):self.run_command()
        self.file.write_text('{invalid')
        with self.assertRaises(ValueError):self.run_command()
        self.assertEqual(self.gate.state()['phase'],'draining');self.assertEqual(self.rows(),self.before)

    def test_all_local_activity_kinds_block_and_timeout_retains_lease(self):
        for kind in ('http','read','local_write','recovery','runner','stream','upload','result','model'):
            with self.subTest(kind=kind):
                set_phase(self.gate,'open');lease=self.gate.admit(self.iid,kind);self.assertIsNotNone(lease)
                set_phase(self.gate,'draining');self.approval['epoch']=self.gate.state()['epoch'];self.save()
                result=self.run_command(.001);self.assertTrue(result['timed_out']);self.assertEqual(result['active'],1)
                self.assertEqual(self.gate.status()['active'],1);lease.finish()
        set_phase(self.gate,'open');lease=self.gate.admit(self.iid,'model');set_phase(self.gate,'draining')
        self.approval['epoch']=self.gate.state()['epoch'];self.save()
        self.assertFalse(self.run_command()['interruption_authorized']);lease.finish()

    def test_waits_for_accepted_work_and_atomically_rejects_new_work(self):
        lease=self.gate.admit(self.iid,'local_write');done=[]
        thread=threading.Thread(target=lambda:done.append(self.run_command(2)));thread.start()
        time.sleep(.08);self.assertEqual(self.gate.state()['phase'],'draining');lease.finish();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(done[0]['interruption_authorized'])
        for kind in ('read','local_write','recovery','upload','model'):
            self.assertIsNone(self.gate.admit(self.iid,kind))

    def test_starting_and_missing_coverage_fail_closed(self):
        with sqlite3.connect(self.db) as db:db.execute("UPDATE instances SET status='starting'")
        self.assertIn('instance_starting',self.run_command()['blockers'])
        with sqlite3.connect(self.db) as db:db.execute("UPDATE instances SET status='running'")
        with self.gate.db() as db:db.execute("DELETE FROM processes WHERE instance='gateway'")
        self.assertIn('legacy_or_unobserved_process',self.run_command()['blockers'])

    def test_orphan_activity_never_discarded(self):
        set_phase(self.gate,'open');lease=self.gate.admit(self.iid,'runner');set_phase(self.gate,'draining')
        self.approval['epoch']=self.gate.state()['epoch'];self.save()
        with self.gate.db() as db:db.execute("UPDATE activities SET start='old process'")
        result=self.run_command();self.assertEqual(result['orphaned'],1)
        self.assertIn('process_interrupted_requires_reconcile',result['blockers'])
        self.assertEqual(self.gate.status()['active'],1)

    def test_pending_uncertain_result_and_corrupt_ledgers_denied(self):
        path=self.data/'.auth/model-tasks.sqlite3'
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE jobs (data TEXT)')
        for job in ({'status':'submitting'},{'submission_uncertain':True},{'status':'result_recovery_required'}):
            with sqlite3.connect(path) as db:db.execute('DELETE FROM jobs');db.execute('INSERT INTO jobs VALUES (?)',(json.dumps(job),))
            self.assertFalse(self.run_command()['interruption_authorized'])
        path.write_bytes(b'corrupt sqlite')
        with self.assertRaises(sqlite3.DatabaseError):self.run_command()
        self.assertEqual(self.gate.state()['phase'],'draining')

    def test_asset_processing_denied(self):
        (self.data/'data').mkdir();(self.data/'data/asset_library.json').write_text(json.dumps({'categories':[{'items':[{'registrations':{'x':{'status':'Processing'}}}]}]}))
        self.assertIn('asset_processing',self.run_command()['blockers'])

    def test_epoch_expiry_version_and_private_permissions(self):
        for key,bad in [('epoch',2),('expires_at',time.time()-1),('source_revision','c'*40),('target_revision','c'*40)]:
            old=self.approval[key];self.approval[key]=bad;self.save()
            with self.assertRaises(ValueError):self.run_command()
            self.approval[key]=old
        self.save();self.file.chmod(0o644)
        with self.assertRaises(ValueError):self.run_command()

    def test_repeat_and_reopened_epoch_cannot_reuse_authorization(self):
        self.run_command()
        with self.assertRaises(ValueError):self.run_command()
        set_phase(self.gate,'open');set_phase(self.gate,'draining')
        with self.assertRaises(ValueError):self.run_command()
        self.approval['epoch']=self.gate.state()['epoch'];self.save()
        with self.assertRaises(FileExistsError):self.run_command()
        self.assertEqual(self.rows(),self.before)

    def test_failure_before_state_switch_consumes_window_without_authorizing(self):
        with patch('public_beta_maintenance.atomic_state',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.run_command()
        self.assertEqual(self.gate.state()['phase'],'draining')
        with self.assertRaises(FileExistsError):self.run_command()
        self.assertEqual(self.rows(),self.before)

    def test_failure_after_seal_never_returns_authorized_and_stays_closed(self):
        real=command.atomic_audit
        def fail_commit(path,value,**kw):
            if value['status']=='committed':raise OSError('fixture')
            return real(path,value,**kw)
        with patch('maintenance_interruption.atomic_audit',fail_commit):
            with self.assertRaises(OSError):self.run_command()
        self.assertEqual(self.gate.state()['phase'],'sealed');self.assertEqual(self.rows(),self.before)
        self.assertFalse(inspect(self.gate,[self.data])['restart_safe'])

    def test_open_phase_and_sandbox_are_not_an_interruption_entry(self):
        set_phase(self.gate,'open')
        with self.assertRaises(ValueError):self.run_command()
        self.assertEqual(main(['--root',str(self.control),'--sandbox','seal-for-interruption',
            '--gateway-db',str(self.db),'--instances-root',str(self.instances),'--approval-file',str(self.file),
            '--source-release',str(self.root),'--target-release',str(Path(__file__).parent)]),2)

    def test_real_admin_guard_denies_nonroot(self):
        from public_beta_maintenance import admin
        # Test the actual guard, outside the unit fixture seam.
        import importlib
        import public_beta_maintenance
        original=importlib.reload(public_beta_maintenance).admin
        with patch('os.geteuid',return_value=501):
            with self.assertRaises(PermissionError):original(self.control)

    def test_worker_source_uses_exact_entrypoint_and_isolated_data_cwd(self):
        with self.gate.db() as db:db.execute("UPDATE processes SET pid=999 WHERE instance='gateway'")
        def cwd(path):return str(self.root if '/999/' in str(path) else self.data)
        def argv(path):
            return (b'/python\0'+str(self.root/('public_beta.py' if '/999/' in str(path) else 'public_beta_worker.py')).encode()
                    +(b'\0' if '/999/' in str(path) else b'\0serve\0'))
        with patch('os.readlink',cwd),patch('pathlib.Path.read_bytes',argv):
            self.real_running_source(self.gate,self.db,self.root)
        with patch('os.readlink',cwd),patch('pathlib.Path.read_bytes',return_value=b'/python\0/other/public_beta_worker.py\0serve\0'):
            with self.assertRaises(ValueError):self.real_running_source(self.gate,self.db,self.root)


class SevenRecordInterruptionTests(InterruptionTests):
    """Repeat all safety gates with seven; count never substitutes for identity."""
    record_count = 7
