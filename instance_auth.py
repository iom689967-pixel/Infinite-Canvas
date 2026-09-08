"""Local assistant credentials and revocable sessions; no dependency or default password.

Passwords use OpenSSL scrypt via hashlib (N=2**17, r=8, p=1). Session tokens are
random bearer values, stored as SHA-256 digests (not password hashes) in SQLite.
This module intentionally does not import instance_paths: local administration must
work while the service owns its independent data-root process lock.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import BoundedSemaphore
import time

PRINCIPAL = ContextVar("instance_principal", default=None)
PASSWORD_JOBS = BoundedSemaphore(2)
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("密码长度必须为 12–1024 个字符")
    salt = secrets.token_bytes(32)
    with PASSWORD_JOBS:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                                p=SCRYPT_P, dklen=64, maxmem=256 * 1024 * 1024)
    return "scrypt$131072$8$1$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        if not isinstance(password, str) or len(password) > 1024:
            return False
        with PASSWORD_JOBS:
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                    n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=64,
                                    maxmem=256 * 1024 * 1024)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def validate_root(root, instance_id, *, initialize=False):
    raw = Path(root)
    if not raw.is_absolute() or raw.is_symlink() or ".." in raw.parts:
        raise ValueError("需要明确的绝对实例目录，不能使用符号链接")
    root = raw.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", instance_id):
        raise ValueError("实例身份不合法")
    marker = root / ".instance.json"
    if not marker.is_file():
        raise ValueError("请先初始化空实例目录；管理命令不会接管未标记的用户数据")
    if marker.is_symlink() or json.loads(marker.read_text()) != {"instance_id": instance_id, "data_root": str(root)}:
        raise ValueError("实例目录与身份不匹配")
    private = root / ".auth"
    if private.is_symlink() or any(p.is_symlink() for p in private.rglob("*")):
        raise ValueError("认证目录禁止符号链接")
    if initialize:
        private.mkdir(mode=0o700, exist_ok=True)
    return root


class AuthStore:
    def __init__(self, root, instance_id, *, initialize=False, ttl=28800):
        self.root = validate_root(root, instance_id, initialize=initialize)
        self.instance_id = instance_id
        self.db_path = self.root / ".auth/access.sqlite3"
        self.ttl = ttl
        self.boot = secrets.token_hex(16)
        if not self.db_path.is_file() and not initialize:
            raise RuntimeError("显式实例必须先通过本机命令创建账号，禁止匿名启动")
        if initialize:
            with self.connect() as db:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                  username TEXT PRIMARY KEY, instance_id TEXT NOT NULL, role TEXT NOT NULL,
                  password_hash TEXT NOT NULL, enabled INTEGER NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, instance_id TEXT NOT NULL,
                  revision INTEGER NOT NULL, expires REAL NOT NULL, csrf TEXT NOT NULL, boot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
                """)
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def connect(self):
        if self.db_path.is_symlink():
            raise RuntimeError("认证数据库禁止符号链接")
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start_server(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM accounts").fetchall()
            if len(rows) != 1 or rows[0]["instance_id"] != self.instance_id or rows[0]["role"] != "assistant":
                raise RuntimeError("显式实例必须绑定唯一的 assistant 账号")
            # Restart deliberately logs everyone out. No portable/replayable disk sessions.
            db.execute("DELETE FROM sessions")
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))

    def create_account(self, username, password):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
            raise ValueError("账号名称不合法")
        encoded = password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM accounts").fetchone():
                raise ValueError("一人一实例：已有账号，请使用重置密码或禁用命令")
            db.execute("INSERT INTO accounts VALUES (?,?,?,?,1,1)",
                       (username, self.instance_id, "assistant", encoded))

    def change_account(self, username, *, password=None, disable=False):
        encoded = password_hash(password) if password is not None else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM accounts WHERE username=? AND instance_id=?", (username, self.instance_id)).fetchone()
            if not account:
                raise ValueError("本实例没有此账号")
            db.execute("UPDATE accounts SET password_hash=?, enabled=?, revision=revision+1 WHERE username=?",
                       (encoded or account["password_hash"], 0 if disable else account["enabled"], username))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))

    def login(self, username, password, peer):
        now = time.time()
        # Per-peer aggregate limit avoids username enumeration and username-spray bypass.
        bucket = hashlib.sha256(str(peer).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE started < ?", (now - 300,))
            row = db.execute("SELECT count FROM attempts WHERE bucket=?", (bucket,)).fetchone()
            if row and row["count"] >= 10:
                return None, "limited"
            db.execute("INSERT INTO attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (bucket, now))
            account = db.execute("SELECT * FROM accounts WHERE username=? AND instance_id=?", (username, self.instance_id)).fetchone()
        valid = verify_password(password, account["password_hash"] if account else self.dummy_hash)
        if not valid or not account or not account["enabled"]:
            return None, "invalid"
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
            if not current or not current["enabled"] or current["revision"] != account["revision"]:
                return None, "invalid"
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (self.digest(token), username,
                       self.instance_id, account["revision"], now + self.ttl, csrf, self.boot))
            db.execute("DELETE FROM attempts WHERE bucket=?", (bucket,))
        return token, None

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def validate(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.connect() as db:
            row = db.execute("""SELECT s.*, a.role, a.enabled, a.revision AS account_revision
              FROM sessions s JOIN accounts a ON a.username=s.username
              WHERE s.token_hash=? AND s.instance_id=? AND a.instance_id=?""",
                             (self.digest(token), self.instance_id, self.instance_id)).fetchone()
        if not row or not row["enabled"] or row["role"] != "assistant" or row["revision"] != row["account_revision"] or row["expires"] <= time.time() or row["boot"] != self.boot:
            return None
        return {"username": row["username"], "role": "assistant", "instance_id": self.instance_id,
                "csrf": row["csrf"], "expires": row["expires"],
                "subject": hashlib.sha256((self.instance_id+":"+row["username"]).encode()).hexdigest()}

    def revoke(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (self.digest(token or ""),))


def task_namespace(value):
    principal = PRINCIPAL.get()
    if not principal:
        return value
    return hashlib.sha256((principal["subject"]+":"+value).encode()).hexdigest()
