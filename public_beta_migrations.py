"""Additive, transactional Gateway migrations. Existing identities/sessions stay intact."""
SCHEMA_VERSION = 1


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
