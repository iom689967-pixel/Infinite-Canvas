"""Seat reservations use real SQLite transactions, including independent writers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from public_beta_store import BetaConfig, GatewayStore, BetaError


class BetaCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='beta-capacity-')
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.config=BetaConfig(root/'gateway',root/'instances',max_users=2,min_free_disk=0)
        self.store=GatewayStore(self.config)
        self.port=35000

    def reserve(self,name,status='active'):
        self.port+=1
        item=self.store.reserve(name,'test-hash-not-used',self.port)
        if status!='provisioning':self.store.activate(item['user_id'])
        if status=='disabled':self.store.set_enabled(item['user_id'],False)
        return item['user_id']

    def test_active_and_provisioning_reserve_seats(self):
        self.reserve('active');self.reserve('reserved','provisioning')
        self.assertEqual(self.store.capacity(),{'total_users':2,'active_users':1,'provisioning_users':1,
                         'active_seats':2,'disabled_users':0,'max_public_users':2,'remaining_registration_slots':0})
        with self.assertRaises(BetaError):self.reserve('third')

    def test_disabled_records_remain_but_release_seats(self):
        uid=self.reserve('alice');self.store.set_enabled(uid,False)
        self.reserve('bob');self.reserve('charlie')
        info=self.store.capacity()
        self.assertEqual((info['total_users'],info['active_seats'],info['disabled_users']),(3,2,1))
        self.assertEqual(self.store.instance(uid)['user_status'],'disabled')

    def test_enable_full_refused_then_enable_claims_seat(self):
        alice=self.reserve('alice','disabled');bob=self.reserve('bob');self.reserve('charlie')
        with self.assertRaises(BetaError) as error:self.store.set_enabled(alice,True)
        self.assertEqual(error.exception.message,'Public Beta 名额已满，无法启用该用户。')
        self.assertEqual(self.store.instance(alice)['user_status'],'disabled')
        self.store.set_enabled(bob,False);self.store.set_enabled(alice,True)
        self.assertEqual(self.store.capacity()['active_seats'],2)
        self.assertEqual(self.store.instance(alice)['user_status'],'active')

    def test_already_active_enable_does_not_take_another_seat(self):
        self.config.max_users=1;uid=self.reserve('alice');self.store.set_enabled(uid,True)
        self.assertEqual(self.store.capacity()['active_seats'],1)

    def test_safe_registration_rollback_releases_reservation_only(self):
        reserved=self.reserve('reserved','provisioning');active=self.reserve('active')
        self.store.rollback(reserved);self.store.rollback(active)
        self.assertEqual(self.store.capacity()['active_seats'],1)
        self.assertEqual(self.store.instance(active)['user_status'],'active')
        self.reserve('replacement')

    def test_unknown_state_fails_closed_and_provisioning_cannot_be_enabled(self):
        uid=self.reserve('pending','provisioning')
        with self.assertRaises(BetaError):self.store.set_enabled(uid,True)
        with self.store.db() as db:db.execute("UPDATE users SET status='future-state' WHERE id=?",(uid,))
        with self.assertRaises(BetaError):self.reserve('other')

    def test_twenty_seats_cannot_be_oversold_by_independent_writers(self):
        self.config.max_users=20
        stores=[GatewayStore(self.config) for _ in range(24)]
        barrier=threading.Barrier(len(stores))
        def insert(i):
            barrier.wait()
            try:stores[i].reserve('user'+str(i),'unused-test-hash',36000+i);return 200
            except BetaError as error:return error.status
        with ThreadPoolExecutor(max_workers=len(stores)) as pool:results=list(pool.map(insert,range(len(stores))))
        self.assertEqual(results.count(200),20);self.assertEqual(results.count(409),4)
        self.assertEqual(self.store.capacity()['active_seats'],20)

    def test_enable_and_registration_compete_for_same_last_seat(self):
        self.config.max_users=1;uid=self.reserve('disabled','disabled')
        other=GatewayStore(self.config);barrier=threading.Barrier(2)
        def run(enable):
            barrier.wait()
            try:
                if enable:self.store.set_enabled(uid,True)
                else:other.reserve('new-user','unused-test-hash',36500)
                return 200
            except BetaError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[True,False]))
        self.assertEqual(sorted(results),[200,409]);self.assertEqual(self.store.capacity()['active_seats'],1)

    def test_cli_reports_seats_separately_without_secret_fields(self):
        import public_beta_admin
        self.reserve('alice');self.reserve('disabled','disabled')
        out=io.StringIO()
        with patch('sys.argv',['admin','users']),patch.object(BetaConfig,'from_env',return_value=self.config),redirect_stdout(out):
            self.assertEqual(public_beta_admin.main(),0)
        report=json.loads(out.getvalue())
        self.assertEqual((report['total_users'],report['active_seats'],report['disabled_users'],report['remaining_registration_slots']),(2,1,1,1))
        self.assertNotIn('test-hash',out.getvalue())
        self.assertNotIn('password_hash',out.getvalue());self.assertNotIn('csrf',out.getvalue())

    def test_cli_reports_exact_enable_capacity_error(self):
        import public_beta_admin
        self.config.max_users=1;self.reserve('disabled','disabled');self.reserve('active')
        out=io.StringIO()
        with patch('sys.argv',['admin','enable','disabled']),patch.object(BetaConfig,'from_env',return_value=self.config),redirect_stdout(out):
            self.assertEqual(public_beta_admin.main(),1)
        self.assertEqual(out.getvalue().strip(),'Public Beta 名额已满，无法启用该用户。')

    def test_gateway_session_persists_on_registry_reopen_and_disable_revokes_it(self):
        uid=self.reserve('alice');token=self.store.session(uid)
        reopened=GatewayStore(self.config)
        self.assertEqual(reopened.principal(token)['id'],uid)
        reopened.set_enabled(uid,False)
        self.assertIsNone(self.store.principal(token))
