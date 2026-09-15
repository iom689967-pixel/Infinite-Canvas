"""Synthetic codes only. No external mail/model calls or production data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout,redirect_stderr
import hashlib
import hmac
from io import StringIO
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch

import httpx
from public_beta import create_app
from public_beta_email import EmailRegistration
from public_beta_email_codes import PENDING_COOKIE,EXPIRED,INVALIDATED,MAX_RATE_BUCKETS,MAX_CODE_HISTORY
from public_beta_mail import MockMailer,MailUnavailable,ResendMailer,SMTPMailer,verification_content,mailer_for
from public_beta_store import BetaConfig,GatewayStore,BetaError
from legacy_email_helpers import legacy_pending

PASSWORD='Synthetic-code-password-2026'
CURRENT_CODE=object()


class CodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mio-code-unit-');self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.cfg=BetaConfig(root/'g',root/'i',mail_mode='mock',min_free_disk=0,register_limit=100,login_limit=100)
        self.store=GatewayStore(self.cfg);self.mail=MockMailer()
        self.emails=EmailRegistration(self.store,None,self.mail);self.codes=self.emails.codes
        self.now=time.time();clock=patch('public_beta_email_codes.time.time',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)

    def register(self,name='alice',email=None):
        return self.emails.register(email or name+'@example.test',name,PASSWORD,PASSWORD,'loopback')

    def code(self):return self.mail.messages[-1]['code']

    def verify(self,registration,code=CURRENT_CODE):
        return self.codes.verify(registration.data['pending_id'],registration.cookie,self.code() if code is CURRENT_CODE else code,'loopback')

    def resend(self,registration):return self.codes.resend(registration.data['pending_id'],registration.cookie,'loopback')

    def row(self,registration):
        with self.store.db() as db:
            return dict(db.execute('SELECT p.*,u.email_normalized FROM email_code_pending p JOIN users u ON p.user_id=u.id WHERE pending_digest=?',
                                   (self.codes.identity(registration.data['pending_id'],registration.cookie)[0],)).fetchone())

    def wrong(self):return '000001' if self.code()!='000001' else '000002'

    def test_six_digits_secure_rng(self):
        with patch('public_beta_email_codes.secrets.randbelow',return_value=38421) as rng:
            self.register();self.assertEqual(self.code(),'038421');rng.assert_called_once_with(1_000_000)

    def test_zero_and_upper_boundary(self):
        with patch('public_beta_email_codes.secrets.randbelow',side_effect=[0,999999]):
            self.register();self.assertEqual(self.code(),'000000')
            self.register('bob');self.assertEqual(self.code(),'999999')

    def test_only_keyed_hash_in_sqlite(self):
        with patch('public_beta_email_codes.secrets.randbelow',return_value=38421):r=self.register()
        row=self.row(r)
        self.assertEqual(row['code_hmac'],self.codes.code_hmac(row,self.code()))
        self.assertNotEqual(row['code_hmac'],hashlib.sha256(self.code().encode()).hexdigest())
        with self.store.db() as db:dump='\n'.join(db.iterdump())
        for secret in (self.code(),PASSWORD,r.cookie,self.store.key.hex(),self.codes.key.hex()):self.assertNotIn(secret,dump)

    def test_hash_binds_email_identity_and_generation(self):
        r=self.register();row=self.row(r)
        changed={**row,'email_normalized':'other@example.test'}
        self.assertNotEqual(self.codes.code_hmac(changed,self.code()),row['code_hmac'])
        self.assertNotEqual(self.codes.code_hmac(row,self.code(),row['generation']+1),row['code_hmac'])

    def test_hash_domain_separated_from_handoff(self):
        self.assertEqual(self.codes.key,hmac.new(self.store.key,b'mio-email-code-v1',hashlib.sha256).digest())
        self.assertNotEqual(self.codes.key,self.store.key)

    def test_defaults_and_config_env(self):
        self.assertEqual((self.cfg.email_code_ttl,self.cfg.email_code_max_attempts,self.cfg.email_code_resend_seconds),(600,5,60))
        root=Path(self.tmp.name)
        with patch.dict(os.environ,{'PUBLIC_BETA_ROOT':str(root/'g2'),'PUBLIC_BETA_INSTANCES_ROOT':str(root/'i2'),
            'PUBLIC_BETA_BACKUP_ROOT':str(root/'b2'),'MIO_EMAIL_CODE_TTL_SECONDS':'900','MIO_EMAIL_CODE_MAX_ATTEMPTS':'4',
            'MIO_EMAIL_CODE_RESEND_SECONDS':'75','MIO_EMAIL_CODE_HOURLY_LIMIT':'5'},clear=True):
            cfg=BetaConfig.from_env();self.assertEqual((cfg.email_code_ttl,cfg.email_code_max_attempts,cfg.email_code_resend_seconds),(900,4,75))

    def test_invalid_config_rejected(self):
        root=Path(self.tmp.name)
        for options in ({'email_code_ttl':0},{'email_code_max_attempts':11},{'email_code_resend_seconds':0}):
            with self.subTest(options=options),self.assertRaises(ValueError):BetaConfig(root/'g',root/'i',**options)

    def test_correct_code_activates_and_creates_session_without_instance(self):
        r=self.register();uid=self.row(r)['user_id'];token=self.verify(r)
        self.assertEqual(self.store.principal(token)['id'],uid)
        self.assertEqual(self.store.capacity()['active_seats'],1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],0)
        self.assertEqual(list(self.cfg.instances_root.iterdir()),[])

    def test_format_strict_ascii_string(self):
        r=self.register()
        for value in ('12345','1234567','abcdef','１２３４５６','١٢٣٤٥٦','123 45','123.45',38421,None):
            with self.subTest(value=value),self.assertRaises(BetaError) as error:self.verify(r,value)
            self.assertEqual(error.exception.message,'请输入 6 位数字验证码')
        self.assertEqual(self.row(r)['failed_attempts'],0)

    def test_wrong_code_persists_attempts(self):
        r=self.register()
        with self.assertRaises(BetaError) as error:self.verify(r,self.wrong())
        self.assertEqual(error.exception.message,'验证码错误，请重新输入');self.assertEqual(self.row(r)['failed_attempts'],1)

    def test_compare_digest_used_for_code(self):
        r=self.register();row=self.row(r)
        with patch('public_beta_email_codes.hmac.compare_digest',wraps=hmac.compare_digest) as compare:self.verify(r)
        self.assertIn(unittest.mock.call(row['code_hmac'],row['code_hmac']),compare.call_args_list)

    def test_ttl_is_ten_minutes(self):
        r=self.register();row=self.row(r);self.assertEqual(row['expires_at']-self.now,600)

    def test_ttl_exact_boundary_expired(self):
        r=self.register();self.now+=600
        with self.assertRaises(BetaError) as error:self.verify(r)
        self.assertEqual(error.exception.message,EXPIRED);self.assertEqual(self.store.capacity()['active_seats'],0)

    def test_ttl_just_before_boundary_success(self):
        r=self.register();self.now+=599.999;self.assertIsNotNone(self.store.principal(self.verify(r)))

    def test_five_wrong_attempts_invalidate_even_correct_code(self):
        r=self.register();valid=self.code();wrong=self.wrong()
        for n in range(5):
            with self.assertRaises(BetaError) as error:self.verify(r,wrong)
            self.assertEqual(error.exception.message,INVALIDATED if n==4 else '验证码错误，请重新输入')
        with self.assertRaises(BetaError) as error:self.verify(r,valid)
        self.assertEqual(error.exception.message,INVALIDATED);self.assertEqual(self.row(r)['failed_attempts'],5)

    def test_resend_resets_failed_attempts_and_ttl(self):
        r=self.register()
        with self.assertRaises(BetaError):self.verify(r,self.wrong())
        self.now+=60;self.resend(r);row=self.row(r)
        self.assertEqual(row['failed_attempts'],0);self.assertEqual(row['generation'],2);self.assertEqual(row['expires_at'],self.now+600)

    def test_cooldown_at_59_seconds_rejected(self):
        r=self.register();self.now+=59
        with self.assertRaises(BetaError) as error:self.resend(r)
        self.assertEqual(error.exception.status,429);self.assertEqual(len(self.mail.messages),1)

    def test_cooldown_exact_60_seconds_allowed(self):
        r=self.register();self.now+=60;self.resend(r);self.assertEqual(len(self.mail.messages),2)

    def test_restart_and_status_do_not_reset_cooldown(self):
        r=self.register();self.now+=30
        new=EmailRegistration(GatewayStore(self.cfg),None,self.mail).codes
        self.assertEqual(new.status(r.cookie)['resend_after_seconds'],30)
        with self.assertRaises(BetaError):new.resend(r.data['pending_id'],r.cookie,'loopback')

    def test_resend_old_code_rejected(self):
        r=self.register();old=self.code();self.now+=60;self.resend(r)
        with self.assertRaises(BetaError):self.verify(r,old)
        self.assertIsNotNone(self.store.principal(self.verify(r)))

    def test_generation_increases_and_late_old_mail_is_invalid(self):
        r=self.register();old=self.code()
        for _ in range(3):self.now+=60;self.resend(r)
        newest=self.code();self.assertEqual(self.row(r)['generation'],4)
        with self.assertRaises(BetaError):self.verify(r,old)
        self.verify(r,newest)

    def test_rng_collision_does_not_reissue_previous_code(self):
        with patch('public_beta_email_codes.secrets.randbelow',side_effect=[38421,38421,100001]):
            r=self.register();self.now+=60;self.resend(r);self.assertEqual(self.code(),'100001')

    def test_duplicate_resends_only_send_once(self):
        r=self.register();self.now+=60
        def attempt(_):
            try:self.resend(r);return True
            except BetaError:return False
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(attempt,range(2)))
        self.assertEqual(result.count(True),1);self.assertEqual(len(self.mail.messages),2)

    def test_concurrent_verify_only_one_session_and_seat(self):
        r=self.register();code=self.code()
        def attempt(_):
            try:return self.verify(r,code)
            except BetaError:return None
        with ThreadPoolExecutor(2) as pool:tokens=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(t is not None for t in tokens),1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)
        self.assertEqual(self.store.capacity()['active_seats'],1)

    def test_verification_transaction_rolls_back_activation_and_consumption(self):
        r=self.register()
        with self.store.db() as db:db.execute("CREATE TRIGGER reject_session BEFORE INSERT ON sessions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.verify(r)
        self.assertIsNone(self.row(r)['completed_at']);self.assertEqual(self.store.capacity()['active_seats'],0)

    def test_concurrent_same_email_registration_creates_one_pending(self):
        def attempt(name):
            try:return self.register(name,'shared@example.test')
            except BetaError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['alice','bob']))
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(len(self.mail.messages),1)

    def test_username_conflict_and_case_email_conflict(self):
        self.register()
        for name,email in [('alice','other@example.test'),('bob','ALICE@EXAMPLE.TEST')]:
            with self.assertRaises(BetaError):self.register(name,email)
        self.assertEqual(self.store.capacity()['total_users'],1)

    def test_capacity_race_reserves_last_seat_once(self):
        self.cfg.max_users=1;a=self.register();ac=self.code();b=self.register('bob');bc=self.code()
        def attempt(pair):
            try:self.verify(*pair);return True
            except BetaError:return False
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[(a,ac),(b,bc)]))
        self.assertEqual(results.count(True),1);self.assertEqual(self.store.capacity()['active_seats'],1)

    def test_pending_does_not_consume_seat_running_slot_or_directory(self):
        self.register();self.assertEqual(self.store.capacity()['active_seats'],0)
        self.assertEqual(list(self.cfg.instances_root.iterdir()),[])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],0)

    def test_send_failure_cannot_activate_and_drops_raw_exception(self):
        with patch.object(self.mail,'send',side_effect=MailUnavailable('private-message')):
            with self.assertRaises(BetaError) as error:self.register()
        self.assertEqual(error.exception.message,'邮件服务暂不可用，请稍后重试。')
        self.assertIsNone(error.exception.__context__)
        with self.store.db() as db:
            row=db.execute('SELECT * FROM email_code_pending').fetchone();self.assertIsNone(row['code_hmac'])
        self.assertEqual(self.store.capacity()['active_seats'],0)

    def test_failed_send_can_reconfirm_password_to_recover(self):
        with patch.object(self.mail,'send',side_effect=MailUnavailable()):
            with self.assertRaises(BetaError):self.register()
        r=self.register();self.verify(r);self.assertEqual(self.store.capacity()['total_users'],1)

    def test_pending_not_verifiable_until_send_succeeds(self):
        started=threading.Event();finish=threading.Event();original=self.mail.send
        def slow(*args):started.set();finish.wait(3);return original(*args)
        with patch.object(self.mail,'send',side_effect=slow),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.register)
            try:
                self.assertTrue(started.wait(3))
                with self.store.db() as db:
                    row=db.execute('SELECT * FROM email_code_pending').fetchone()
                    self.assertEqual(row['delivery_state'],'sending');self.assertIsNone(row['code_hmac'])
            finally:finish.set()
            future.result()

    def test_disabled_mode_safe_failure_no_pending(self):
        self.cfg.mail_mode='disabled';self.emails.mailer=mailer_for(self.cfg)
        with self.assertRaises(BetaError):self.register(email='alice@example.org')
        self.assertEqual(self.store.capacity()['total_users'],0)

    def test_five_per_email_hour_limit_including_initial_send(self):
        r=self.register()
        for _ in range(4):self.now+=60;self.resend(r)
        self.now+=60
        with self.assertRaises(BetaError) as error:self.resend(r)
        self.assertEqual(error.exception.status,429);self.assertEqual(len(self.mail.messages),5)

    def test_cancel_and_reregister_cannot_reset_email_hour_limit(self):
        self.cfg.email_code_hourly_limit=1;r=self.register();self.codes.cancel(r.data['pending_id'],r.cookie)
        with self.assertRaises(BetaError):self.register()
        self.assertEqual(len(self.mail.messages),1)

    def test_ip_rate_limit_across_different_emails(self):
        self.cfg.email_code_ip_hourly_limit=1;self.register()
        with self.assertRaises(BetaError) as error:self.register('bob')
        self.assertEqual(error.exception.status,429);self.assertEqual(len(self.mail.messages),1)

    def test_hour_limit_expires_and_storage_is_bounded(self):
        self.cfg.email_code_hourly_limit=1;r=self.register();self.now+=3600;self.resend(r)
        with self.store.db() as db:self.assertLessEqual(db.execute('SELECT COUNT(*) FROM email_code_limits').fetchone()[0],3)

    def test_rate_storage_hard_cap_rejects_new_buckets(self):
        with self.store.db() as db:db.executemany('INSERT INTO email_code_limits VALUES (?,?,1)',[(str(n),self.now) for n in range(MAX_RATE_BUCKETS)])
        with self.assertRaises(BetaError):self.register()
        self.assertEqual(len(self.mail.messages),0)

    def test_a_b_pending_ownership(self):
        a=self.register();ac=self.code();b=self.register('bob')
        with self.assertRaises(BetaError) as error:self.codes.verify(a.data['pending_id'],b.cookie,ac,'loopback')
        self.assertEqual(error.exception.status,404)
        with self.assertRaises(BetaError):self.codes.resend(a.data['pending_id'],b.cookie,'loopback')

    def test_invalid_id_missing_owner_and_malformed_cookie(self):
        r=self.register()
        for identity,cookie in [('1',r.cookie),(r.data['pending_id'],''),(r.data['pending_id'],r.data['pending_id']+'.'+secrets.token_urlsafe(32))]:
            with self.assertRaises(BetaError) as error:self.codes.verify(identity,cookie,self.code(),'loopback')
            self.assertEqual(error.exception.status,404)

    def test_gateway_restart_pending_still_verifiable(self):
        r=self.register();new=EmailRegistration(GatewayStore(self.cfg),None,self.mail)
        token=new.codes.verify(r.data['pending_id'],r.cookie,self.code(),'loopback');self.assertIsNotNone(new.store.principal(token))

    def test_cleanup_removes_only_expired_unverified_pending(self):
        a=self.register();b=self.register('bob');token=self.verify(b);self.now+=self.cfg.pending_ttl+1
        with self.store.db() as db:self.assertEqual(self.emails.cleanup(db,self.now),1)
        self.assertIsNotNone(self.store.principal(token) if self.cfg.session_ttl>self.cfg.pending_ttl else self.row(b))
        with self.assertRaises(BetaError):self.codes.status(a.cookie)

    def test_cancel_invalidates_pending_and_allows_modified_email(self):
        r=self.register();self.codes.cancel(r.data['pending_id'],r.cookie)
        with self.assertRaises(BetaError):self.verify(r)
        new=self.register(email='other@example.test');self.verify(new)

    def test_session_revocation_does_not_affect_other_user(self):
        a=self.register();at=self.verify(a);b=self.register('bob');bt=self.verify(b)
        self.store.revoke(bt);self.assertIsNone(self.store.principal(bt));self.assertIsNotNone(self.store.principal(at))

    def test_code_and_digest_never_log_or_escape_exceptions(self):
        output=StringIO();handler=logging.StreamHandler(output);logger=logging.getLogger();logger.addHandler(handler)
        try:
            with redirect_stdout(output),redirect_stderr(output):
                with patch('public_beta_email_codes.secrets.randbelow',return_value=38421):r=self.register()
                row=self.row(r)
                with self.assertRaises(BetaError) as error:self.verify(r,'654321')
                trace=''.join(traceback.format_exception(error.exception))
                with self.store.db() as db:events=json.dumps([dict(x) for x in db.execute('SELECT * FROM security_events')])
            for private in ('038421',row['code_hmac'],self.codes.key.hex(),PASSWORD,r.cookie):self.assertNotIn(private,output.getvalue()+events+trace)
        finally:logger.removeHandler(handler)

    def test_no_debug_code_route_in_production_app(self):
        app=create_app(self.cfg,mailer=self.mail)
        paths=[route.path for route in app.routes]
        self.assertFalse(any(path.startswith(('/debug','/__test','/api/code')) for path in paths))

    def test_legacy_pending_switch_requires_same_password(self):
        uid,old=legacy_pending(self.store,None,'alice',PASSWORD)
        with self.assertRaises(BetaError):self.emails.register('alice@example.test','alice','wrong-password-2026','wrong-password-2026','loopback')
        r=self.register();self.assertEqual(self.row(r)['user_id'],uid)
        self.assertEqual(self.emails.verify(old,'loopback')['status'],'invalid');self.verify(r)

    def test_legacy_links_work_only_within_frozen_deadline(self):
        uid,old=legacy_pending(self.store,None,'alice',PASSWORD)
        self.now+=self.cfg.verification_ttl
        self.assertEqual(self.emails.verify(old,'loopback')['status'],'expired')
        with self.store.db() as db:self.assertIsNone(db.execute('SELECT email_verified_at FROM users WHERE id=?',(uid,)).fetchone()[0])

    def test_admin_cannot_issue_a_new_link(self):
        uid,_=legacy_pending(self.store,None,'alice',PASSWORD)
        self.assertFalse(self.emails.send(uid));self.assertEqual(len(self.mail.messages),0)

    def test_nonadjacent_rng_collision_cannot_revalidate_an_old_mail(self):
        with patch('public_beta_email_codes.secrets.randbelow',side_effect=[38421,555111,38421,555222]):
            r=self.register();old=self.code();self.now+=60;self.resend(r);self.now+=60;self.resend(r)
        self.assertNotEqual(self.code(),old);self.assertEqual(self.row(r)['generation'],3)
        with self.assertRaises(BetaError):self.verify(r,old)
        self.verify(r)
        self.assertEqual(self.row(r)['code_history'],'[]')

    def test_hash_history_is_bounded_and_malformed_data_does_not_escape(self):
        r=self.register();row=self.row(r);self.now+=60
        for history in (json.dumps([[1,row['code_hmac']]]*MAX_CODE_HISTORY),'{unsafe-history'):
            with self.store.db() as db:
                db.execute('UPDATE email_code_pending SET code_history=? WHERE user_id=?',(history,row['user_id']))
            with self.assertRaises(BetaError) as error:self.resend(r)
            self.assertEqual(error.exception.status,503);self.assertIsNone(error.exception.__context__)
        self.assertEqual(len(self.mail.messages),1)

    def test_real_v1_migration_freezes_original_link_expiry_across_restarts(self):
        uid,old=legacy_pending(self.store,None,'alice',PASSWORD)
        with self.store.db() as db:
            expiry=db.execute('SELECT expires_at FROM account_tokens WHERE user_id=?',(uid,)).fetchone()[0]
            for table in ('email_code_pending','email_code_limits','email_link_transition'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=1')
        GatewayStore(self.cfg)
        with self.store.db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT legacy_until FROM email_link_transition').fetchone()[0],expiry)
        self.now+=30;GatewayStore(self.cfg)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT legacy_until FROM email_link_transition').fetchone()[0],expiry)
        self.now=expiry
        self.assertEqual(self.emails.verify(old,'loopback')['status'],'expired')

    def test_failed_resend_leaves_neither_old_nor_new_code_verifiable(self):
        r=self.register();old=self.code();self.now+=60
        self.emails.mailer.send=lambda *_: (_ for _ in ()).throw(RuntimeError('unsafe mock body'))
        with self.assertRaises(BetaError) as failed:self.resend(r)
        self.assertEqual(failed.exception.status,503)
        self.assertIsNone(self.row(r)['code_hmac'])
        with self.assertRaises(BetaError) as invalid:self.verify(r,old)
        self.assertEqual(invalid.exception.message,INVALIDATED)

    def test_mail_content_is_shared_code_only(self):
        content=verification_content('038421',10)
        self.assertEqual(content['subject'],'你的 Mio Canvas 验证码')
        for body in (content['html'],content['text']):
            self.assertIn('038421',body);self.assertIn('10 分钟',body);self.assertNotIn('token=',body);self.assertNotIn('/verify',body)

    def test_smtp_and_mock_use_same_code_content(self):
        env={'MIO_SMTP_HOST':'smtp.example.org','MIO_SMTP_PORT':'465','MIO_SMTP_TLS':'ssl','MIO_SMTP_USERNAME':'synthetic',
             'MIO_SMTP_PASSWORD':'synthetic-smtp-key','MIO_MAIL_FROM':'Mio Canvas <noreply@example.org>'}
        with patch('public_beta_mail.smtplib.SMTP_SSL') as factory:
            SMTPMailer(env).send('alice@example.org','038421',10)
            message=factory.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(str(message['Subject']),verification_content('038421',10)['subject'])
            self.assertIn('038421',message.get_body(preferencelist=('plain',)).get_content())
        self.mail.send('alice@example.test','038421',10);self.assertEqual(self.code(),'038421')


class CodeAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.cfg=BetaConfig(root/'g',root/'i',mail_mode='mock',min_free_disk=0,register_limit=100,login_limit=100)
        self.app=create_app(self.cfg);self.mail=self.app.state.emails.mailer
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(self.app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin})

    async def asyncTearDown(self):
        await self.client.aclose();await asyncio.to_thread(self.app.state.supervisor.close);self.tmp.cleanup()

    async def register(self):
        return await self.client.post('/api/beta/register',json={'email':'alice@example.test','username':'alice','password':PASSWORD,'confirmation':PASSWORD})

    async def verify(self,result,**extra):
        return await self.client.post('/api/beta/verify-email-code',json={'pending_id':result.json()['pending_id'],'code':self.mail.messages[-1]['code'],**extra})

    async def test_pending_cookie_httponly_without_gateway_login(self):
        r=await self.register();self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
        self.assertNotIn('mio_beta_session',r.headers['set-cookie']);self.assertNotIn(self.mail.messages[-1]['code'],r.text)
        self.assertEqual((await self.client.get('/api/beta/me')).status_code,401)

    async def test_verify_auto_login_and_full_canvas(self):
        r=await self.register();v=await self.verify(r);self.assertEqual(v.status_code,200)
        self.assertIn('mio_beta_session',v.headers['set-cookie']);self.assertEqual((await self.client.get('/api/beta/me')).json()['username'],'alice')
        html=await self.client.get('/');self.assertEqual(html.status_code,200);self.assertIn('studioSidebar',html.text)
        me=await self.client.get('/api/beta/me')
        entered=await self.client.post('/api/beta/enter',headers={'X-CSRF-Token':me.json()['csrf']})
        self.assertEqual(entered.status_code,200)
        self.assertEqual((await self.client.get('/api/canvases')).json()['canvases'],[])

    async def test_pending_refresh_is_private_and_preserves_deadline(self):
        r=await self.register();status=await self.client.get('/api/beta/email-code-pending')
        self.assertEqual(status.json()['pending_id'],r.json()['pending_id']);self.assertGreater(status.json()['resend_after_seconds'],0)
        self.assertEqual(status.headers['cache-control'],'no-store, private')

    async def test_origin_body_size_and_untrusted_fields(self):
        r=await self.register();pending=r.json()['pending_id']
        bad=await self.client.post('/api/beta/verify-email-code',headers={'Origin':'https://evil.example'},json={'pending_id':pending,'code':'123456'})
        self.assertEqual(bad.status_code,403)
        self.assertEqual((await self.verify(r,email='attacker@example.test')).status_code,400)
        self.assertEqual((await self.client.post('/api/beta/verify-email-code',json={'pending_id':pending,'code':'x'*9000})).status_code,413)

    async def test_direct_resend_and_second_tab_cannot_bypass_server_cooldown(self):
        r=await self.register();pending=r.json()['pending_id']
        for _ in range(2):
            response=await self.client.post('/api/beta/resend-email-code',json={'pending_id':pending})
            self.assertEqual(response.status_code,429)
        self.assertEqual(len(self.mail.messages),1)

    async def test_pending_id_without_cookie_does_not_authorize(self):
        r=await self.register()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin}) as b:
            response=await b.post('/api/beta/verify-email-code',json={'pending_id':r.json()['pending_id'],'code':self.mail.messages[-1]['code']})
            self.assertEqual(response.status_code,404)

    async def test_consumed_cookie_cannot_repeat_verification(self):
        r=await self.register();cookie=self.client.cookies.get(PENDING_COOKIE);code=self.mail.messages[-1]['code']
        await self.verify(r)
        self.client.cookies.clear();self.client.cookies.set(PENDING_COOKIE,cookie)
        repeated=await self.client.post('/api/beta/verify-email-code',json={'pending_id':r.json()['pending_id'],'code':code})
        self.assertEqual(repeated.status_code,404);self.assertNotIn('set-cookie',repeated.headers)

    async def test_logout_after_code_login_private_apis_401(self):
        r=await self.register();await self.verify(r)
        csrf=(await self.client.get('/api/beta/me')).json()['csrf']
        self.assertEqual((await self.client.post('/api/beta/logout',headers={'X-CSRF-Token':csrf})).status_code,200)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app),base_url=self.cfg.origin) as fresh:
            for path in ('/api/beta/me','/api/canvases','/api/instance/provider-settings','/assets/private.png'):
                self.assertEqual((await fresh.get(path)).status_code,401)

    async def test_autofill_paste_leading_zero_markup_and_no_storage(self):
        page=(await self.client.get('/register')).text
        for value in ('inputmode="numeric"','autocomplete="one-time-code"','maxlength="6"','返回修改邮箱','验证并进入'):
            self.assertIn(value,page)
        self.assertNotIn('localStorage',page);self.assertNotIn('sessionStorage',page);self.assertNotIn('token=',page)

    async def test_active_session_write_still_requires_csrf(self):
        r=await self.register();await self.verify(r)
        response=await self.client.post('/api/beta/resend-email-code',json={'pending_id':r.json()['pending_id']})
        self.assertEqual(response.status_code,403)

    async def test_init_failure_keeps_verified_seat_and_session_for_retry(self):
        r=await self.register();await self.verify(r)
        with patch('public_beta_supervisor.subprocess.run',side_effect=OSError('synthetic')):
            response=await self.client.get('/')
            self.assertEqual(response.status_code,503)
        self.assertEqual((await self.client.get('/api/beta/me')).status_code,200)
        self.assertEqual(self.app.state.store.capacity()['active_seats'],1)
        self.assertEqual((await self.client.get('/')).status_code,200)

    async def test_two_first_visits_provision_only_one_instance(self):
        r=await self.register();await self.verify(r)
        a,b=await asyncio.gather(self.client.get('/'),self.client.get('/'))
        self.assertEqual((a.status_code,b.status_code),(200,200))
        with self.app.state.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],1)

    async def test_last_verified_seat_can_provision_without_reserving_twice(self):
        self.cfg.max_users=1
        r=await self.register();await self.verify(r)
        self.assertEqual(self.app.state.store.capacity()['active_seats'],1)
        self.assertEqual((await self.client.get('/')).status_code,200)
        self.assertEqual(self.app.state.store.capacity()['active_seats'],1)


if __name__=='__main__':unittest.main()
