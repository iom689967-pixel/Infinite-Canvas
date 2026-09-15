"""Browser-bound, keyed email codes. Only transient mail transport holds plaintext."""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import secrets
import shutil
import sqlite3
import time
import uuid

from instance_auth import PASSWORD_MAX_LENGTH, password_hash, verify_password
from public_beta_email_address import masked_email
from public_beta_mail import MAIL_UNAVAILABLE
from public_beta_store import BetaError, REGISTRATION_PASSWORD_MIN_LENGTH

PENDING_COOKIE='mio_email_pending'
INVALID_PENDING='验证申请已失效，请重新注册。'
EXPIRED='验证码已过期，请重新获取'
INVALIDATED='验证码已失效，请重新获取'
SEND_LIMIT='验证码发送过于频繁，请稍后再试。'
HOURLY_WINDOW_SECONDS=3600
MAX_RATE_BUCKETS=4096
MAX_CODE_HISTORY=1024


@dataclass(repr=False)
class PendingRegistration:
    data: dict
    cookie: str = field(repr=False)


class EmailCodes:
    def __init__(self, registration):
        self.registration=registration
        self.store,self.config=registration.store,registration.config
        # The master handoff.key never reaches a worker; workers get separately
        # derived instance keys. Domain separation prevents cross-protocol reuse.
        self.key=hmac.new(self.store.key,b'mio-email-code-v1',hashlib.sha256).digest()

    def digest(self, purpose, value):
        return hmac.new(self.key,purpose+b'\0'+value.encode(),hashlib.sha256).hexdigest()

    def code_hmac(self, row, code, generation=None):
        value=json.dumps([row['pending_digest'],row['email_normalized'],
                          generation if generation is not None else row['generation'],code],separators=(',',':'))
        return self.digest(b'verification',value)

    def identity(self, pending_id, cookie):
        parts=cookie.split('.') if isinstance(cookie,str) else []
        if (not isinstance(pending_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',pending_id)
                or len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_-]{43}',p) for p in parts)
                or not hmac.compare_digest(pending_id,parts[0])):
            raise BetaError(INVALID_PENDING,404)
        return self.digest(b'pending',pending_id),self.digest(b'owner',parts[1])

    def pending(self, db, pending_id, cookie, now):
        digest,owner=self.identity(pending_id,cookie)
        row=db.execute('''SELECT p.*,u.email,u.email_normalized,u.username,u.status,u.email_verified_at,
            u.pending_expires_at FROM email_code_pending p JOIN users u ON p.user_id=u.id
            WHERE p.pending_digest=?''',(digest,)).fetchone()
        if (not row or not hmac.compare_digest(owner,row['owner_digest']) or row['completed_at'] is not None
                or row['status']!='pending_verification' or row['pending_expires_at']<=now):
            raise BetaError(INVALID_PENDING,404)
        return row

    def public_state(self, row, pending_id, now):
        return {'verification_required':True,'pending_id':pending_id,'masked_email':masked_email(row['email']),
                'resend_after_seconds':max(0,math.ceil((row['resend_available_at'] or now)-now)),
                'expires_in_seconds':max(0,math.ceil((row['expires_at'] or now)-now)),
                'status':'pending_verification'}

    def status(self, cookie):
        pending_id=cookie.split('.')[0] if isinstance(cookie,str) else ''
        now=time.time()
        with self.store.db() as db:
            return self.public_state(self.pending(db,pending_id,cookie,now),pending_id,now)

    def rate_limit(self, db, row, peer, now):
        db.execute('DELETE FROM email_code_limits WHERE started_at<=?',(now-HOURLY_WINDOW_SECONDS,))
        buckets=[(self.digest(b'rate-email',row['email_normalized']),self.config.email_code_hourly_limit),
                 (self.digest(b'rate-pending',row['pending_digest']),self.config.email_code_hourly_limit),
                 (self.digest(b'rate-ip',str(peer)),self.config.email_code_ip_hourly_limit)]
        for bucket,maximum in buckets:
            count=db.execute('SELECT count FROM email_code_limits WHERE bucket=?',(bucket,)).fetchone()
            if count and count[0]>=maximum: raise BetaError(SEND_LIMIT,429)
        if db.execute('SELECT COUNT(*) FROM email_code_limits').fetchone()[0]+len(buckets)>MAX_RATE_BUCKETS:
            raise BetaError(SEND_LIMIT,429)
        for bucket,_ in buckets:
            db.execute('INSERT INTO email_code_limits VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,now))

    def register(self, email, username, password, confirmation, peer):
        self.store.limit('registration-ip',peer,self.config.register_limit)
        name=self.store.username(username);self.store.limit('registration-name',name,3)
        display,normalized=self.registration.address(email)
        if not isinstance(password,str) or not REGISTRATION_PASSWORD_MIN_LENGTH<=len(password)<=PASSWORD_MAX_LENGTH:
            raise BetaError(f'密码长度必须为 {REGISTRATION_PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 个字符')
        if password!=confirmation: raise BetaError('两次密码输入不一致')
        self.registration.ready()
        if shutil.disk_usage(self.config.instances_root).free<self.config.min_free_disk:
            raise BetaError('服务器存储资源不足，暂时停止新注册',503)
        encoded=password_hash(password);now=time.time();uid=uuid.uuid4().hex
        pending_id,owner=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        digest=self.digest(b'pending',pending_id)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');self.registration.cleanup(db,now)
            existing=db.execute('SELECT * FROM users WHERE username=? OR email_normalized=?',(name,normalized)).fetchall()
            if existing:
                row=existing[0]
                # A pre-migration pending may switch to codes only after the same
                # registration password and both unique identities are confirmed.
                challenge=db.execute('SELECT * FROM email_code_pending WHERE user_id=?',(row['id'],)).fetchone()
                recoverable=(not challenge or challenge['delivery_state'] in {'failed','idle'}
                             or (challenge['delivery_state']=='sending' and (challenge['sending_until'] or 0)<=now))
                can_migrate=(len(existing)==1 and row['status']=='pending_verification'
                    and row['username']==name and row['email_normalized']==normalized
                    and row['email_verified_at'] is None
                    and recoverable
                    and verify_password(password,row['password_hash']))
                if not can_migrate: raise BetaError('邮箱或用户名已被使用，请登录或重新发送验证码。',409)
                uid=row['id']
                db.execute('UPDATE account_tokens SET invalidated_at=? WHERE user_id=? AND used_at IS NULL',(now,uid))
                db.execute('DELETE FROM email_code_pending WHERE user_id=?',(uid,))
            else:
                count=db.execute("SELECT COUNT(*) FROM users WHERE status IN ('pending_verification','verified_waiting')").fetchone()[0]
                if count>=self.config.max_pending_registrations: raise BetaError('待验证注册数量已达上限，请稍后重试。',429)
                db.execute('''INSERT INTO users(id,username,password_hash,status,created_at,email,email_normalized,
                    email_status,legacy_username_login,pending_expires_at) VALUES (?,?,?,'pending_verification',?,?,?,'pending',0,?)''',
                    (uid,name,encoded,now,display,normalized,now+self.config.pending_ttl))
            db.execute('INSERT INTO email_code_pending(pending_digest,user_id,owner_digest,created_at) VALUES (?,?,?,?)',
                       (digest,uid,self.digest(b'owner',owner),now))
        cookie=pending_id+'.'+owner
        self.send(digest,peer)
        return PendingRegistration(self.status(cookie),cookie)

    def send(self, digest, peer):
        self.registration.ready();now=time.time()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT p.*,u.email,u.email_normalized,u.status,u.pending_expires_at
                FROM email_code_pending p JOIN users u ON p.user_id=u.id WHERE p.pending_digest=?''',(digest,)).fetchone()
            if not row or row['status']!='pending_verification' or row['completed_at'] is not None or row['pending_expires_at']<=now:
                raise BetaError(INVALID_PENDING,404)
            if ((row['resend_available_at'] or 0)>now or (row['delivery_state']=='sending' and (row['sending_until'] or 0)>now)):
                raise BetaError('请稍后再重新发送',429)
            self.rate_limit(db,row,peer,now)
            # A code from any old mail must never equal a later generation's
            # digits. Retain only keyed hashes with their server-owned generation.
            history=[];invalid_history=False
            if not isinstance(row['code_history'],str) or len(row['code_history'])>MAX_CODE_HISTORY*96:
                raise BetaError(MAIL_UNAVAILABLE,503)
            try: history=json.loads(row['code_history'])
            except (TypeError,ValueError): invalid_history=True
            if (invalid_history or not isinstance(history,list) or len(history)>=MAX_CODE_HISTORY
                    or any(not isinstance(entry,list) or len(entry)!=2 or type(entry[0]) is not int
                           or entry[0]<1 or not isinstance(entry[1],str)
                           or not re.fullmatch(r'[0-9a-f]{64}',entry[1]) for entry in history)):
                raise BetaError(MAIL_UNAVAILABLE,503)
            for _ in range(16):
                code=f'{secrets.randbelow(1_000_000):06d}'
                if not any(hmac.compare_digest(self.code_hmac(row,code,old_generation),old_digest)
                           for old_generation,old_digest in history): break
            else: raise BetaError(MAIL_UNAVAILABLE,503)
            generation=row['generation']+1
            digest_value=self.code_hmac(row,code,generation)
            history.append([generation,digest_value])
            db.execute('''UPDATE email_code_pending SET generation=?,code_hmac=NULL,code_history=?,failed_attempts=0,
                delivery_state='sending',sending_until=?,resend_available_at=?,expires_at=NULL WHERE pending_digest=?''',
                (generation,json.dumps(history,separators=(',',':')),now+30,now+self.config.email_code_resend_seconds,digest))
        failed=False
        try:
            self.registration.mailer.send(row['email'],code,math.ceil(self.config.email_code_ttl/60))
        except Exception:
            failed=True  # Never retain raw exception/message/request/body in the raised chain.
        code=None
        finished=time.time()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            eligible=db.execute('''SELECT 1 FROM users u JOIN email_code_pending p ON p.user_id=u.id
                WHERE p.pending_digest=? AND p.generation=? AND p.delivery_state='sending'
                AND u.status='pending_verification' AND u.pending_expires_at>?''',(digest,generation,finished)).fetchone()
            if not eligible: raise BetaError(INVALID_PENDING,404)
            db.execute('''UPDATE email_code_pending SET code_hmac=?,delivery_state=?,sending_until=NULL,
                expires_at=?,resend_available_at=? WHERE pending_digest=? AND generation=?''',
                (None if failed else digest_value,'failed' if failed else 'ready',
                 min(finished+self.config.email_code_ttl,row['pending_expires_at']),finished+self.config.email_code_resend_seconds,digest,generation))
        self.store.event('email_delivery_failed' if failed else 'email_verification_sent',row['user_id'])
        if failed: raise BetaError(MAIL_UNAVAILABLE,503)

    def resend(self, pending_id, cookie, peer):
        now=time.time()
        with self.store.db() as db: row=self.pending(db,pending_id,cookie,now)
        self.send(row['pending_digest'],peer)
        return self.status(cookie)

    def cancel(self, pending_id, cookie):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');row=self.pending(db,pending_id,cookie,time.time())
            if db.execute('SELECT 1 FROM instances WHERE user_id=?',(row['user_id'],)).fetchone(): raise BetaError(INVALID_PENDING,404)
            db.execute('DELETE FROM account_tokens WHERE user_id=?',(row['user_id'],))
            db.execute('DELETE FROM email_code_pending WHERE user_id=?',(row['user_id'],))
            db.execute('DELETE FROM users WHERE id=?',(row['user_id'],))
        return {'ok':True}

    def verify(self, pending_id, cookie, code, peer):
        self.store.limit('email-code-verify-ip',peer,self.config.login_limit)
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code): raise BetaError('请输入 6 位数字验证码')
        now=time.time();error=None;token=None
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');row=self.pending(db,pending_id,cookie,now)
            if row['delivery_state']!='ready' or not row['code_hmac'] or row['failed_attempts']>=self.config.email_code_max_attempts:
                error=INVALIDATED
            elif row['expires_at']<=now: error=EXPIRED
            elif not hmac.compare_digest(self.code_hmac(row,code),row['code_hmac']):
                attempts=row['failed_attempts']+1
                exhausted=attempts>=self.config.email_code_max_attempts
                db.execute('UPDATE email_code_pending SET failed_attempts=? WHERE pending_digest=?',
                           (attempts,row['pending_digest']))
                error=INVALIDATED if exhausted else '验证码错误，请重新输入'
            elif self.config.registration_mode=='closed': error='当前 Mio Canvas Public Beta 暂未开放注册。'
            elif self.store.capacity(db)['active_seats']>=self.config.max_users: error='当前 Mio Canvas Beta 名额已满。'
            else:
                if db.execute('SELECT COUNT(*) FROM users WHERE username=? OR email_normalized=?',(row['username'],row['email_normalized'])).fetchone()[0]!=1:
                    raise BetaError('邮箱或用户名已被使用',409)
                token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
                db.execute("UPDATE users SET status='active',email_verified_at=?,email_status='verified',pending_expires_at=NULL,last_login_at=? WHERE id=?",(now,now,row['user_id']))
                db.execute("UPDATE email_code_pending SET completed_at=?,code_hmac=NULL,code_history='[]',delivery_state='consumed' WHERE pending_digest=?",(now,row['pending_digest']))
                db.execute('UPDATE account_tokens SET invalidated_at=? WHERE user_id=? AND used_at IS NULL',(now,row['user_id']))
                db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(self.store.digest(token),row['user_id'],now+self.config.session_ttl,csrf))
                db.execute('INSERT INTO security_events(event,user_id,created_at) VALUES (?,?,?)',('email_verification_success',row['user_id'],now))
        # Raise after commit so incorrect attempts persist. No rollback on expected errors.
        if error: raise BetaError(error,409 if '名额' in error else 400)
        return token
