"""Release-external, fail-closed maintenance fence and durable activity journal.

The administrator owns state.json and its directory. Workers may only lock the
fixed lock file and write the private activity subdirectory. A single flock spans
gate admission AND activity insertion, as well as draining/sealing transitions.
No HTTP administration endpoint is provided.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
import uuid

from starlette.responses import JSONResponse
from maintenance_routes import classify

MESSAGE = '工作区维护中，暂时不能提交新任务，请稍后再试。'
DETAIL = {'code': 'maintenance', 'message': MESSAGE}
CURRENT_ACTIVITY = ContextVar('maintenance_activity', default=None)
MAINTENANCE_IO = ContextVar('maintenance_server_io', default=False)
SAFE_PHASES = {'http', 'runner', 'stream', 'upload', 'result', 'recovery', 'local_write', 'read',
               'model', 'registration', 'unreviewed', 'control', 'llm', 'llm_stream', 'agent', 'caption',
               'classification', 'video', 'image', 'provider_probe', 'runner_image', 'runner_video'}


class MaintenanceUnavailable(RuntimeError):
    pass


def process_identity(pid=None):
    pid = pid or os.getpid()
    result = subprocess.run(['ps', '-p', str(int(pid)), '-o', 'lstart='],
                            capture_output=True, text=True, timeout=3)
    return result.stdout.strip() if result.returncode == 0 else ''


class Maintenance:
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise MaintenanceUnavailable()
        self.pid = os.getpid()
        self.start = process_identity()

    def validate(self):
        for path in (self.root, *self.root.parents):
            if path.is_symlink():
                raise MaintenanceUnavailable()
        directory = self.root.stat()
        state = (self.root/'state.json').lstat()
        lock = (self.root/'gate.lock').lstat()
        if (not stat.S_ISDIR(directory.st_mode) or stat.S_IMODE(directory.st_mode) & 0o022
                or not stat.S_ISREG(state.st_mode) or state.st_uid != directory.st_uid
                or stat.S_IMODE(state.st_mode) & 0o022 or not stat.S_ISREG(lock.st_mode)
                or lock.st_uid != directory.st_uid or (self.root/'activity/journal.sqlite3').is_symlink()):
            raise MaintenanceUnavailable()

    @contextmanager
    def locked(self):
        token = MAINTENANCE_IO.set(True)
        fd = None
        try:
            self.validate()
            fd = os.open(self.root/'gate.lock', os.O_RDWR | os.O_NOFOLLOW)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fd is not None:
                os.close(fd)
            MAINTENANCE_IO.reset(token)

    @contextmanager
    def db(self):
        token = MAINTENANCE_IO.set(True)
        db = None
        try:
            db = sqlite3.connect(self.root/'activity/journal.sqlite3', timeout=5)
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            if db is not None:
                db.close()
            MAINTENANCE_IO.reset(token)

    def state(self):
        self.validate()
        value = json.loads((self.root/'state.json').read_text())
        if set(value) != {'schema', 'phase', 'epoch'} or value['schema'] != 1 or value['phase'] not in {'open', 'draining', 'sealed'}:
            raise MaintenanceUnavailable()
        return value

    def admit(self, instance, kind, *, parent=None, delegated=False):
        if kind not in SAFE_PHASES:
            raise MaintenanceUnavailable()
        with self.locked():
            state = self.state()
            with self.db() as db:
                inherited = parent and (db.execute('SELECT 1 FROM activities WHERE id=? AND instance=? AND kind=?',
                                                    (parent, 'gateway', kind)).fetchone() if delegated else
                                         db.execute('SELECT 1 FROM activities WHERE id=? AND pid=? AND start=?',
                                                    (parent, self.pid, self.start)).fetchone())
                if not inherited and (state['phase'] == 'sealed' or state['phase'] == 'draining'
                                      and kind not in {'read', 'recovery', 'local_write', 'control'}):
                    return None
                identity = uuid.uuid4().hex
                db.execute('INSERT INTO activities VALUES (?,?,?,?,?,?,?)',
                           (identity, str(instance), self.pid, self.start, kind, state['epoch'], time.time()))
                return Lease(self, identity, instance)

    def child(self, instance, kind='runner'):
        parent = CURRENT_ACTIVITY.get()
        lease = self.admit(instance, kind, parent=parent.id if parent and parent.gate.root == self.root else None)
        if lease is None:
            from fastapi import HTTPException
            raise HTTPException(503, DETAIL)
        return lease

    def startup(self, instance):
        """Trusted server lifecycle only, not a public admission bypass.

        A planned sealed restart must initialize, but its migrations cannot be
        mistaken for a quiescent process. No model submission is authorized here.
        """
        with self.locked(), self.db() as db:
            state=self.state();identity=uuid.uuid4().hex
            db.execute('INSERT INTO activities VALUES (?,?,?,?,?,?,?)',
                       (identity,str(instance),self.pid,self.start,'local_write',state['epoch'],time.time()))
        return Lease(self,identity,instance)

    def counts_locked(self):
        with self.db() as db:
            rows = db.execute('SELECT instance,pid,start,kind FROM activities').fetchall()
        counts = {}
        orphaned = 0
        identities = {}
        for row in rows:
            counts[row['kind']] = counts.get(row['kind'], 0) + 1
            key = (row['pid'], row['start'])
            if key not in identities:
                identities[key] = process_identity(key[0]) == key[1] and bool(key[1])
            orphaned += not identities[key]
        return {'active': len(rows), 'by_phase': counts, 'orphaned': orphaned}

    def status(self):
        with self.locked():
            return {**self.state(), **self.counts_locked()}

    def uncertain_llm(self, instance, reason='llm_response_unknown'):
        if reason not in {'llm_response_unknown', 'legacy_request_unknown'}:
            raise MaintenanceUnavailable()
        identity = uuid.uuid4().hex
        with self.locked(), self.db() as db:
            db.execute('INSERT INTO uncertainties VALUES (?,?,?,?,?)',
                       (identity, str(instance), self.pid, self.start, reason))
        return identity

    def complete_llm(self, identity):
        with self.locked(), self.db() as db:
            db.execute('DELETE FROM uncertainties WHERE id=? AND pid=? AND start=?',
                       (identity, self.pid, self.start))


class Lease:
    def __init__(self, gate, identity, instance):
        self.gate, self.id, self.instance = gate, identity, instance

    def finish(self):
        # Losing the journal is a blocker, never a reason to assert zero activity.
        with self.gate.locked(), self.gate.db() as db:
            db.execute('DELETE FROM activities WHERE id=? AND pid=? AND start=?',
                       (self.id, self.gate.pid, self.gate.start))


async def tracked_to_thread(function, *args, **kwargs):
    """A canceled await does not mean the underlying thread finished writing."""
    parent = CURRENT_ACTIVITY.get()
    if not parent:
        return await asyncio.to_thread(function, *args, **kwargs)
    lease = parent.gate.child(parent.instance, 'result')
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    def finished(result):
        try:
            result.exception()  # Consume errors when the HTTP waiter disconnected.
        finally:
            lease.finish()
    task.add_done_callback(finished)
    return await asyncio.shield(task)


class MaintenanceMiddleware:
    """Pure ASGI: release only after body iteration, background work and finally.

