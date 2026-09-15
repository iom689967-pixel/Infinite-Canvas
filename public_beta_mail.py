"""Bounded mail transports. No secret, body, recipient or token is logged/persisted."""
import asyncio
from collections import deque
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import HeaderParser
import os
import re
import smtplib
import ssl
from urllib.parse import urlsplit

import httpx

from public_beta_email_address import normalize_email

MAIL_UNAVAILABLE = '邮件服务暂不可用，请稍后重试。'


class MailUnavailable(Exception):
    def __init__(self, *_legacy_details, provider=None, status_code=None, error_class=None):
        # Legacy callers may supply an unsafe message. Accept but never retain it.
        super().__init__()
        self.provider, self.status_code, self.error_class = provider, status_code, error_class


def parse_sender(value, sending_domain=None):
    """One RFC mailbox, with optional display name and exact configured domain."""
    try:
        if not isinstance(value, str) or not value.strip() or len(value)>512:
            raise ValueError()
        if any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError()
        header=HeaderParser(policy=policy.default).parsestr('From: '+value.strip())['From']
        if header.defects or len(header.addresses)!=1 or any(g.display_name is not None for g in header.groups):
            raise ValueError()
        address=header.addresses[0]
        _,envelope=normalize_email(address.addr_spec)
        if sending_domain is not None:
            _,expected=normalize_email('sender@'+sending_domain)
            if envelope.rsplit('@',1)[1]!=expected.rsplit('@',1)[1]:
                raise ValueError()
        return str(Address(display_name=address.display_name,addr_spec=envelope)),envelope
    except Exception:
        pass
    raise MailUnavailable(error_class='InvalidSender')


def verification_content(code, minutes):
    if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):
        raise MailUnavailable(error_class='InvalidVerificationCode')
    return {
        'subject':'你的 Mio Canvas 验证码',
        'text':f'Mio Canvas\n\n你的邮箱验证码是：\n{code}\n\n验证码将在 {minutes} 分钟后失效。\n如果不是你本人操作，请忽略这封邮件。',
        'html':f'<h1>Mio Canvas</h1><p>你的邮箱验证码是：</p><p style="font:700 32px monospace;letter-spacing:8px">{code}</p><p>验证码将在 {minutes} 分钟后失效。</p><p>如果不是你本人操作，请忽略这封邮件。</p>',
    }


class UnavailableMailer:
    def ready(self):
        raise MailUnavailable()

    def send(self, recipient, code, minutes):
        self.ready()


class MockMailer:
    """Explicit local test dependency. Memory-only, bounded, no public inbox endpoint."""
    def __init__(self):
        self.messages = deque(maxlen=200)

    def ready(self):
        pass

    def send(self, recipient, code, minutes):
        verification_content(code,minutes)
        self.messages.append({'recipient': recipient, 'code': code, 'minutes': minutes})


class SMTPMailer:
    def __init__(self, env, *, sending_domain=None):
        self.host = env.get('MIO_SMTP_HOST', '')
        self.port = env.get('MIO_SMTP_PORT', '587')
        self.username = env.get('MIO_SMTP_USERNAME', '')
        self._password = env.get('MIO_SMTP_PASSWORD', '')
        self.sender = env.get('MIO_MAIL_FROM') or env.get('MIO_SMTP_FROM', '')
        self.sending_domain = env.get('MIO_MAIL_SENDING_DOMAIN') or sending_domain
        self.tls = env.get('MIO_SMTP_TLS', 'starttls')
        self.timeout = env.get('MIO_SMTP_TIMEOUT_SECONDS', '10')

    def ready(self):
        try:
            if not self.host or any(c.isspace() for c in self.host) or not self.username or not self._password:
                raise ValueError()
            if self.tls not in {'starttls', 'ssl'} or not 1 <= int(self.port) <= 65535 or not 1 <= float(self.timeout) <= 30:
                raise ValueError()
            parse_sender(self.sender,self.sending_domain)
        except (ValueError, TypeError):
            raise MailUnavailable() from None

    def send(self, recipient, code, minutes):
        self.ready()
        sender,envelope=parse_sender(self.sender,self.sending_domain)
        content=verification_content(code,minutes)
        message = EmailMessage()
        message['Subject'] = content['subject']
        message['From'] = sender
        message['To'] = recipient
        message.set_content(content['text'])
        message.add_alternative(content['html'], subtype='html')
        try:
            context = ssl.create_default_context()
            cls = smtplib.SMTP_SSL if self.tls == 'ssl' else smtplib.SMTP
            options = {'context': context} if self.tls == 'ssl' else {}
            with cls(self.host, int(self.port), timeout=float(self.timeout), **options) as client:
                # Never set_debuglevel: SMTP traces include credentials and mail content.
                if self.tls == 'starttls':
                    client.ehlo(); client.starttls(context=context); client.ehlo()
                client.login(self.username, self._password)
                # IDNA domains use an ASCII envelope without requiring SMTPUTF8.
                client.send_message(message, from_addr=envelope,
                                    to_addrs=[normalize_email(recipient)[1]])
        except Exception:
            raise MailUnavailable() from None


