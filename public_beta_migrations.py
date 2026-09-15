"""Additive, transactional Gateway migrations. Existing identities/sessions stay intact."""
import time

SCHEMA_VERSION = 2


def migrate(db):
    db.execute('BEGIN IMMEDIATE')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError('Gateway 数据库版本高于当前程序')
    if version == 0:
        for definition in (
            'email TEXT', 'email_normalized TEXT', 'email_verified_at REAL',
            "email_status TEXT NOT NULL DEFAULT 'legacy'",
            'legacy_username_login INTEGER NOT NULL DEFAULT 1',
            'pending_expires_at REAL',
        ):
            db.execute('ALTER TABLE users ADD COLUMN ' + definition)
        db.execute('CREATE UNIQUE INDEX users_email_unique ON users(email_normalized) WHERE email_normalized IS NOT NULL')
        db.execute('''CREATE TABLE account_tokens (
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, purpose TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL,
            used_at REAL, invalidated_at REAL)''')
        db.execute('CREATE INDEX account_tokens_user ON account_tokens(user_id,purpose)')
        db.execute('PRAGMA user_version=1')
        version=1
    if version == 1:
        db.execute('''CREATE TABLE email_code_pending (
            pending_digest TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL,
            owner_digest TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
            code_hmac TEXT, code_history TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, expires_at REAL,
            failed_attempts INTEGER NOT NULL DEFAULT 0, resend_available_at REAL,
            delivery_state TEXT NOT NULL DEFAULT 'idle', sending_until REAL,
            completed_at REAL)''')
        db.execute('''CREATE TABLE email_code_limits (
            bucket TEXT PRIMARY KEY, started_at REAL NOT NULL, count INTEGER NOT NULL)''')
        db.execute('CREATE TABLE email_link_transition (legacy_until REAL NOT NULL)')
        # Only already issued links survive this one-time transition. Never extend
        # their deadline on Gateway/Supervisor restart or generate another link.
        latest=db.execute("SELECT MAX(expires_at) FROM account_tokens WHERE purpose='verify_email' AND used_at IS NULL AND invalidated_at IS NULL").fetchone()[0]
        now=time.time()
        db.execute('INSERT INTO email_link_transition VALUES (?)',(min(latest or now,now+3600),))
        db.execute('PRAGMA user_version=2')