The whole request, including slow receive and response close, is tracked. Native
stats WebSockets have no model operations and are intentionally not drain blockers.
Their planned restart disconnect remains visible to deployment operators.
"""
    def __init__(self, app, *, root, instance='gateway'):
        self.app, self.gate, self.instance = app, root if isinstance(root, Maintenance) else Maintenance(root), instance
        with self.gate.locked(), self.gate.db() as db:
            db.execute('INSERT OR REPLACE INTO processes VALUES (?,?,?)',
                       (instance, self.gate.pid, self.gate.start))

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            lease=self.gate.startup(self.instance)
            token=CURRENT_ACTIVITY.set(lease)
            async def lifecycle_send(message):
                if message['type'] in {'lifespan.startup.complete','lifespan.startup.failed'}:
                    lease.finish()
                    CURRENT_ACTIVITY.set(None)
                await send(message)
            try:
                return await self.app(scope,receive,lifecycle_send)
            finally:
                CURRENT_ACTIVITY.reset(token)
                lease.finish()
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method, path = scope['method'], scope['path']
        if method in {'GET', 'HEAD'} and (path in {'/healthz', '/__mio/health', '/login', '/register',
            '/verify-email', '/resend-verification'} or path.startswith('/static/')):
            return await self.app(scope, receive, send)
        try:
            # Gateway strips client-supplied copies and supplies its still-live,
            # unexposed random admission ID. The journal is not publicly readable.
            parent = next((v.decode() for k,v in scope.get('headers', []) if k.lower() == b'x-mio-maintenance-parent'), None)
            lease = self.gate.admit(self.instance, classify(method, path),
                                    parent=parent if self.instance != 'gateway' else None, delegated=True)
        except (OSError, ValueError, sqlite3.Error, MaintenanceUnavailable):
            lease = None
        if lease is None:
            response = JSONResponse({'detail': DETAIL}, 503, headers={
                'Cache-Control': 'no-store', 'X-Mio-Maintenance': '1', 'Retry-After': '60'})
            return await response(scope, receive, send)
        token = CURRENT_ACTIVITY.set(lease)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_ACTIVITY.reset(token)
            lease.finish()


def initialize(root, *, worker_uid=None, worker_gid=None):
    """Administrator only; callers enforce root or an explicit temporary sandbox."""
    root = Path(root).absolute()
    if root.exists() or root.is_symlink():
        raise ValueError('维护目录已存在；禁止重新初始化或清空活动')
    root.mkdir(mode=0o750, parents=True)
    worker_uid = os.getuid() if worker_uid is None else worker_uid
    worker_gid = os.getgid() if worker_gid is None else worker_gid
    if os.getuid() == 0:
        os.chown(root, 0, worker_gid)
    (root/'state.json').write_text(json.dumps({'schema': 1, 'phase': 'draining', 'epoch': 1}))
    (root/'state.json').chmod(0o640)
    (root/'gate.lock').touch(mode=0o660)
    (root/'gate.lock').chmod(0o660)
    (root/'activity').mkdir(mode=0o700)
    if os.getuid() == 0:
        for name in ('state.json', 'gate.lock'):
            os.chown(root/name, 0, worker_gid)
        os.chown(root/'activity', worker_uid, worker_gid)
    with Maintenance(root).db() as db:
        db.execute('CREATE TABLE activities (id TEXT PRIMARY KEY, instance TEXT, pid INTEGER, start TEXT, kind TEXT, epoch INTEGER, accepted REAL)')
        db.execute('CREATE TABLE processes (instance TEXT PRIMARY KEY, pid INTEGER, start TEXT)')
        db.execute('CREATE TABLE uncertainties (id TEXT PRIMARY KEY, instance TEXT, pid INTEGER, start TEXT, reason TEXT)')
        db.execute('CREATE TABLE reconciliations (id TEXT, epoch INTEGER, evidence_sha256 TEXT, checked REAL)')
    (root/'activity/journal.sqlite3').chmod(0o600)
    if os.getuid() == 0:
        os.chown(root/'activity/journal.sqlite3', worker_uid, worker_gid)