class ResendMailer:
    ENDPOINT = 'https://api.resend.com/emails'
    TIMEOUT_SECONDS = 10

    def __init__(self, env, *, sending_domain, transport=None):
        # No SMTP or Provider fallback. Test transport is code injection, never env input.
        self._api_key=env.get('MIO_RESEND_API_KEY','')
        self.sender=env.get('MIO_MAIL_FROM','')
        self.sending_domain=env.get('MIO_MAIL_SENDING_DOMAIN') or sending_domain
        self._transport=transport

    def ready(self):
        if (not isinstance(self._api_key,str) or not 1<=len(self._api_key)<=4096
                or any(not 33<=ord(c)<=126 for c in self._api_key)):
            raise MailUnavailable(provider='resend',error_class='InvalidConfiguration')
        if not self.sending_domain:
            raise MailUnavailable(provider='resend',error_class='InvalidSender')
        parse_sender(self.sender,self.sending_domain)

    async def _request(self, payload):
        transport=self._transport if self._transport is not None else httpx.AsyncHTTPTransport(
            verify=True,trust_env=False,retries=0,http2=False)
        async with httpx.AsyncClient(transport=transport,verify=True,trust_env=False,
                timeout=self.TIMEOUT_SECONDS,follow_redirects=False,http2=False) as client:
            async with client.stream('POST',self.ENDPOINT,
                    headers={'Authorization':'Bearer '+self._api_key,'Content-Type':'application/json'},
                    json=payload) as response:
                # The response body and upstream message id are unnecessary. Never read/log them.
                return response.status_code

    async def asend(self, recipient, code, minutes):
        self.ready()
        sender,_=parse_sender(self.sender,self.sending_domain)
        error_class=None;status=None
        try:
            _,recipient=normalize_email(recipient)
            payload={'from':sender,'to':[recipient],**verification_content(code,minutes)}
            status=await asyncio.wait_for(self._request(payload),timeout=self.TIMEOUT_SECONDS)
        except Exception as exc:
            error_class=type(exc).__name__
        # Raise outside the handler: no raw HTTP exception/request/body in the exception chain.
        if error_class:
            raise MailUnavailable(provider='resend',error_class=error_class)
        if not 200<=status<300:
            raise MailUnavailable(provider='resend',status_code=status,error_class='HTTPStatusError')

    def send(self, recipient, code, minutes):
        # Existing Gateway register/resend operations run in asyncio.to_thread; CLI is synchronous.
        # Keep their pending/token transaction logic unchanged while using async HTTPS internally.
        try: asyncio.get_running_loop()
        except RuntimeError: pass
        else: raise MailUnavailable(provider='resend',error_class='AsyncContextRequired')
        asyncio.run(self.asend(recipient,code,minutes))


def mailer_for(config):
    domain=os.environ.get('MIO_MAIL_SENDING_DOMAIN') or urlsplit(config.origin).hostname
    if config.mail_mode == 'resend':
        return ResendMailer(os.environ,sending_domain=domain)
    if config.mail_mode == 'smtp':
        return SMTPMailer(os.environ,sending_domain=domain if os.environ.get('MIO_MAIL_FROM') else None)
    if config.mail_mode == 'mock' and not config.proxied:
        return MockMailer()
    return UnavailableMailer()
