"""Verified registration orchestration; no worker lifecycle or session replacement."""
import math
import re
import secrets
import sqlite3
import time
import uuid

from instance_auth import PASSWORD_MAX_LENGTH, password_hash
from public_beta_email_address import normalize_email, masked_email
from public_beta_mail import MailUnavailable, MAIL_UNAVAILABLE
from public_beta_store import BetaError, REGISTRATION_PASSWORD_MIN_LENGTH

RESEND_MESSAGE = '如果该邮箱存在待验证账户，我们已经发送验证邮件。'
RESULT_MESSAGES = {
    'verified': '邮箱验证成功，请登录进入 Mio Canvas。',
    'used': '链接已使用，请登录或重新发送验证邮件。',
    'expired': '链接已失效，请重新发送验证邮件。',
    'invalid': '验证失败，链接无效。',
    'full': '邮箱验证成功，但当前 Beta 名额已满。',
    'waiting': '邮箱已验证，工作区初始化待管理员处理。',
}


class EmailRegistration:
    def __init__(self, store, supervisor, mailer):
        self.store, self.config, self.supervisor, self.mailer = store, store.config, supervisor, mailer

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
            db.execute('DELETE FROM users WHERE id=?',(row['id'],))
        db.execute('DELETE FROM account_tokens WHERE expires_at<?',(now-self.config.pending_ttl,))
        return len(rows)

    def register(self, email, username, password, confirmation, peer):
        self.store.limit('registration-ip',peer,self.config.register_limit)
        name=self.store.username(username)
        self.store.limit('registration-name',name,3)
        display,normalized=self.address(email)
        if not isinstance(password,str) or not REGISTRATION_PASSWORD_MIN_LENGTH<=len(password)<=PASSWORD_MAX_LENGTH:
            raise BetaError(f'密码长度必须为 {REGISTRATION_PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 个字符')
        if password!=confirmation: raise BetaError('两次密码输入不一致')
        self.ready()
        # Preserve the existing disk safety valve even though no instance is created yet.
        import shutil
        if shutil.disk_usage(self.config.instances_root).free < self.config.min_free_disk:
            raise BetaError('服务器存储资源不足，暂时停止新注册',503)
        encoded=password_hash(password);now=time.time();uid=uuid.uuid4().hex
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');self.cleanup(db,now)
            if db.execute('SELECT 1 FROM users WHERE username=? OR email_normalized=?',(name,normalized)).fetchone():
                raise BetaError('邮箱或用户名已被使用，请登录或重新发送验证邮件。',409)
            count=db.execute("SELECT COUNT(*) FROM users WHERE status IN ('pending_verification','verified_waiting')").fetchone()[0]
            if count>=self.config.max_pending_registrations:
                raise BetaError('待验证注册数量已达上限，请稍后重试。',429)
            db.execute('''INSERT INTO users(id,username,password_hash,status,created_at,email,email_normalized,
                email_status,legacy_username_login,pending_expires_at) VALUES (?,?,?,'pending_verification',?,?,?,'pending',0,?)''',
                (uid,name,encoded,now,display,normalized,now+self.config.pending_ttl))
        self.send(uid)
        return {'status':'pending_verification','detail':'验证邮件已发送，请检查邮箱。','masked_email':masked_email(display)}

    def send(self, uid):
        self.ready();now=time.time();raw=secrets.token_urlsafe(32);digest=self.store.digest(raw)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            user=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if (not user or not user['email'] or user['email_verified_at'] is not None
                    or user['status'] not in {'pending_verification','active'}): return False
            if user['pending_expires_at'] is not None and user['pending_expires_at']<=now: return False
            latest=db.execute("SELECT MAX(created_at) FROM account_tokens WHERE user_id=? AND purpose='verify_email'",(uid,)).fetchone()[0]
            if latest is not None and now-latest<self.config.resend_cooldown: return False
            db.execute("UPDATE account_tokens SET invalidated_at=? WHERE user_id=? AND purpose='verify_email' AND used_at IS NULL AND invalidated_at IS NULL",(now,uid))
            expires=min(now+self.config.verification_ttl,user['pending_expires_at'] or float('inf'))
            db.execute('INSERT INTO account_tokens VALUES (?,?,?, ?,?,NULL,NULL)',(digest,uid,'verify_email',now,expires))
            recipient=user['email']
        # Fragment never reaches reverse-proxy/access logs. The page removes it before POST.
        link=self.config.origin+'/verify-email#token='+raw
        try: self.mailer.send(recipient,link,math.ceil(self.config.verification_ttl/60))
        except MailUnavailable:
            with self.store.db() as db:
                db.execute('UPDATE account_tokens SET invalidated_at=? WHERE digest=?',(time.time(),digest))
            self.store.event('email_delivery_failed',uid)
            raise BetaError(MAIL_UNAVAILABLE,503) from None
        self.store.event('email_verification_sent',uid)
        return True

    def resend(self, email, peer):
        self.store.limit('resend-ip',peer,self.config.register_limit)
        self.ready()
        try: _,normalized=self.address(email)
        except BetaError: normalized='invalid'
        try: self.store.limit('resend-email',normalized,self.config.resend_limit)
        except BetaError: return {'detail':RESEND_MESSAGE}
        with self.store.db() as db:
            row=db.execute('SELECT id FROM users WHERE email_normalized=?',(normalized,)).fetchone()
        if row:
            try: self.send(row['id'])
            except BetaError:
                # A mailbox-specific SMTP rejection must not become an existence oracle.
                pass
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
