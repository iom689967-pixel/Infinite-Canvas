"""Email verification tests: temporary SQLite, memory mail, no real SMTP/models."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit, parse_qs

import httpx
from instance_auth import password_hash
from public_beta import create_app
from public_beta_email import EmailRegistration, RESEND_MESSAGE
from public_beta_email_address import normalize_email
from public_beta_mail import MockMailer, SMTPMailer, MailUnavailable, mailer_for
from public_beta_store import GatewayStore, BetaConfig, BetaError
from legacy_email_helpers import already_issued_link

PASSWORD='Email-test-password-2026'


def mail_token(mailer):
    return parse_qs(urlsplit(mailer.messages[-1]['link']).fragment)['token'][0]


class RegistryProvisioner:
    """No process creation; uses the actual transactional seat reservation and activation."""
    def __init__(self,store):
        self.store=store;self.port=41000;self.lock=threading.Lock();self.calls=[]

    def provision(self,name,encoded):
        with self.lock:
            self.port+=1;port=self.port
        row=self.store.reserve(name,encoded,port)
        self.store.activate(row['user_id']);self.calls.append(row['user_id']);return row['user_id']


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mio-email-test-');root=Path(self.tmp.name)
        self.cfg=BetaConfig(root/'gateway',root/'instances',mail_mode='mock',min_free_disk=0,
                            register_limit=100,login_limit=100)
        self.store=GatewayStore(self.cfg);self.mail=MockMailer();self.supervisor=RegistryProvisioner(self.store)
        self.emails=EmailRegistration(self.store,self.supervisor,self.mail)

    def tearDown(self): self.tmp.cleanup()

    def register(self,name='alice',email=None):
        result=self.emails.register(email or name+'@example.test',name,PASSWORD,PASSWORD,'local')
        # Preserve link-only migration coverage without generating production link mail.
        token=already_issued_link(self.store,self.mail,self.user(name)['id'])
        return result.data,token

    def user(self,name='alice'):
        with self.store.db() as db:return dict(db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone())

    def verify(self,token):return self.emails.verify(token,'local')

    def legacy(self,name='legacy'):
        uid=self.supervisor.provision(name,password_hash(PASSWORD));return uid

    def test_registration_is_pending_masked_and_no_session(self):
        result,_=self.register()
        self.assertEqual(result['status'],'pending_verification');self.assertEqual(result['masked_email'],'a***@example.test')
        self.assertEqual(self.user()['email_status'],'pending')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)

    def test_pending_does_not_create_instance_or_consume_seat(self):
        self.register()
        self.assertEqual(self.store.capacity()['active_seats'],0)
        self.assertEqual(self.supervisor.calls,[]);self.assertEqual(list(self.cfg.instances_root.iterdir()),[])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],0)

    def test_invalid_email_rejected_by_library(self):
        for email in ('a','x@localhost','a b@example.com','x\r\nBcc:x@example.com','x@127.0.0.1'):
            with self.subTest(email=email),self.assertRaises(BetaError):self.register(email=email)

    def test_whitespace_case_and_idna_normalization(self):
        a=normalize_email(' A@EXAMPLE.COM ');b=normalize_email('a@example.com')
        self.assertEqual(a[1],b[1]);self.assertEqual(a[0],'A@example.com')
        self.assertEqual(normalize_email('a@bücher.de')[1],'a@xn--bcher-kva.de')

    def test_email_case_uniqueness(self):
        self.register(email='A@EXAMPLE.COM')
        with self.assertRaises(BetaError):self.register('bob','a@example.com')
        self.assertEqual(self.store.capacity()['total_users'],1)

    def test_duplicate_email_and_username_do_not_create_rows(self):
        self.register()
        for name,email in [('bob','alice@example.test'),('alice','other@example.test')]:
            with self.assertRaises(BetaError):self.register(name,email)
        self.assertEqual(self.store.capacity()['total_users'],1)

    def test_registration_requires_twelve_characters(self):
        for password in ('123456','12345678901'):
            with self.assertRaises(BetaError):self.emails.register('a@example.test','alice',password,password,'local')

    def test_pending_cap_does_not_change_active_seat_limit(self):
        self.cfg.max_pending_registrations=1;self.register()
        with self.assertRaises(BetaError) as error:self.register('bob')
        self.assertEqual(error.exception.status,429);self.assertEqual(self.store.capacity()['active_seats'],0)

    def test_pending_ip_registration_rate_limit(self):
        self.cfg.register_limit=1;self.register()
        with self.assertRaises(BetaError) as error:self.register('bob')
        self.assertEqual(error.exception.status,429)

    def test_pending_ttl_cleanup_does_not_delete_legacy_or_sessions(self):
        uid=self.legacy();session=self.store.session(uid);self.register()
        with patch('public_beta_email.time.time',return_value=time.time()+self.cfg.pending_ttl+1):
            self.register('bob')
        with self.store.db() as db:self.assertIsNone(db.execute("SELECT id FROM users WHERE username='alice'").fetchone())
        self.assertIsNotNone(self.store.principal(session))

    def test_token_has_256_bits_and_only_hash_is_stored(self):
        _,token=self.register()
        self.assertEqual(len(token),43)
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM account_tokens')]
            dump='\n'.join(db.iterdump())
        self.assertEqual(rows[0]['digest'],hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token,dump);self.assertNotIn(PASSWORD,dump)

    def test_valid_token_provisions_same_user_and_no_weak_session(self):
        _,token=self.register();uid=self.user()['id']
        self.assertEqual(self.verify(token)['status'],'verified')
        self.assertEqual(self.user()['id'],uid);self.assertEqual(self.user()['status'],'active')
        self.assertEqual(self.supervisor.calls,[uid])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)

    def test_expired_token_does_not_provision(self):
        _,token=self.register()
        with patch('public_beta_email.time.time',return_value=time.time()+self.cfg.verification_ttl+1):
            self.assertEqual(self.verify(token)['status'],'expired')
        self.assertEqual(self.supervisor.calls,[])

    def test_token_single_use(self):
        _,token=self.register();self.verify(token)
        self.assertEqual(self.verify(token)['status'],'used');self.assertEqual(len(self.supervisor.calls),1)

    def test_invalid_token_and_wrong_purpose(self):
        self.assertEqual(self.verify('invalid')['status'],'invalid')
        _,token=self.register()
        with self.store.db() as db:db.execute("UPDATE account_tokens SET purpose='reset_password'")
        self.assertEqual(self.verify(token)['status'],'invalid')

    def test_retired_resend_cannot_issue_new_legacy_links(self):
        _,old=self.register();response=self.emails.resend('alice@example.test','local')
        self.assertEqual(response,{'detail':RESEND_MESSAGE});self.assertEqual(len(self.mail.messages),1)
        with patch('public_beta_email.time.time',return_value=time.time()+self.cfg.resend_cooldown+1):
            self.emails.resend('alice@example.test','local')
        self.assertEqual(len(self.mail.messages),1)
        self.assertEqual(self.verify(old)['status'],'verified')

    def test_resend_unknown_invalid_verified_are_indistinguishable(self):
        _,token=self.register();self.verify(token)
        for email in ('alice@example.test','missing@example.test','invalid'):
            self.assertEqual(self.emails.resend(email,'local'),{'detail':RESEND_MESSAGE})
        self.assertEqual(len(self.mail.messages),1)

    def test_resend_limit_is_generic_and_no_extra_mail(self):
        self.cfg.resend_limit=1;self.register()
        self.emails.resend('alice@example.test','local')
        with patch('public_beta_email.time.time',return_value=time.time()+61):
            self.assertEqual(self.emails.resend('alice@example.test','local'),{'detail':RESEND_MESSAGE})
        self.assertEqual(len(self.mail.messages),1)

    def test_full_beta_keeps_verified_user_without_instance(self):
        self.cfg.max_users=1;self.legacy();_,token=self.register()
        self.assertEqual(self.verify(token)['status'],'full')
        self.assertEqual(self.user()['status'],'verified_waiting');self.assertIsNotNone(self.user()['email_verified_at'])
        self.assertEqual(self.store.capacity()['active_seats'],1)
        with self.assertRaises(BetaError):self.store.instance(self.user()['id'])

    def test_two_concurrent_verifications_cannot_exceed_twenty_seats(self):
        for n in range(19):self.legacy('legacy'+str(n))
        _,a=self.register();_,b=self.register('bob')
        with ThreadPoolExecutor(max_workers=2) as pool:
            states=list(pool.map(lambda token:self.verify(token)['status'],[a,b]))
        self.assertEqual(sorted(states),['full','verified']);self.assertEqual(self.store.capacity()['active_seats'],20)

    def test_closed_registration_never_provisions_verified_pending(self):
        _,token=self.register();self.cfg.registration_mode='closed'
        self.assertEqual(self.verify(token)['status'],'waiting');self.assertEqual(self.store.capacity()['active_seats'],0)

    def test_email_login_and_case_are_consistent(self):
        _,token=self.register();self.verify(token)
        session=self.store.login(' ALICE@EXAMPLE.TEST ',PASSWORD,'local')
        self.assertEqual(self.store.principal(session)['username'],'alice')

    def test_new_account_cannot_use_username_login(self):
        _,token=self.register();self.verify(token)
        with self.assertRaises(BetaError) as e:self.store.login('alice',PASSWORD,'local')
        self.assertEqual(e.exception.message,'邮箱/用户名或密码错误')

    def test_login_errors_do_not_enumerate_accounts(self):
        _,token=self.register();self.verify(token)
        for email in ('alice@example.test','missing@example.test'):
            with self.assertRaises(BetaError) as e:self.store.login(email,'incorrect','local')
            self.assertEqual(e.exception.message,'邮箱/用户名或密码错误')

    def test_unverified_login_requires_correct_password_before_status_disclosure(self):
        self.register()
        with self.assertRaises(BetaError) as e:self.store.login('alice@example.test',PASSWORD,'local')
        self.assertEqual(e.exception.message,'请先完成邮箱验证。')
        with self.assertRaises(BetaError) as e:self.store.login('alice@example.test','bad','local')
        self.assertEqual(e.exception.status,401)

    def test_legacy_username_and_session_survive_email_binding(self):
        uid=self.legacy();session=self.store.login('LEGACY',PASSWORD,'local')
        self.emails.set_email(uid,'legacy@example.test')
        self.assertIsNotNone(self.store.principal(session))
        self.assertIsNotNone(self.store.principal(self.store.login('legacy',PASSWORD,'local')))
        self.emails.admin_verify(uid)
        self.assertIsNotNone(self.store.principal(session))
        self.assertIsNotNone(self.store.principal(self.store.login('legacy@example.test',PASSWORD,'local')))

    def test_admin_binding_does_not_guess_or_print_email_or_token(self):
        uid=self.legacy();self.assertIsNone(self.user('legacy')['email'])
        result=self.emails.set_email(uid,'legacy@example.test')
        self.assertEqual(result,{'masked_email':'l***@example.test','email_status':'pending'})
        self.assertFalse(self.emails.send(uid));old=already_issued_link(self.store,self.mail,uid);self.emails.admin_verify(uid)
        self.assertEqual(self.verify(old)['status'],'expired')

    def test_provision_failure_keeps_verified_identity_and_releases_seat(self):
        _,token=self.register()
        def fail(name,encoded):
            row=self.store.reserve(name,encoded,44001);self.store.rollback(row['user_id']);raise BetaError('mock failure',503)
        with patch.object(self.supervisor,'provision',side_effect=fail):
            self.assertEqual(self.verify(token)['status'],'waiting')
        self.assertEqual(self.user()['status'],'verified_waiting');self.assertEqual(self.store.capacity()['active_seats'],0)
        self.assertEqual(self.emails.provision(self.user()['id']),'verified')

    def test_smtp_failure_is_safe_and_invalidates_delivery_token(self):
        with patch.object(self.mail,'send',side_effect=MailUnavailable('sensitive-test-message')):
            with self.assertRaises(BetaError) as e:self.register()
        self.assertEqual(e.exception.message,'邮件服务暂不可用，请稍后重试。')
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT code_hmac FROM email_code_pending').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],0)

    def test_missing_mail_configuration_fails_before_pending_creation(self):
        self.cfg.mail_mode='disabled';self.emails.mailer=mailer_for(self.cfg)
        with self.assertRaises(BetaError) as e:self.register(email='alice@example.org')
        self.assertEqual(e.exception.status,503);self.assertEqual(self.store.capacity()['total_users'],0)

    def test_production_never_uses_mock_from_environment(self):
        self.cfg.external_origin='https://example.org'
        with self.assertRaises(MailUnavailable):mailer_for(self.cfg).ready()

    def test_token_password_and_mail_body_are_not_security_logs(self):
        capture=StringIO()
        with redirect_stdout(capture):
            _,token=self.register();self.verify(token)
        with self.store.db() as db:events=json.dumps([dict(r) for r in db.execute('SELECT * FROM security_events')])
        for private in (PASSWORD,token,'alice@example.test','欢迎使用 Mio Canvas'):
            self.assertNotIn(private,capture.getvalue()+events)

    def test_verification_link_has_no_query(self):
        self.register();parts=urlsplit(self.mail.messages[-1]['link'])
        self.assertEqual(parts.query,'');self.assertTrue(parts.fragment.startswith('token='))

    def test_migration_is_versioned_idempotent_preserves_legacy_sessions(self):
        # Build the actual six-column legacy schema in a separate registry, then upgrade.
        root=Path(self.tmp.name)/'legacy-db';cfg=BetaConfig(root,Path(self.tmp.name)/'legacy-instances')
        path=root/'gateway.sqlite3';uid=secrets.token_hex(16);token=secrets.token_urlsafe(32)
        encoded=password_hash(PASSWORD)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,last_login_at REAL)')
            db.execute("INSERT INTO users VALUES (?,?,?,'active',1,NULL)",(uid,'legacy',encoded))
            db.execute('CREATE TABLE sessions(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL,csrf TEXT NOT NULL)')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(GatewayStore.digest(token),uid,time.time()+3600,'existing-csrf'))
            db.execute('''CREATE TABLE instances(instance_id TEXT PRIMARY KEY,user_id TEXT UNIQUE NOT NULL,
                instance_slug TEXT UNIQUE NOT NULL,data_root TEXT UNIQUE NOT NULL,assigned_port INTEGER UNIQUE NOT NULL,
                status TEXT NOT NULL,created_at REAL NOT NULL,last_started_at REAL,pid INTEGER,process_started TEXT)''')
            db.execute("INSERT INTO instances VALUES ('legacy-instance',?,'legacy-instance',?,42000,'running',1,2,123,'synthetic-start')",(uid,str(cfg.instances_root/'legacy-instance')))
        for _ in range(2):
            migrated=GatewayStore(cfg);self.assertEqual(migrated.principal(token)['id'],uid)
            with migrated.db() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
                row=db.execute('SELECT * FROM users').fetchone()
                self.assertEqual(row['password_hash'],encoded);self.assertIsNone(row['email']);self.assertEqual(row['legacy_username_login'],1)
                instance=db.execute('SELECT * FROM instances').fetchone()
                self.assertEqual(instance['user_id'],uid);self.assertEqual(instance['pid'],123)
                self.assertEqual(instance['data_root'],str(cfg.instances_root/'legacy-instance'))

    def test_cli_masks_email_and_explicit_verify_only(self):
        import public_beta_admin
        uid=self.legacy();self.emails.set_email(uid,'legacy@example.test')
        capture=StringIO()
        with patch('public_beta_admin.BetaConfig.from_env',return_value=self.cfg),patch('sys.argv',['admin','users']),redirect_stdout(capture):
            self.assertEqual(public_beta_admin.main(),0)
        self.assertIn('l***@example.test',capture.getvalue());self.assertNotIn('legacy@example.test',capture.getvalue())
        self.assertIsNone(self.user('legacy')['email_verified_at'])

    def test_smtp_tls_and_multipart_without_debug_output(self):
        env={'MIO_SMTP_HOST':'smtp.example.org','MIO_SMTP_USERNAME':'test-user','MIO_SMTP_PASSWORD':'synthetic-smtp-secret','MIO_SMTP_FROM':'mio@example.org'}
        mail=SMTPMailer(env)
        with patch('public_beta_mail.smtplib.SMTP') as factory:
            client=factory.return_value.__enter__.return_value
            mail.send('a@example.org','038421',10)
            client.starttls.assert_called_once();client.login.assert_called_once();client.set_debuglevel.assert_not_called()
            message=client.send_message.call_args.args[0]
            self.assertTrue(message.is_multipart());self.assertNotIn('synthetic-smtp-secret',message.as_string())
        with patch('public_beta_mail.smtplib.SMTP',side_effect=OSError('private-network-message')):
            with self.assertRaises(MailUnavailable) as error:mail.send('a@example.org','038421',10)
            self.assertEqual(str(error.exception),'')


class EmailAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mio-email-api-');root=Path(self.tmp.name)
        self.cfg=BetaConfig(root/'g',root/'i',mail_mode='mock',min_free_disk=0)
        self.app=create_app(self.cfg);self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin})

    async def asyncTearDown(self):await self.client.aclose();self.tmp.cleanup()

    async def test_new_register_requires_email_and_returns_no_login_cookie(self):
        payload={'username':'alice','password':PASSWORD,'confirmation':PASSWORD}
        self.assertEqual((await self.client.post('/api/beta/register',json=payload)).status_code,400)
        response=await self.client.post('/api/beta/register',json={**payload,'email':'alice@example.test'})
        self.assertEqual(response.status_code,200);self.assertNotIn('mio_beta_session',response.headers['set-cookie'])
        for path in ('/api/beta/me','/api/canvases','/api/instance/provider-settings'):
            self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_public_pages_no_token_echo_and_private_cache(self):
        for path in ('/register','/login','/verify-email?token=synthetic-secret','/resend-verification'):
            response=await self.client.get(path)
            self.assertEqual(response.status_code,200);self.assertNotIn('synthetic-secret',response.text)
            self.assertEqual(response.headers['cache-control'],'no-store, private')
        html=(await self.client.get('/verify-email')).text
        self.assertIn("history.replaceState(null,'','/verify-email')",html);self.assertNotIn('localStorage',html)

    async def test_origin_guard_and_verify_request_fields(self):
        r=await self.client.post('/api/beta/verify-email',headers={'Origin':'https://evil.example'},json={'token':'x'})
        self.assertEqual(r.status_code,403)
        self.assertEqual((await self.client.post('/api/beta/verify-email',json={'token':'x','user_id':'other'})).status_code,400)

    async def test_unverified_login_has_no_session_cookie(self):
        await self.client.post('/api/beta/register',json={'email':'alice@example.test','username':'alice','password':PASSWORD,'confirmation':PASSWORD})
        response=await self.client.post('/api/beta/login',json={'identifier':'alice@example.test','password':PASSWORD})
        self.assertEqual(response.status_code,403);self.assertNotIn('set-cookie',response.headers)

    async def test_logout_closed_websocket_cleanup_does_not_escape_to_logs(self):
        from types import SimpleNamespace
        from starlette.websockets import WebSocketDisconnect
        store=self.app.state.store
        row=store.reserve('legacy','test-hash-not-used',41999);store.activate(row['user_id'])
        token=store.session(row['user_id'])
        async def already_closed(**kwargs): raise WebSocketDisconnect(code=1006)
        socket=SimpleNamespace(cookies={'mio_beta_session':token},
            headers={'origin':self.cfg.origin,'host':self.cfg.public_host},
            scope={'type':'websocket','scheme':'ws','client':('127.0.0.1',40000)},close=already_closed)
        endpoint=next(route.endpoint for route in self.app.routes if route.path=='/ws/stats')
        with patch('websockets.asyncio.client.connect',side_effect=OSError('synthetic disconnect')):
            await endpoint(socket)


if __name__=='__main__':unittest.main()
