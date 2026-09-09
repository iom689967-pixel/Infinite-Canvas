"""Short-lived, instance-bound one-use tickets. Never placed in browser URLs."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path


def instance_key(master, instance_id):
    return hmac.new(master, ('mio-instance:'+instance_id).encode(), hashlib.sha256).digest()


def sign_ticket(key, user_id, instance_id, ttl=45):
    claims={'user_id':user_id,'instance_id':instance_id,'nonce':secrets.token_urlsafe(32),
            'issued':int(time.time()),'expires':int(time.time())+ttl}
    body=base64.urlsafe_b64encode(json.dumps(claims,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
    return body+'.'+hmac.new(key,body.encode(),hashlib.sha256).hexdigest()


def verify_ticket(key, ticket, user_id, instance_id):
    try:
        if not isinstance(ticket,str) or len(ticket)>2048: return None
        body, signature=ticket.split('.')
        if not hmac.compare_digest(signature,hmac.new(key,body.encode(),hashlib.sha256).hexdigest()): return None
        claims=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if set(claims) != {'user_id','instance_id','nonce','issued','expires'}: return None
        if claims['user_id']!=user_id or claims['instance_id']!=instance_id: return None
        if not isinstance(claims['issued'],int) or not isinstance(claims['expires'],int): return None
        if not claims['issued']-2 <= time.time() < claims['expires'] or not 0 < claims['expires']-claims['issued'] <= 60: return None
        if not isinstance(claims['nonce'],str) or len(claims['nonce'])!=43: return None
        return claims
    except (ValueError,TypeError,KeyError): return None


def consume_ticket(store, ticket):
    config=json.loads((store.root/'.auth/gateway-handoff.json').read_text())
    claims=verify_ticket(bytes.fromhex(config['key']),ticket,config['user_id'],store.instance_id)
    if not claims: return None
    token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS handoffs (nonce TEXT PRIMARY KEY, expires REAL NOT NULL)')
        db.execute('DELETE FROM handoffs WHERE expires < ?',(time.time(),))
        if db.execute('SELECT 1 FROM handoffs WHERE nonce=?',(claims['nonce'],)).fetchone(): return None
        account=db.execute('SELECT * FROM accounts WHERE username=? AND enabled=1 AND instance_id=?',
                           (config['username'],store.instance_id)).fetchone()
        if not account: return None
        db.execute('INSERT INTO handoffs VALUES (?,?)',(claims['nonce'],claims['expires']))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',(store.digest(token),account['username'],store.instance_id,
                    account['revision'],time.time()+store.ttl,csrf,store.boot))
    return token


def health_proof(root, challenge):
    if not isinstance(challenge,str) or len(challenge)!=43: return ''
    config=json.loads((Path(root)/'.auth/gateway-handoff.json').read_text())
    return hmac.new(bytes.fromhex(config['key']),('health:'+challenge).encode(),hashlib.sha256).hexdigest()
