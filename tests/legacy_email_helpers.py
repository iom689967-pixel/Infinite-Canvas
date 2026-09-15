"""Only test fixtures may seed already-issued legacy tokens after schema upgrade."""
import hashlib
import secrets
import time
import uuid

from instance_auth import password_hash
from public_beta_email_address import normalize_email


def legacy_pending(store,mail,name,password,email=None):
    now=time.time();display,normalized=normalize_email(email or name+'@example.test',testing=True)
    uid=uuid.uuid4().hex;raw=secrets.token_urlsafe(32);expires=now+store.config.verification_ttl
    with store.db() as db:
        db.execute('''INSERT INTO users(id,username,password_hash,status,created_at,email,email_normalized,
            email_status,legacy_username_login,pending_expires_at) VALUES (?,?,?,'pending_verification',?,?,?,'pending',0,?)''',
            (uid,name,password_hash(password),now,display,normalized,now+store.config.pending_ttl))
        db.execute("INSERT INTO account_tokens VALUES (?,?, 'verify_email',?,?,NULL,NULL)",
                   (hashlib.sha256(raw.encode()).hexdigest(),uid,now,expires))
        db.execute('UPDATE email_link_transition SET legacy_until=MAX(legacy_until,?)',(expires,))
    if mail is not None:
        mail.messages.append({'recipient':display,'link':store.config.origin+'/verify-email#token='+raw,'minutes':60})
    return uid,raw


def already_issued_link(store,mail,uid):
    """Convert a test pending to the exact pre-upgrade token structure, not a new mail send."""
    now=time.time();raw=secrets.token_urlsafe(32);expires=now+store.config.verification_ttl
    with store.db() as db:
        email=db.execute('SELECT email FROM users WHERE id=?',(uid,)).fetchone()[0]
        db.execute('DELETE FROM email_code_pending WHERE user_id=?',(uid,))
        db.execute("INSERT INTO account_tokens VALUES (?,?,'verify_email',?,?,NULL,NULL)",(hashlib.sha256(raw.encode()).hexdigest(),uid,now,expires))
        db.execute('UPDATE email_link_transition SET legacy_until=MAX(legacy_until,?)',(expires,))
    message={'recipient':email,'link':store.config.origin+'/verify-email#token='+raw,'minutes':60}
    if mail.messages and mail.messages[-1].get('recipient')==email:mail.messages[-1]=message
    else:mail.messages.append(message)
    return raw
