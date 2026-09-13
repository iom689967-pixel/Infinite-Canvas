"""Bounded loopback instance lifecycle. argv is fixed; users never choose paths/ports."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

import httpx
from instance_auth import AuthStore, PASSWORD_MAX_LENGTH, password_hash
from public_beta_handoff import instance_key, sign_ticket
from public_beta_store import BetaError, REGISTRATION_PASSWORD_MIN_LENGTH


class Supervisor:
    def __init__(self, store):
        self.store, self.config = store, store.config
        self.program = Path(__file__).resolve().parent
        self.children = {}

    @contextmanager
    def locked(self):
        with open(self.config.root/'supervisor.lock','a+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            yield

    def root(self, instance):
        root=self.config.instances_root/instance['instance_id']
        if str(root)!=instance['data_root'] or root.is_symlink() or root.parent!=self.config.instances_root:
            raise BetaError('工作区目录校验失败',503)
        return root

    def env(self, instance):
        return {'PATH':os.defpath,'LANG':'en_US.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
                'INSTANCE_ID':instance['instance_id'],'INSTANCE_DATA_ROOT':instance['data_root'],
                'INSTANCE_HOST':'127.0.0.1','INSTANCE_PORT':str(instance['assigned_port']),
                'INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK':'1','INSTANCE_MOCK_UPSTREAMS':self.config.mock_upstreams}

    @staticmethod
    def available(port):
        try:
            with socket.socket() as sock: sock.bind(('127.0.0.1',port))
            return True
        except OSError: return False

    def allocate(self):
        with self.store.db() as db: used={r[0] for r in db.execute('SELECT assigned_port FROM instances')}
        for port in range(self.config.port_start,self.config.port_end+1):
            if port not in used|{self.config.port,3000} and self.available(port): return port
        raise BetaError('暂时没有可用工作区资源，请稍后再试',503)

    def register(self, username, password, confirmation, peer):
        self.store.limit('registration-ip',peer,self.config.register_limit)
        name=self.store.username(username)
        self.store.limit('registration-name',name,3)
        if password!=confirmation: raise BetaError('两次密码输入不一致')
        if shutil.disk_usage(self.config.instances_root).free < self.config.min_free_disk:
            raise BetaError('服务器存储资源不足，暂时停止新注册',503)
        try:
            if not isinstance(password,str) or len(password)<REGISTRATION_PASSWORD_MIN_LENGTH:
                raise ValueError()
            encoded=password_hash(password)
        except (ValueError,TypeError):
            raise BetaError(f'密码长度必须为 {REGISTRATION_PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 个字符') from None
        with self.locked():
            instance=self.store.reserve(name,encoded,self.allocate())
            root=self.root(instance); created=False
            try:
                root.mkdir(mode=0o700);created=True
                with self.store.db() as db:
                    db.execute("UPDATE instances SET status='provisioning-owned' WHERE user_id=?",(instance['user_id'],))
                result=subprocess.run([sys.executable,str(self.program/'public_beta_worker.py'),'initialize'],
                    cwd=self.program,env=self.env(instance),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,timeout=30)
                if result.returncode: raise RuntimeError('initialization failed')
                account=AuthStore(root,instance['instance_id'])
                # No second user password. Direct instance password login is disabled in beta.
                account.create_account(name,secrets.token_urlsafe(48))
                account.set_provider_permission(name,True)
                files={
                    'gateway-handoff.json':{'user_id':instance['user_id'],'username':name,
                        'key':instance_key(self.store.key,instance['instance_id']).hex()},
                    'public-beta.json':{'storage_quota':self.config.storage_quota,'max_upload':self.config.max_upload,
                        'max_concurrent_generations':self.config.concurrency,
                        'min_free_disk_bytes':self.config.min_free_disk},
                    'model-access.json':{'schema_version':1,'mode':'mock' if self.config.mock_upstreams else 'live',
                        'max_concurrent':self.config.concurrency,'providers':[],'personal_providers':[]}}
                for filename,data in files.items():
                    path=root/'.auth'/filename
                    with open(path,'x') as f:
                        os.chmod(path,0o600);json.dump(data,f)
                self.store.activate(instance['user_id'])
            except Exception:
                # Only a root exclusively created by this registration can be removed.
                if created and root.is_dir() and not root.is_symlink(): shutil.rmtree(root)
                self.store.rollback(instance['user_id'])
                raise BetaError('工作区初始化失败，本次注册已撤销，请稍后再试',503) from None
        return self.store.session(instance['user_id'])

    def recover_incomplete(self):
        with self.locked():
            with self.store.db() as db:
                rows=[dict(r) for r in db.execute("SELECT i.* FROM instances i JOIN users u ON i.user_id=u.id WHERE u.status='provisioning'")]
            for instance in rows:
                root=self.root(instance)
                marker=root/'.instance.json'
                if instance['status']=='provisioning-owned' and marker.is_file() and not marker.is_symlink():
                    try: owned=json.loads(marker.read_text())=={'instance_id':instance['instance_id'],'data_root':str(root)}
                    except ValueError: owned=False
                    if owned and not root.is_symlink(): shutil.rmtree(root)
                elif root.is_dir() and not root.is_symlink():
                    try: root.rmdir()  # Only an empty directory, never unknown existing data.
                    except OSError: pass
                self.store.rollback(instance['user_id'])

    @staticmethod
    def process_start(pid):
        if not pid: return ''
        result=subprocess.run(['ps','-p',str(int(pid)),'-o','lstart='],capture_output=True,text=True,timeout=3)
        return result.stdout.strip() if result.returncode==0 else ''

    def health(self, instance):
        challenge=secrets.token_urlsafe(32)
        expected=hmac.new(instance_key(self.store.key,instance['instance_id']),('health:'+challenge).encode(),hashlib.sha256).hexdigest()
        try:
            with httpx.Client(trust_env=False,timeout=1) as c:
                response=c.get(f'http://127.0.0.1:{instance["assigned_port"]}/__mio/health',headers={'X-Mio-Challenge':challenge})
                return response.status_code==200 and hmac.compare_digest(response.json().get('proof',''),expected)
        except (httpx.HTTPError,ValueError,TypeError): return False

    def running_count(self):
        with self.store.db() as db:
            rows=[dict(row) for row in db.execute("SELECT user_id,pid,process_started,status FROM instances WHERE status IN ('starting','running')")]
        live=0
        for row in rows:
            if row['pid'] and self.process_start(row['pid'])==row['process_started']:
                live+=1
            else:
                with self.store.db() as db:
                    db.execute("UPDATE instances SET status='stopped',pid=NULL,process_started=NULL WHERE user_id=?",(row['user_id'],))
        return live

    def start(self, uid):
        with self.locked():
            instance=self.store.instance(uid)
            if instance['user_status']!='active': raise BetaError('账号当前不可用',401)
            root=self.root(instance)
            if self.health(instance):
                if instance['pid'] and self.process_start(instance['pid'])==instance['process_started']:
                    return instance
                raise BetaError('工作区进程状态待管理员确认',503)
            if instance['pid'] and self.process_start(instance['pid'])==instance['process_started']:
                raise BetaError('原工作区正在启动或暂时无响应，请稍后再试',503)
            if not self.available(instance['assigned_port']):
                # Keep the persisted mapping stable. Never connect to another process or guess.
                raise BetaError('工作区端口暂时不可用，请联系管理员',503)
            if self.running_count() >= self.config.max_running_instances:
                raise BetaError('当前正在运行的工作区已达服务器安全上限，请稍后再试',503)
            child=subprocess.Popen([sys.executable,str(self.program/'public_beta_worker.py'),'serve'],cwd=self.program,
                env=self.env(instance),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                start_new_session=True)
            self.children[uid]=child
            started=self.process_start(child.pid)
            with self.store.db() as db:
                db.execute("UPDATE instances SET status='starting',pid=?,process_started=?,last_started_at=? WHERE user_id=?",(child.pid,started,time.time(),uid))
            instance=self.store.instance(uid)
            for _ in range(100):
                if child.poll() is not None: break
                if self.health(instance):
                    with self.store.db() as db: db.execute("UPDATE instances SET status='running' WHERE user_id=?",(uid,))
                    self.store.event('started',uid)
                    return self.store.instance(uid)
                time.sleep(.1)
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill();child.wait(timeout=5)
            with self.store.db() as db: db.execute("UPDATE instances SET status='stopped',pid=NULL,process_started=NULL WHERE user_id=?",(uid,))
            self.store.event('start_failed',uid)
            raise BetaError('工作区启动失败，请稍后再试',503)

    def ticket(self, instance):
        return sign_ticket(instance_key(self.store.key,instance['instance_id']),instance['user_id'],instance['instance_id'],self.config.ticket_ttl)

    def stop(self, uid):
        with self.locked():
            instance=self.store.instance(uid);pid=instance['pid']
            if pid and self.process_start(pid)==instance['process_started']:
                # PID + OS creation time + marked per-instance process record; never a port-only kill.
                root=self.root(instance)
                try: record=json.loads((root/'.runtime/process.json').read_text())
                except (OSError,ValueError): raise BetaError('进程身份无法确认，未发送停止信号',503) from None
                if record.get('pid')!=pid or record.get('instance_id')!=instance['instance_id']:
                    raise BetaError('进程身份不匹配，未发送停止信号',503)
                os.kill(pid,signal.SIGTERM)
                child=self.children.get(uid)
                if child:
                    try: child.wait(timeout=8)
                    except subprocess.TimeoutExpired: raise BetaError('实例仍在停止中，请稍后检查',503) from None
            with self.store.db() as db: db.execute("UPDATE instances SET status='stopped',pid=NULL,process_started=NULL WHERE user_id=?",(uid,))
            self.store.event('stopped',uid)

    def account_status(self, username, enabled):
        name=self.store.username(username)
        with self.store.db() as db:
            row=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if not row: raise BetaError('用户不存在',404)
        uid=row[0];instance=self.store.instance(uid);root=self.root(instance)
        # Revoke the central sessions before any stop operation can fail.
        with self.store.db() as db:
            db.execute('UPDATE users SET status=? WHERE id=?',('active' if enabled else 'disabled',uid))
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        auth=AuthStore(root,instance['instance_id'])
        with auth.connect() as db:
            db.execute('UPDATE accounts SET enabled=?,revision=revision+1',(int(enabled),))
            db.execute('DELETE FROM sessions')
        if not enabled: self.stop(uid)
        self.store.event('enabled' if enabled else 'disabled',uid)

    def close(self):
        for uid,child in list(self.children.items()):
            if child.poll() is None:
                try: self.stop(uid)
                except BetaError: pass
