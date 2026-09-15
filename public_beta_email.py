"""Registration codes, with a bounded read-only transition for already issued links."""
import re
import sqlite3
import time

from public_beta_email_address import normalize_email, masked_email
from public_beta_mail import MailUnavailable, MAIL_UNAVAILABLE
from public_beta_store import BetaError
from public_beta_email_codes import HOURLY_WINDOW_SECONDS

RESEND_MESSAGE = '请返回注册页继续邮箱验证码验证。'
RESULT_MESSAGES = {
    'verified': '邮箱验证成功，请登录进入 Mio Canvas。',
    'used': '链接已使用，请登录。',
    'expired': '链接已失效，请返回注册页获取验证码。',
    'invalid': '验证失败，链接无效。',
    'full': '邮箱验证成功，但当前 Beta 名额已满。',
    'waiting': '邮箱已验证，工作区初始化待管理员处理。',
}


class EmailRegistration:
    def __init__(self, store, supervisor, mailer):
        self.store, self.config, self.supervisor, self.mailer = store, store.config, supervisor, mailer
        from public_beta_email_codes import EmailCodes
        self.codes=EmailCodes(self)

    def address(self, value):
        try:
            return normalize_email(value, testing=self.config.mail_mode=='mock' and not self.config.proxied)
        except ValueError:
            raise BetaError('请输入有效邮箱') from None

    def ready(self):
        try: self.mailer.ready()
        except MailUnavailable: raise BetaError(MAIL_UNAVAILABLE,503) from None

    def cleanup(self, db, now):
        # Only unverified pending identities without any instance can expire.
        rows=db.execute("""SELECT id FROM users WHERE status='pending_verification'
            AND email_verified_at IS NULL AND pending_expires_at<=?
            AND NOT EXISTS(SELECT 1 FROM instances WHERE user_id=users.id)""",(now,)).fetchall()
        for row in rows:
            db.execute('DELETE FROM account_tokens WHERE user_id=?',(row['id'],))
            db.execute('DELETE FROM email_code_pending WHERE user_id=?',(row['id'],))
            db.execute('DELETE FROM users WHERE id=?',(row['id'],))
        db.execute('DELETE FROM account_tokens WHERE expires_at<?',(now-self.config.pending_ttl,))
        db.execute('DELETE FROM email_code_limits WHERE started_at<=?',(now-HOURLY_WINDOW_SECONDS,))
        return len(rows)

    def register(self, email, username, password, confirmation, peer):
        return self.codes.register(email,username,password,confirmation,peer)

    def send(self, uid):
        with self.store.db() as db:
            row=db.execute('SELECT pending_digest FROM email_code_pending WHERE user_id=? AND completed_at IS NULL',(uid,)).fetchone()
        if not row: return False  # No new legacy verification links, including operator sends.
        self.codes.send(row['pending_digest'],'local-operator')
        return True

    def resend(self, email, peer):
        self.store.limit('resend-ip',peer,self.config.register_limit)
        # Compatibility endpoint cannot authorize code sends by email alone.
        # Legacy pending users can re-confirm their registration password to switch.
        return {'detail':RESEND_MESSAGE}

    def provision(self, uid):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if row and row['status']=='active': return 'verified'
        if not row or row['status']!='verified_waiting' or row['email_verified_at'] is None: return 'waiting'
        if self.config.registration_mode=='closed': return 'waiting'
        try:
            # All values come from the registry. Use the existing fixed IPC operation.
            if hasattr(self.supervisor,'call'):
                self.supervisor.call('provision',username=row['username'],password_hash=row['password_hash'])
            else:
                self.supervisor.provision(row['username'],row['password_hash'])
        except BetaError:
            with self.store.db() as db:
                if self.store.capacity(db)['active_seats']>=self.config.max_users: return 'full'
            return 'waiting'
        return 'verified'

    def verify(self, raw, peer):
        self.store.limit('verify-ip',peer,self.config.login_limit)
        if not isinstance(raw,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',raw):
            return self.result('invalid')
        now=time.time();digest=self.store.digest(raw);uid=None
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            token=db.execute("SELECT * FROM account_tokens WHERE digest=? AND purpose='verify_email'",(digest,)).fetchone()
            if not token: state='invalid'
            elif now>=db.execute('SELECT legacy_until FROM email_link_transition').fetchone()[0]: state='expired'
            elif db.execute('SELECT 1 FROM email_code_pending WHERE user_id=?',(token['user_id'],)).fetchone(): state='invalid'
            elif token['used_at'] is not None: state='used'
            elif token['invalidated_at'] is not None or token['expires_at']<=now: state='expired'
            else:
                user=db.execute('SELECT * FROM users WHERE id=?',(token['user_id'],)).fetchone()
                if not user or user['status'] not in {'pending_verification','active'}: state='invalid'
                elif user['pending_expires_at'] is not None and user['pending_expires_at']<=now: state='expired'
                else:
                    uid=user['id'];state='verified'
                    db.execute('UPDATE account_tokens SET used_at=? WHERE digest=?',(now,digest))
                    db.execute("UPDATE account_tokens SET invalidated_at=? WHERE user_id=? AND purpose='verify_email' AND digest!=? AND used_at IS NULL",(now,uid,digest))
                    db.execute("""UPDATE users SET email_verified_at=?,email_status='verified',pending_expires_at=NULL,
                        status=CASE WHEN status='pending_verification' THEN 'verified_waiting' ELSE status END WHERE id=?""",(now,uid))
        if state=='expired': self.store.event('email_verification_expired')
        if uid:
            self.store.event('email_verification_success',uid)
            state=self.provision(uid)
        return self.result(state)

    @staticmethod
    def result(state): return {'status':state,'detail':RESULT_MESSAGES[state]}

    def set_email(self, uid, value):
        display,normalized=self.address(value)
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT status FROM users WHERE id=?',(uid,)).fetchone()
                if not row or row['status'] not in {'active','disabled'}:
                    raise BetaError('只能为已有正式账号设置邮箱',409)
                db.execute("UPDATE users SET email=?,email_normalized=?,email_verified_at=NULL,email_status='pending' WHERE id=?",(display,normalized,uid))
                db.execute('DELETE FROM account_tokens WHERE user_id=?',(uid,))
        except sqlite3.IntegrityError: raise BetaError('邮箱已被使用',409) from None
        self.store.event('email_bound',uid)
        return {'masked_email':masked_email(display),'email_status':'pending'}

    def admin_verify(self, uid):
        now=time.time()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT email,status FROM users WHERE id=?',(uid,)).fetchone()
            if not row or not row['email']: raise BetaError('请先设置邮箱')
            if row['status']=='provisioning': raise BetaError('账号正在初始化',409)
            db.execute("UPDATE users SET email_verified_at=?,email_status='verified',pending_expires_at=NULL,status=CASE WHEN status='pending_verification' THEN 'verified_waiting' ELSE status END WHERE id=?",(now,uid))
            db.execute('UPDATE account_tokens SET invalidated_at=? WHERE user_id=? AND used_at IS NULL',(now,uid))
        self.store.event('email_admin_verified',uid)
        return self.result(self.provision(uid))
