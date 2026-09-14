"""Bounded SMTP delivery. No secret, body, recipient or token is logged/persisted."""
from collections import deque
from email.message import EmailMessage
from html import escape
import os
import smtplib
import ssl

from public_beta_email_address import normalize_email

MAIL_UNAVAILABLE = '邮件服务暂不可用，请稍后重试。'


class MailUnavailable(Exception):
    pass


class UnavailableMailer:
    def ready(self):
        raise MailUnavailable()

    def send(self, recipient, link, minutes):
        self.ready()


class MockMailer:
    """Explicit local test dependency. Memory-only, bounded, no public inbox endpoint."""
    def __init__(self):
        self.messages = deque(maxlen=200)

    def ready(self):
        pass

    def send(self, recipient, link, minutes):
        self.messages.append({'recipient': recipient, 'link': link, 'minutes': minutes})


class SMTPMailer:
    def __init__(self, env):
        self.host = env.get('MIO_SMTP_HOST', '')
        self.port = env.get('MIO_SMTP_PORT', '587')
        self.username = env.get('MIO_SMTP_USERNAME', '')
        self._password = env.get('MIO_SMTP_PASSWORD', '')
        self.sender = env.get('MIO_SMTP_FROM', '')
        self.tls = env.get('MIO_SMTP_TLS', 'starttls')
        self.timeout = env.get('MIO_SMTP_TIMEOUT_SECONDS', '10')

    def ready(self):
        try:
            if not self.host or any(c.isspace() for c in self.host) or not self.username or not self._password:
                raise ValueError()
            if self.tls not in {'starttls', 'ssl'} or not 1 <= int(self.port) <= 65535 or not 1 <= float(self.timeout) <= 30:
                raise ValueError()
            normalize_email(self.sender)
        except (ValueError, TypeError):
            raise MailUnavailable() from None

    def send(self, recipient, link, minutes):
        self.ready()
        message = EmailMessage()
        message['Subject'] = '验证你的 Mio Canvas 邮箱'
        message['From'] = self.sender
        message['To'] = recipient
        message.set_content(f'欢迎使用 Mio Canvas。\n\n点击下面的链接完成邮箱验证：\n{link}\n\n验证链接将在 {minutes} 分钟后失效。\n如果不是你本人操作，可以忽略这封邮件。')
        message.add_alternative(f'<p>欢迎使用 Mio Canvas。</p><p><a href="{escape(link, quote=True)}">验证邮箱</a></p><p>验证链接将在 {minutes} 分钟后失效。</p><p>如果不是你本人操作，可以忽略这封邮件。</p>', subtype='html')
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
                client.send_message(message, from_addr=normalize_email(self.sender)[1],
                                    to_addrs=[normalize_email(recipient)[1]])
        except Exception:
            raise MailUnavailable() from None


def mailer_for(config):
    if config.mail_mode == 'smtp':
        return SMTPMailer(os.environ)
    if config.mail_mode == 'mock' and not config.proxied:
        return MockMailer()
    return UnavailableMailer()
