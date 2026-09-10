"""Local Public Beta registry. No provider credentials or workspace content belongs here."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
import time
import urllib.parse
import uuid

from instance_auth import password_hash, verify_password


class BetaError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def private_directory(value):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or '..' in raw.parts or raw.is_symlink():
        raise ValueError('需要独立的绝对目录，禁止符号链接')
    root = raw.resolve()
    program = Path(__file__).resolve().parent
    if root == Path.home().resolve() or root.is_relative_to(program) or program.is_relative_to(root):
        raise ValueError('数据目录不能包含程序目录或整个 Home')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@dataclass
class BetaConfig:
    root: Path
    instances_root: Path
    host: str = '127.0.0.1'
    port: int = 32100
    port_start: int = 32000
    port_end: int = 32999
    max_users: int = 20
    storage_quota: int = 5 * 1024**3
    max_upload: int = 50 * 1024**2
    concurrency: int = 2
    register_limit: int = 5
    login_limit: int = 10
    window_seconds: int = 300
    ticket_ttl: int = 45
    session_ttl: int = 28800
    mock_upstreams: str = ''
    external_origin: str = ''
    trusted_proxies: tuple = ()
    registration_mode: str = 'open'
    invite_hash: str = ''
    min_free_disk: int = 2 * 1024**3
    max_running_instances: int = 4
    backup_root: Path = None
    backup_retention: int = 7

    def __post_init__(self):
        self.root = private_directory(self.root)
        self.instances_root = private_directory(self.instances_root)
        self.backup_root = private_directory(self.backup_root or self.root.parent/'backups')
        if self.root == self.instances_root or self.root.is_relative_to(self.instances_root) or self.instances_root.is_relative_to(self.root):
            raise ValueError('Gateway 与用户实例目录必须分离')
        for left,right in ((self.backup_root,self.root),(self.backup_root,self.instances_root)):
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('备份目录必须与运行数据分离')
        if self.host != '127.0.0.1':
            raise ValueError('Gateway 只能监听 127.0.0.1')
        if not 1024 <= self.port <= 65535 or not 1024 <= self.port_start <= self.port_end <= 65535:
            raise ValueError('无效 loopback 端口范围')
        for value in (self.max_users, self.storage_quota, self.max_upload, self.concurrency, self.register_limit, self.login_limit, self.window_seconds, self.backup_retention):
            if type(value) is not int or value < 1:
                raise ValueError('资源限制必须为正整数')
        if type(self.min_free_disk) is not int or self.min_free_disk < 0:
            raise ValueError('磁盘安全余量必须是非负整数')
        if not 1 <= self.concurrency <= 8 or not 1 <= self.max_running_instances <= 20 or not 1 <= self.ticket_ttl <= 60 or not 1 <= self.session_ttl <= 86400:
            raise ValueError('会话或并发配置无效')
        if self.registration_mode not in {'open','closed','invite'}:
            raise ValueError('注册模式必须为 open、closed 或 invite')
        if self.registration_mode == 'invite' and not re.fullmatch(r'[0-9a-f]{64}',self.invite_hash):
            raise ValueError('邀请码模式需要 SHA-256 摘要')
        if self.invite_hash and not re.fullmatch(r'[0-9a-f]{64}',self.invite_hash):
            raise ValueError('邀请码摘要格式无效')
        parsed=urllib.parse.urlsplit(self.external_origin or self.listen_origin)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
            raise ValueError('Public Beta Origin 必须是完整且无路径的 http(s) origin')
        normalized=f'{parsed.scheme}://{parsed.netloc}'
        if self.external_origin and self.external_origin.rstrip('/') != normalized:
            raise ValueError('Public Beta Origin 格式无效')
        if self.external_origin and parsed.scheme != 'https':
            raise ValueError('公网 Public Beta Origin 必须使用 HTTPS')
        self.external_origin=normalized if self.external_origin else ''
        normalized_proxies=[]
        for value in self.trusted_proxies:
            address=ipaddress.ip_address(value)
            if not address.is_loopback: raise ValueError('Trusted proxy 只允许本机地址')
            normalized_proxies.append(str(address))
        self.trusted_proxies=tuple(normalized_proxies)
        if self.external_origin and not self.trusted_proxies:
            raise ValueError('公网 Origin 必须配置本机 trusted proxy')
        if self.mock_upstreams and not all(re.fullmatch(r'127\.0\.0\.1:[0-9]{4,5}', s) for s in self.mock_upstreams.split(',')):
            raise ValueError('Mock 只允许管理员明确配置 loopback 上游')

    @property
    def listen_origin(self): return f'http://{self.host}:{self.port}'

    @property
    def origin(self): return self.external_origin or self.listen_origin

    @property
    def secure(self): return self.origin.startswith('https://')

    @property
    def public_host(self): return urllib.parse.urlsplit(self.origin).netloc

    @property
    def proxied(self): return bool(self.external_origin)

    def invite_valid(self,value):
        if self.registration_mode != 'invite': return self.registration_mode == 'open'
        if not isinstance(value,str) or len(value)>1024: return False
        return secrets.compare_digest(hashlib.sha256(value.encode()).hexdigest(),self.invite_hash)

    @classmethod
    def from_env(cls):
        env = os.environ
        names = {'port':'GATEWAY_PORT', 'port_start':'INSTANCE_PORT_START', 'port_end':'INSTANCE_PORT_END',
                 'max_users':'MAX_PUBLIC_USERS', 'storage_quota':'INSTANCE_STORAGE_QUOTA',
                 'max_upload':'MAX_UPLOAD_BYTES', 'concurrency':'MAX_CONCURRENT_GENERATIONS',
                 'min_free_disk':'MIN_FREE_DISK_BYTES','max_running_instances':'MAX_RUNNING_INSTANCES',
                 'backup_retention':'PUBLIC_BETA_BACKUP_RETENTION'}
        values = {key:int(env[name]) for key,name in names.items() if name in env}
        return cls(Path(env.get('PUBLIC_BETA_ROOT', '~/.infinite-canvas/public-beta')).expanduser(),
                   Path(env.get('PUBLIC_BETA_INSTANCES_ROOT', '~/.infinite-canvas/instances')).expanduser(),
                   host=env.get('GATEWAY_HOST','127.0.0.1'),mock_upstreams=env.get('PUBLIC_BETA_MOCK_UPSTREAMS',''),
                   external_origin=env.get('PUBLIC_BETA_ORIGIN',''),
                   trusted_proxies=tuple(filter(None,(v.strip() for v in env.get('PUBLIC_BETA_TRUSTED_PROXIES','').split(',')))),
                   registration_mode=env.get('PUBLIC_BETA_REGISTRATION_MODE','open').strip().lower(),
                   invite_hash=env.get('PUBLIC_BETA_INVITE_CODE_HASH','').strip().lower(),
                   backup_root=Path(env.get('PUBLIC_BETA_BACKUP_ROOT','~/.infinite-canvas/backups')).expanduser(), **values)


class GatewayStore:
    def __init__(self, config):
        self.config = config
        self.path = config.root / 'gateway.sqlite3'
        self.key_path = config.root / 'handoff.key'
        if self.path.is_symlink() or self.key_path.is_symlink():
            raise ValueError('Gateway 私有存储禁止链接')
        if not self.key_path.exists():
            with open(self.key_path, 'xb') as f:
                os.chmod(self.key_path, 0o600); f.write(secrets.token_bytes(32))
        key_stat=self.key_path.stat()
        if not stat.S_ISREG(key_stat.st_mode) or stat.S_IMODE(key_stat.st_mode) & 0o077:
            raise ValueError('Gateway handoff key 权限不安全')
        self.key = self.key_path.read_bytes()
        if len(self.key)!=32: raise ValueError('Gateway handoff key 格式无效')
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, last_login_at REAL);
            CREATE TABLE IF NOT EXISTS instances (instance_id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL,
                instance_slug TEXT UNIQUE NOT NULL, data_root TEXT UNIQUE NOT NULL, assigned_port INTEGER UNIQUE NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, last_started_at REAL, pid INTEGER, process_started TEXT);
            CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL, csrf TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, started REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS security_events (id INTEGER PRIMARY KEY, event TEXT, user_id TEXT, created_at REAL);
            ''')
        os.chmod(self.path, 0o600)
        # CLI does not hash a dummy password; the web login path creates it lazily.
        self.dummy = None

    @contextmanager
    def db(self):
        if self.path.is_symlink():
            raise ValueError('Gateway 私有存储禁止链接')
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,31}', value):
            raise BetaError('用户名须为 3–32 位字母、数字、下划线或连字符')
        return value.lower()

    def event(self, event, user_id=''):
        # Caller-selected enum only, never request content/IP/password/raw exception.
        if event not in {'registered','registration_failed','login','login_failed','disabled','enabled','started','stopped','start_failed'}:
            raise ValueError('Unknown security event')
        with self.db() as db:
            db.execute('INSERT INTO security_events(event,user_id,created_at) VALUES (?,?,?)',(event,user_id,time.time()))
            db.execute('DELETE FROM security_events WHERE id < (SELECT COALESCE(MAX(id),0)-10000 FROM security_events)')

    def limit(self, kind, value, maximum):
        bucket = hashlib.sha256((kind+':'+str(value)).encode()).hexdigest()
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE started < ?', (now-self.config.window_seconds,))
            row = db.execute('SELECT count FROM attempts WHERE bucket=?',(bucket,)).fetchone()
            if row and row[0] >= maximum:
                raise BetaError('尝试过于频繁，请稍后再试',429)
            db.execute('INSERT INTO attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,now))

    def reserve(self, username, encoded, port):
        uid, iid = uuid.uuid4().hex, uuid.uuid4().hex
        root = self.config.instances_root / iid
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM users').fetchone()[0] >= self.config.max_users:
                raise BetaError('当前 Mio Canvas Beta 名额已满。',409)
            if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                raise BetaError('用户名已被使用',409)
            db.execute('INSERT INTO users VALUES (?,?,?, ?,?,NULL)',(uid,username,encoded,'provisioning',time.time()))
            db.execute('INSERT INTO instances VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL)',(iid,uid,iid,str(root),port,'provisioning',time.time()))
        return self.instance(uid)

    def instance(self, user_id):
        with self.db() as db:
            row = db.execute('SELECT i.*, u.username, u.status AS user_status FROM instances i JOIN users u ON u.id=i.user_id WHERE user_id=?',(user_id,)).fetchone()
        if not row: raise BetaError('工作区不存在',404)
        return dict(row)

    def activate(self, uid):
        with self.db() as db:
            db.execute("UPDATE users SET status='active' WHERE id=? AND status='provisioning'",(uid,))
            db.execute("UPDATE instances SET status='stopped' WHERE user_id=?",(uid,))
            db.execute('INSERT INTO security_events(event,user_id,created_at) VALUES (?,?,?)',('registered',uid,time.time()))

    def rollback(self, uid):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM users WHERE id=? AND status='provisioning'",(uid,)).fetchone():
                db.execute('DELETE FROM instances WHERE user_id=?',(uid,));db.execute('DELETE FROM users WHERE id=?',(uid,))
        self.event('registration_failed')

    def session(self, uid):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM users WHERE id=? AND status='active'",(uid,)).fetchone():
                raise BetaError('账号当前不可用',401)
            db.execute('DELETE FROM sessions WHERE expires < ?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(self.digest(token),uid,time.time()+self.config.session_ttl,csrf))
            db.execute('UPDATE users SET last_login_at=? WHERE id=?',(time.time(),uid))
        return token

    @staticmethod
    def digest(value): return hashlib.sha256(value.encode()).hexdigest()

    def principal(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token or ''): return None
        with self.db() as db:
            row = db.execute("SELECT u.id,u.username,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE digest=? AND expires>? AND u.status='active'",(self.digest(token),time.time())).fetchone()
        return dict(row) if row else None

    def revoke(self, token):
        with self.db() as db: db.execute('DELETE FROM sessions WHERE digest=?',(self.digest(token or ''),))

    def login(self, username, password, peer):
        self.limit('login-ip',peer,self.config.login_limit)
        name = str(username).lower() if isinstance(username,str) else ''
        with self.db() as db: row = db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
        if self.dummy is None: self.dummy = password_hash(secrets.token_urlsafe(32))
        if not verify_password(password, row['password_hash'] if row else self.dummy) or not row or row['status'] != 'active':
            self.event('login_failed');raise BetaError('账号或密码不正确',401)
        self.event('login',row['id'])
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE bucket=?',(hashlib.sha256(('login-ip:'+str(peer)).encode()).hexdigest(),))
        return self.session(row['id'])
