"""No external mail: actual Resend backend with httpx.MockTransport only."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch

import httpx

from public_beta import create_app
from public_beta_mail import (MAIL_UNAVAILABLE, MailUnavailable, MockMailer, SMTPMailer,
                              ResendMailer, mailer_for, parse_sender)
from public_beta_store import BetaConfig
from public_beta_supervisor import Supervisor

ENV={'MIO_RESEND_API_KEY':'synthetic-resend-key-only',
     'MIO_MAIL_FROM':'Mio Canvas <noreply@mio-canvas.eu.cc>'}
DOMAIN='mio-canvas.eu.cc'
LINK='https://mio-canvas.eu.cc/verify-email#token=synthetic-verification-token'


class SenderTests(unittest.TestCase):
    def test_legacy_mail_error_details_are_accepted_but_not_retained(self):
        error=MailUnavailable('synthetic-private-error',{'authorization':'synthetic-secret'})
        self.assertEqual(error.args,());self.assertEqual(str(error),'')
        self.assertNotIn('synthetic-private-error',repr(error));self.assertNotIn('synthetic-secret',repr(vars(error)))

    def test_display_name(self):
        self.assertEqual(parse_sender(ENV['MIO_MAIL_FROM'],DOMAIN),
                         (ENV['MIO_MAIL_FROM'],'noreply@mio-canvas.eu.cc'))

    def test_bare_address(self):
        self.assertEqual(parse_sender('noreply@mio-canvas.eu.cc',DOMAIN),
                         ('noreply@mio-canvas.eu.cc','noreply@mio-canvas.eu.cc'))

    def test_crlf_and_control_characters_rejected(self):
        for value in ('Mio\r\nBcc: attacker@example.org','Mio\n <noreply@mio-canvas.eu.cc>',
                      'Mio\x00 <noreply@mio-canvas.eu.cc>'):
            with self.subTest(value=repr(value)),self.assertRaises(MailUnavailable):parse_sender(value,DOMAIN)

    def test_multiple_group_malformed_or_invalid_address_rejected(self):
        for value in ('a@example.org,b@example.org','Group: a@example.org;',
                      'Mio Canvas noreply@mio-canvas.eu.cc','Mio <invalid>', 'Mio <x@localhost>', ''):
            with self.subTest(value=value),self.assertRaises(MailUnavailable):parse_sender(value,DOMAIN)

    def test_sender_domain_must_match_exactly(self):
        for value in ('x@attacker.example.org','x@sub.mio-canvas.eu.cc','x@mio-canvas.eu.cc.attacker.org'):
            with self.subTest(value=value),self.assertRaises(MailUnavailable):parse_sender(value,DOMAIN)

    def test_case_idna_and_quoted_name(self):
        self.assertEqual(parse_sender('"Mio, Canvas" <X@BÜCHER.DE>','bücher.de'),
                         ('"Mio, Canvas" <x@xn--bcher-kva.de>','x@xn--bcher-kva.de'))

    def test_smtp_display_name_and_ssl_still_work(self):
        env={**ENV,'MIO_SMTP_HOST':'smtp.example.org','MIO_SMTP_PORT':'465',
             'MIO_SMTP_USERNAME':'synthetic-user','MIO_SMTP_PASSWORD':'synthetic-smtp-secret','MIO_SMTP_TLS':'ssl'}
        with patch('public_beta_mail.smtplib.SMTP_SSL') as factory:
            mail=SMTPMailer(env,sending_domain=DOMAIN);mail.send('alice@example.org',LINK,60)
            client=factory.return_value.__enter__.return_value
            message=client.send_message.call_args.args[0]
            self.assertEqual(str(message['From']),ENV['MIO_MAIL_FROM'])
            self.assertEqual(client.send_message.call_args.kwargs['from_addr'],'noreply@mio-canvas.eu.cc')
            self.assertTrue(message.is_multipart());client.set_debuglevel.assert_not_called()
            context=factory.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)

    def test_existing_mock_backend_unchanged(self):
        mail=MockMailer();mail.send('alice@example.test',LINK,60)
        self.assertEqual(mail.messages[-1]['link'],LINK)

    def test_new_mode_loaded_from_env_and_smtp_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            env={**ENV,'MIO_MAIL_MODE':'resend','PUBLIC_BETA_ROOT':str(root/'g'),
                 'PUBLIC_BETA_INSTANCES_ROOT':str(root/'i'),'PUBLIC_BETA_BACKUP_ROOT':str(root/'b'),
                 'PUBLIC_BETA_ORIGIN':'https://'+DOMAIN,'PUBLIC_BETA_TRUSTED_PROXIES':'127.0.0.1',
                 'MIO_SMTP_HOST':'invalid smtp config',
                 'MIO_SMTP_PORT':'invalid','MIO_SMTP_PASSWORD':'do-not-use-this-smtp-secret'}
            with patch.dict(os.environ,env,clear=True):
                cfg=BetaConfig.from_env();self.assertEqual(cfg.mail_mode,'resend')
                mail=mailer_for(cfg);self.assertIsInstance(mail,ResendMailer);mail.ready()
                self.assertEqual(mail.sending_domain,DOMAIN)
                self.assertNotIn('do-not-use-this-smtp-secret',repr(vars(mail)))

    def test_explicit_sending_domain_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cfg=BetaConfig(root/'g',root/'i',mail_mode='resend')
            with patch.dict(os.environ,{**ENV,'MIO_MAIL_SENDING_DOMAIN':DOMAIN},clear=True):
                mailer_for(cfg).ready()

    def test_resend_secret_not_in_instance_env(self):
        supervisor=object.__new__(Supervisor)
        supervisor.config=type('Config',(),{'mock_upstreams':''})()
        with patch.dict(os.environ,ENV):
            env=supervisor.env({'instance_id':'synthetic','data_root':'/temporary-test','assigned_port':42000})
        self.assertNotIn('MIO_RESEND_API_KEY',env);self.assertNotIn(ENV['MIO_RESEND_API_KEY'],str(env))


class ResendTests(unittest.IsolatedAsyncioTestCase):
    def mail(self,handler,env=None):
        return ResendMailer(ENV if env is None else env,sending_domain=DOMAIN,transport=httpx.MockTransport(handler))

    async def test_fixed_endpoint_authorization_and_all_fields(self):
        requests=[]
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url),'https://api.resend.com/emails')
            self.assertEqual(request.method,'POST')
            self.assertEqual(request.headers['authorization'],'Bearer '+ENV['MIO_RESEND_API_KEY'])
            self.assertEqual(request.headers['content-type'],'application/json')
            body=json.loads(request.content)
            self.assertEqual(set(body),{'from','to','subject','html','text'})
            self.assertEqual(body['from'],ENV['MIO_MAIL_FROM']);self.assertEqual(body['to'],['alice@example.org'])
            self.assertIn(LINK,body['text']);self.assertIn('验证邮箱',body['html'])
            return httpx.Response(202)
        mail=self.mail(handler,{**ENV,'MIO_RESEND_API_URL':'https://attacker.example.org'})
        await mail.asend('alice@example.org',LINK,60);self.assertEqual(len(requests),1)

    async def test_missing_key_fails_without_http(self):
        calls=[]
        for key in ('',' ','x\r\ny','x\x00y'):
            mail=self.mail(lambda r:calls.append(r),{**ENV,'MIO_RESEND_API_KEY':key})
            with self.assertRaises(MailUnavailable):await mail.asend('alice@example.org',LINK,60)
        self.assertEqual(calls,[])

    async def test_any_2xx_succeeds_without_reading_response(self):
        class Unreadable(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError('mail response body must not be consumed')
                yield b''
        for status in (200,201,202,204,299):
            with self.subTest(status=status):
                await self.mail(lambda r:httpx.Response(status,stream=Unreadable())).asend('alice@example.org',LINK,60)

    async def safe_status(self,status):
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(status,text='private-response-'+ENV['MIO_RESEND_API_KEY'])
        with self.assertRaises(MailUnavailable) as error:await self.mail(handler).asend('alice@example.org',LINK,60)
        self.assertEqual(error.exception.status_code,status);self.assertEqual(error.exception.provider,'resend')
        self.assertEqual(str(error.exception),'');self.assertIsNone(error.exception.__context__)
        self.assertEqual(len(calls),1)

    async def test_400_safe_failure(self):await self.safe_status(400)
    async def test_401_safe_failure(self):await self.safe_status(401)
    async def test_429_safe_failure_without_retry(self):await self.safe_status(429)
    async def test_500_safe_failure(self):await self.safe_status(500)

    async def test_redirect_is_not_followed(self):
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(307,headers={'Location':'http://127.0.0.1/private'})
        with self.assertRaises(MailUnavailable):await self.mail(handler).asend('alice@example.org',LINK,60)
        self.assertEqual(len(calls),1)

    async def test_timeout_drops_original_exception_and_request(self):
        def handler(request):raise httpx.ReadTimeout('sensitive '+ENV['MIO_RESEND_API_KEY']+' '+LINK,request=request)
        try:await self.mail(handler).asend('alice@example.org',LINK,60)
        except MailUnavailable as error:
            self.assertEqual(error.error_class,'ReadTimeout');self.assertIsNone(error.__context__)
            trace=''.join(traceback.format_exception(error))
            self.assertNotIn(ENV['MIO_RESEND_API_KEY'],trace);self.assertNotIn(LINK,trace)
        else:self.fail('timeout should fail')

    async def test_total_deadline_cancels_and_closes_transport(self):
        finished=asyncio.Event()
        async def handler(request):
            try:await asyncio.sleep(30)
            finally:finished.set()
        mail=self.mail(handler);mail.TIMEOUT_SECONDS=.03
        with self.assertRaises(MailUnavailable) as error:await mail.asend('alice@example.org',LINK,60)
        self.assertEqual(error.exception.error_class,'TimeoutError');self.assertTrue(finished.is_set())

    async def test_tls_verify_and_timeout_options(self):
        actual=httpx.AsyncClient
        with patch('public_beta_mail.httpx.AsyncClient',wraps=actual) as factory:
            await self.mail(lambda r:httpx.Response(200)).asend('alice@example.org',LINK,60)
            options=factory.call_args.kwargs
            self.assertIs(options['verify'],True);self.assertIs(options['trust_env'],False)
            self.assertIs(options['follow_redirects'],False);self.assertIs(options['http2'],False)
            self.assertEqual(options['timeout'],10)

    async def test_tls_certificate_error_is_safe(self):
        def handler(request):raise httpx.ConnectError('certificate verification failed',request=request)
        with self.assertRaises(MailUnavailable) as error:await self.mail(handler).asend('alice@example.org',LINK,60)
        self.assertEqual(error.exception.error_class,'ConnectError')

    async def test_default_transport_verifies_tls_without_proxy_or_retry(self):
        transport=httpx.MockTransport(lambda r:httpx.Response(202))
        with patch('public_beta_mail.httpx.AsyncHTTPTransport',return_value=transport) as factory:
            await ResendMailer(ENV,sending_domain=DOMAIN).asend('alice@example.org',LINK,60)
        self.assertEqual(factory.call_args.kwargs,{'verify':True,'trust_env':False,'retries':0,'http2':False})

    async def test_debug_logs_stdout_stderr_do_not_contain_secret_or_body(self):
        logs=StringIO();console=StringIO();handler=logging.StreamHandler(logs)
        loggers=[logging.getLogger(name) for name in ('httpx','httpcore')]
        levels=[logger.level for logger in loggers]
        for logger in loggers:logger.addHandler(handler);logger.setLevel(logging.DEBUG)
        try:
            with redirect_stdout(console),redirect_stderr(console):
                await self.mail(lambda r:httpx.Response(200,text='private-body')).asend('alice@example.org',LINK,60)
                await self.safe_status(400)
            output=logs.getvalue()+console.getvalue()
            for value in (ENV['MIO_RESEND_API_KEY'],LINK,'Authorization','private-body','private-response','欢迎使用 Mio Canvas'):
                self.assertNotIn(value,output)
        finally:
            for logger,level in zip(loggers,levels):logger.removeHandler(handler);logger.setLevel(level)

    async def test_sync_wrapper_refuses_to_block_event_loop(self):
        with self.assertRaises(MailUnavailable) as error:self.mail(lambda r:httpx.Response(200)).send('alice@example.org',LINK,60)
        self.assertEqual(error.exception.error_class,'AsyncContextRequired')


class ResendGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary=tempfile.TemporaryDirectory();root=Path(self.temporary.name)
        self.cfg=BetaConfig(root/'g',root/'i',mail_mode='resend',min_free_disk=0)

    async def asyncTearDown(self):self.temporary.cleanup()

    def app(self,handler,env=None):
        mail=ResendMailer(ENV if env is None else env,sending_domain=DOMAIN,transport=httpx.MockTransport(handler))
        return create_app(self.cfg,mailer=mail)

    async def register(self,client):
        return await client.post('/api/beta/register',json={'email':'alice@example.org','username':'alice',
            'password':'synthetic-password-2026','confirmation':'synthetic-password-2026'})

    async def test_missing_key_does_not_create_pending(self):
        app=self.app(lambda r:httpx.Response(200),{**ENV,'MIO_RESEND_API_KEY':''})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin}) as client:
            result=await self.register(client)
        self.assertEqual(result.status_code,503);self.assertEqual(result.json(),{'detail':MAIL_UNAVAILABLE})
        self.assertEqual(app.state.store.capacity()['total_users'],0)

    async def test_error_body_not_exposed_pending_and_token_policy_unchanged(self):
        app=self.app(lambda r:httpx.Response(401,text=ENV['MIO_RESEND_API_KEY']+' sensitive upstream body'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin}) as client:
            result=await self.register(client)
        self.assertEqual(result.status_code,503);self.assertEqual(result.json(),{'detail':MAIL_UNAVAILABLE})
        with app.state.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM users').fetchone()[0],'pending_verification')
            self.assertIsNotNone(db.execute('SELECT invalidated_at FROM account_tokens').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM instances').fetchone()[0],0)
            self.assertNotIn(ENV['MIO_RESEND_API_KEY'],str(list(db.execute('SELECT * FROM security_events'))))

    async def test_slow_mail_does_not_block_gateway_event_loop(self):
        started=threading.Event();release=threading.Event()
        async def handler(request):
            started.set()
            while not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(202)
        app=self.app(handler)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=self.cfg.origin,headers={'Origin':self.cfg.origin}) as client:
            registration=asyncio.create_task(self.register(client))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,3))
                response=await asyncio.wait_for(client.get('/healthz'),timeout=1)
                self.assertEqual(response.status_code,200);self.assertFalse(registration.done())
            finally:release.set()
            response=await registration
            self.assertEqual(response.json()['status'],'pending_verification')
            self.assertNotIn('set-cookie',response.headers)


if __name__=='__main__':unittest.main()
