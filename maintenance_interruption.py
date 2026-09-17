"""Root-only, single-window interruption approval; never reconciles remote work."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time

from instance_maintenance import Maintenance


def fingerprint(row):
    # This is the canonical format used by the pre-existing private audit.
    fields = {key: row[key] for key in ('id', 'instance', 'pid', 'start', 'reason')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def private_json(path):
    path = Path(path)
    parent = path.parent.lstat()
    if (not path.is_absolute() or path.parent.resolve() != path.parent
            or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o022):
        raise ValueError('授权清单必须位于管理员受保护的绝对目录')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('授权清单必须是root所有的0600普通文件')
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise ValueError('授权清单过大')
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def validate_approval(value, state, now):
    keys = {'schema', 'window_id', 'epoch', 'source_revision', 'target_revision',
            'created_at', 'expires_at', 'records', 'remote_status_and_cost_unknown'}
    if not isinstance(value, dict) or set(value) != keys or value['schema'] != 1:
        raise ValueError('授权schema不匹配')
    if not re.fullmatch('[0-9a-f]{32}', str(value['window_id'])):
        raise ValueError('窗口ID无效')
    if type(value['epoch']) is not int or value['epoch'] != state['epoch']:
        raise ValueError('授权epoch已失效')
    for key in ('source_revision', 'target_revision'):
        if not re.fullmatch('[0-9a-f]{40}', str(value[key])):
            raise ValueError('授权版本无效')
    if value['source_revision'] == value['target_revision']:
        raise ValueError('源与目标版本不能相同')
    for key in ('created_at', 'expires_at'):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]):
            raise ValueError('授权时间无效')
    if not value['created_at'] <= now < value['expires_at'] <= value['created_at'] + 7200:
        raise ValueError('授权过期或超过两小时窗口')
    rows = value['records']
    if not isinstance(rows, list) or len(rows) != 4 or value['remote_status_and_cost_unknown'] is not True:
        raise ValueError('必须明确批准四条历史未知记录')
    if any(not isinstance(r, dict) or set(r) != {'id', 'fingerprint'}
           or not re.fullmatch('[0-9a-f]{32}', str(r['id']))
           or not re.fullmatch('[0-9a-f]{64}', str(r['fingerprint'])) for r in rows):
        raise ValueError('授权记录身份无效')
    if len({r['id'] for r in rows}) != 4 or len({r['fingerprint'] for r in rows}) != 4:
        raise ValueError('授权记录重复')


def revision(path):
    path = Path(path).resolve()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'safe.directory='+str(path), '-C', str(path), *args],
                                       text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != path or git('status', '--porcelain'):
        raise ValueError('release必须为独立干净的Git检出')
    return git('rev-parse', 'HEAD')


def running_source(gate, gateway_db, source):
    from public_beta_maintenance import read_db
    with read_db(Path(gateway_db)) as db:
        processes = [(r[0], Path(r[1]), source/'public_beta_worker.py', ['serve'])
                     for r in db.execute("SELECT pid,data_root FROM instances WHERE status IN ('running','starting')")]
    with gate.db() as db:
        row = db.execute("SELECT pid FROM processes WHERE instance='gateway'").fetchone()
    if not row:
        raise ValueError('Gateway覆盖缺失')
    processes.append((row[0], source, source/'public_beta.py', []))
    for pid, cwd, entrypoint, tail in processes:
        # Production platform is Linux. No guessed or injected process identity.
        proc = Path('/proc')/str(int(pid))
        argv = (proc/'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
        # Instance intentionally chdirs to its isolated data root. Program source
        # is the exact fixed entrypoint, not that data working directory.
        if (Path(os.readlink(proc/'cwd')).resolve() != cwd.resolve()
                or len(argv) != 2+len(tail) or argv[1:] != [str(entrypoint), *tail]):
            raise ValueError('运行进程入口或数据目录与批准源版本不一致')


def atomic_audit(path, value, *, exclusive=False):
    raw = json.dumps(value, sort_keys=True).encode()
    temporary = path if exclusive else path.with_suffix('.next')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(raw); file.flush(); os.fsync(file.fileno())
        if not exclusive:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # Keep a prepared/partial audit as a consumed window, never retry silently.
        raise


def seal_for_interruption(root, gateway_db, instances_root, approval_file, source_release, target_release, timeout=0):
    from public_beta_maintenance import (admin, atomic_state, coverage_blockers,
                                         instance_roots, ledger_counts, read_db)
    admin(root)  # No --sandbox route for this operation.
    if not math.isfinite(timeout) or not 0 <= timeout <= 300:
        raise ValueError('等待期限必须为0至300秒')
    value, approval_hash = private_json(approval_file)
    gate = Maintenance(root)
    source = Path(source_release).resolve()
    target = Path(target_release).resolve()
    deadline = time.monotonic()+timeout
    while True:
        with gate.locked():
            state = gate.state()
            if state['phase'] != 'draining':
                raise ValueError('只允许从draining执行一次封口')
            validate_approval(value, state, time.time())
            if revision(source) != value['source_revision'] or revision(target) != value['target_revision']:
                raise ValueError('源或目标版本与授权不一致')
            roots = instance_roots(gateway_db, instances_root)
            blockers = list(coverage_blockers(gate, gateway_db))
            running_source(gate, gateway_db, source)
            counts = gate.counts_locked()
            ledgers = ledger_counts(roots)
            # Detect database corruption even when the few queried rows still parse.
            for file in [Path(gateway_db), gate.root/'activity/journal.sqlite3'] + [
                    p/'.auth/model-tasks.sqlite3' for p in roots if (p/'.auth/model-tasks.sqlite3').exists()]:
                with read_db(file) as db:
                    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                        blockers.append('unreadable_ledgers')
            with gate.db() as db:
                rows = [dict(r) for r in db.execute('SELECT id,instance,pid,start,reason FROM uncertainties')]
            actual = {r['id']: fingerprint(r) for r in rows}
            approved = {r['id']: r['fingerprint'] for r in value['records']}
            if actual != approved:
                blockers.append('historical_unknown_set_mismatch')
            blockers += [k for k, v in ledgers.items() if v]
            if counts['active']:
                blockers.append('accepted_activity')
            if counts['orphaned']:
                blockers.append('process_interrupted_requires_reconcile')
            result = {**state, **counts, **ledgers, 'blockers': sorted(set(blockers)),
                      'local_quiescent': not counts['active'] and not counts['orphaned'],
                      'historical_unknowns_retained': len(rows), 'restart_safe': False,
                      'interruption_authorized': False}
            if not blockers:
                directory = gate.root/'interruption-audit'
                directory.mkdir(mode=0o700, exist_ok=True)
                info = directory.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError('授权审计目录权限无效')
                audit_file = directory/(value['window_id']+'.json')
                next_state = {**state, 'phase': 'sealed', 'epoch': state['epoch']+1}
                audit = {'schema': 1, 'status': 'prepared', 'approval': value,
                         'approval_sha256': approval_hash, 'sealed_epoch': next_state['epoch'],
                         'restart_safe': False, 'remote_status_and_cost_unknown': True,
                         'local_quiescent': True, 'historical_unknowns_retained': 4,
                         'original_blockers': ['llm_remote_status_requires_reconcile']}
                atomic_audit(audit_file, audit, exclusive=True)
                atomic_state(gate, next_state)
                audit.update(status='committed', committed_at=time.time())
                atomic_audit(audit_file, audit)
                return {**result, **next_state, 'interruption_authorized': True,
                        'blockers': ['llm_remote_status_requires_reconcile'],
                        'window_id': value['window_id'], 'approval_sha256': approval_hash,
                        'authorization_expires_at': value['expires_at'],
                        'authorization_valid_only_while_sealed_epoch': next_state['epoch']}
            if any(b != 'accepted_activity' for b in blockers) or time.monotonic() >= deadline:
                return {**result, 'timed_out': counts['active'] > 0 and time.monotonic() >= deadline,
                        'action': 'stop_upgrade'}
        time.sleep(min(.1, max(0, deadline-time.monotonic())))
