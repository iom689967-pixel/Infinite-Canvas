"""Administrator CLI. No remote endpoint, credentials, shell or model invocation."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from instance_maintenance import Maintenance, MaintenanceUnavailable, DETAIL, initialize, process_identity
from maintenance_routes import caddy_allowed


def admin(root, sandbox=False):
    path = Path(root)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink():
        raise ValueError('需要绝对、非链接的维护目录')
    if sandbox:
        if not path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise PermissionError('sandbox 只允许系统临时目录')
    elif os.geteuid() != 0:
        raise PermissionError('维护操作仅限服务器 root 管理员')


def atomic_state(gate, state):
    temporary = gate.root/'state.next'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        info = (gate.root/'state.json').stat()
        if os.geteuid() == 0:
            os.fchown(fd, info.st_uid, info.st_gid)
        os.write(fd, json.dumps(state).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, gate.root/'state.json')
    descriptor = os.open(gate.root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def set_phase(gate, phase):
    if phase not in {'open', 'draining'}:
        raise ValueError('sealed 必须经排空检查，不可直接设置')
    with gate.locked():
        state = gate.state()
        if state['phase'] != phase:
            state.update(phase=phase, epoch=state['epoch']+1)
            atomic_state(gate, state)
        return state


def read_db(path):
    if path.is_symlink():
        raise ValueError('禁止链接数据库')
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)


def ledger_counts(roots):
    counts = {'pending_tasks': 0, 'uncertain_submissions': 0, 'pending_results': 0,
              'asset_processing': 0, 'unreadable_ledgers': 0}
    for root in roots:
        root = Path(root)
        try:
            if root.is_symlink() or not (root/'.instance.json').is_file():
                raise ValueError()
            if (root/'.auth').is_symlink() or (root/'data').is_symlink():
                raise ValueError()
            path = root/'.auth/model-tasks.sqlite3'
            if path.exists():
                with read_db(path) as db:
                    rows = db.execute('SELECT data FROM jobs').fetchall()
                for row in rows:
                    job = json.loads(row[0])
                    if not isinstance(job, dict):
                        raise ValueError()
                    counts['uncertain_submissions'] += bool(job.get('submission_uncertain') or job.get('recovery') == 'manual-reconcile')
                    counts['pending_tasks'] += bool(job.get('outstanding') or job.get('status') in {
                        'queued', 'preparing', 'submitting', 'submitted', 'generating', 'waiting', 'queuing'})
                    counts['pending_results'] += job.get('status') in {'result_pending_download', 'result_recovery_required'}
            library = root/'data/asset_library.json'
            if library.exists():
                if library.is_symlink():
                    raise ValueError()
                lib = json.loads(library.read_text())
                for collection in lib.get('libraries', [lib]):
                    for category in collection.get('categories', []):
                        for item in category.get('items', []):
                            for reg in item.get('registrations', {}).values():
                                if isinstance(reg, dict):
                                    counts['asset_processing'] += reg.get('status') in {'Processing', 'SubmissionUnknown'}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
            counts['unreadable_ledgers'] += 1
    return counts


def instance_roots(gateway_db, instances_root):
    """Read-only registry; never instantiate GatewayStore or reconcile here."""
    parent = Path(instances_root).resolve()
    with read_db(Path(gateway_db)) as db:
        rows = db.execute('SELECT instance_id,data_root FROM instances').fetchall()
    roots = []
    for identity, raw in rows:
        root = Path(raw)
        if root != parent/identity or root.is_symlink():
            raise ValueError('实例根目录或归属不匹配')
        marker = json.loads((root/'.instance.json').read_text())
        if marker != {'instance_id': identity, 'data_root': str(root)}:
            raise ValueError('实例根目录或归属不匹配')
        roots.append(root)
    return roots


def coverage_blockers(gate, gateway_db):
    """Missing native coverage automatically blocks first upgrades. No opt-out.

    An empty new activity journal never certifies an old running process. The
    controlled offline rehearsal has provenance, not a production bypass.
    """
    with read_db(Path(gateway_db)) as db:
        instances = db.execute("SELECT instance_id,pid,process_started,status FROM instances WHERE status IN ('running','starting')").fetchall()
    with gate.db() as db:
        participants = {r['instance']: (r['pid'], r['start']) for r in db.execute('SELECT * FROM processes')}
    missing = sum(participants.get(iid) != (pid, started) or process_identity(pid) != started
                  for iid, pid, started, status in instances)
    gateway = participants.get('gateway')
    if not gateway or process_identity(gateway[0]) != gateway[1]:
        missing += 1
    return (('legacy_or_unobserved_process',) if missing else ()) + (
        ('instance_starting',) if any(status=='starting' for _,_,_,status in instances) else ())


def inspect(gate, roots, *, seal=False, legacy_blockers=()):
    with gate.locked():
        state = gate.state()
        counts = gate.counts_locked()
        with gate.db() as db:
            counts['unknown_llm_responses'] = db.execute('SELECT COUNT(*) FROM uncertainties').fetchone()[0]
        ledgers = ledger_counts(roots)
        blockers = [key for key, value in ledgers.items() if value]
        if counts['active']:
            blockers.append('accepted_activity')
        if counts['orphaned']:
            blockers.append('process_interrupted_requires_reconcile')
        if counts['unknown_llm_responses']:
            blockers.append('llm_remote_status_requires_reconcile')
        if state['phase'] == 'open':
            blockers.append('gate_open')
        blockers.extend(legacy_blockers)
        if seal and not blockers and state['phase'] == 'draining':
            state.update(phase='sealed', epoch=state['epoch']+1)
            atomic_state(gate, state)
        return {**state, **counts, **ledgers, 'blockers': sorted(set(blockers)),
                'restart_safe': state['phase'] == 'sealed' and not blockers}


def wait_for_drain(gate, roots, timeout, *, legacy_blockers=()):
    deadline = time.monotonic()+timeout
    while True:
        status = inspect(gate, roots, seal=True, legacy_blockers=legacy_blockers)
        if status['restart_safe']:
            return status
        if time.monotonic() >= deadline:
            return {**status, 'timed_out': True, 'action': 'stop_upgrade'}
        time.sleep(min(.1, max(0, deadline-time.monotonic())))


def reconcile(gate, roots, identity, evidence_file, *, uncertainty=False):
    """Explicit administrator acknowledgement, never task replay or ledger edit.

    An interrupted task with an unresolved original ledger cannot be acknowledged
    away. LLMs without a query contract require actual supplier-side review.
    """
    import re
    if not re.fullmatch('[0-9a-f]{32}', identity):
        raise ValueError('核对 ID 无效')
    path = Path(evidence_file)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('核对证据文件必须为管理员私有普通文件')
    raw = path.read_bytes()
    proof = json.loads(raw)
    if (set(proof) != {'schema','id','original_remote_work_ended','original_ledgers_reviewed'}
        or proof != {'schema':1,'id':identity,'original_remote_work_ended':True,'original_ledgers_reviewed':True}):
        raise ValueError('必须先核对供应商原请求和原 ledger；不接受本地断开作为取消证据')
    with gate.locked(), gate.db() as db:
        if gate.state()['phase']=='open' or any(ledger_counts(roots).values()):
            raise ValueError('先进入维护并解决原任务 ledger 阻塞')
        table = 'uncertainties' if uncertainty else 'activities'
        row = db.execute('SELECT * FROM '+table+' WHERE id=?',(identity,)).fetchone()
        if not row:
            raise ValueError('核对记录不存在')
        if not uncertainty and process_identity(row['pid'])==row['start']:
            raise ValueError('禁止清除仍存活进程的活动')
        if uncertainty and db.execute('SELECT 1 FROM activities WHERE instance=?',(row['instance'],)).fetchone():
            raise ValueError('相关本地活动尚未结束')
        db.execute('INSERT INTO reconciliations VALUES (?,?,?,?)',
                   (identity,gate.state()['epoch'],hashlib.sha256(raw).hexdigest(),time.time()))
        db.execute('DELETE FROM '+table+' WHERE id=?',(identity,))
        return {'reconciled':True,'task_ledger_modified':False,'generation_replayed':False}


def caddy_barrier(config, host, *, sealed=False):
    """Patch only the single top-level Mio host route. Reject ambiguous topology.

    Keep a private JSON preimage for rollback. Caller validates and reloads with
    Caddy's native CLI. No Sub2API route or global option is modified here.
    """
    result = copy.deepcopy(config)
    sites = []
    for server in result.get('apps', {}).get('http', {}).get('servers', {}).values():
        for route in server.get('routes', []):
            match = route.get('match', [])
            if any(host in matcher.get('host', []) for matcher in match):
                if match != [{'host': [host]}]:
                    raise ValueError('Mio 路由必须为独立的精确单域名路由')
                sites.append(route)
    if len(sites) != 1:
        raise ValueError('无法唯一识别 Mio 专属路由；禁止修改共享路由')
    site = sites[0]
    if len(site.get('handle', [])) != 1 or site['handle'][0].get('handler') != 'subroute':
        raise ValueError('Mio 路由必须包含独立 subroute')
    routes = site['handle'][0].setdefault('routes', [])
    routes[:] = [route for route in routes if route.get('@id') != 'mio-maintenance-barrier']
    routes.insert(0, {'@id': 'mio-maintenance-barrier', 'match': [{'not': caddy_allowed(sealed)}],
        'handle': [{'handler': 'static_response', 'status_code': 503,
                    'headers': {'Content-Type': ['application/json; charset=utf-8'],
                                'Cache-Control': ['no-store'], 'X-Mio-Maintenance': ['1'], 'Retry-After': ['60']},
                    'body': json.dumps({'detail': DETAIL}, ensure_ascii=False)}], 'terminal': True})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mio 管理员维护与排空工具；不会启动、停止或重试生成')
    parser.add_argument('--root', required=True)
    parser.add_argument('--sandbox', action='store_true')
    commands = parser.add_subparsers(dest='operation', required=True)
    init = commands.add_parser('init'); init.add_argument('--worker-uid', type=int); init.add_argument('--worker-gid', type=int)
    for name in ('open', 'draining'):
        commands.add_parser(name)
    for name in ('status', 'drain'):
        child = commands.add_parser(name)
        child.add_argument('--gateway-db', required=True); child.add_argument('--instances-root', required=True)
        child.add_argument('--legacy-unobserved', action='store_true')
        if name == 'drain':
            child.add_argument('--timeout', type=float, default=60)
    child = commands.add_parser('caddy-barrier')
    child.add_argument('--input', required=True); child.add_argument('--output', required=True)
    child.add_argument('--host', required=True); child.add_argument('--sealed', action='store_true')
    child=commands.add_parser('reconcile')
    child.add_argument('--id',required=True);child.add_argument('--evidence-file',required=True)
    child.add_argument('--uncertainty',action='store_true')
    child.add_argument('--gateway-db',required=True);child.add_argument('--instances-root',required=True)
    args = parser.parse_args(argv)
    try:
        admin(args.root, args.sandbox)
        if args.operation == 'init':
            initialize(args.root, worker_uid=args.worker_uid, worker_gid=args.worker_gid)
            value = {'phase': 'draining', 'initialized': True}
        elif args.operation == 'caddy-barrier':
            value = caddy_barrier(json.loads(Path(args.input).read_text()), args.host, sealed=args.sealed)
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(value, file)
            value = {'prepared': True, 'phase': 'sealed' if args.sealed else 'draining'}
        else:
            gate = Maintenance(args.root)
            if args.operation == 'reconcile':
                roots=instance_roots(args.gateway_db,args.instances_root)
                value=reconcile(gate,roots,args.id,args.evidence_file,uncertainty=args.uncertainty)
            elif args.operation in {'open', 'draining'}:
                value = set_phase(gate, args.operation)
            else:
                roots = instance_roots(args.gateway_db, args.instances_root)
                blockers = coverage_blockers(gate, args.gateway_db)
                if args.legacy_unobserved:
                    blockers += ('legacy_unobserved_activity',)
                value = (wait_for_drain(gate, roots, args.timeout, legacy_blockers=blockers)
                         if args.operation == 'drain' else inspect(gate, roots, legacy_blockers=blockers))
        print(json.dumps(value, ensure_ascii=False))
        return 3 if value.get('timed_out') else 0
    except (PermissionError, OSError, ValueError, sqlite3.Error, MaintenanceUnavailable):
        print(json.dumps({'error': '维护状态或排空证据不可用，停止升级', 'restart_safe': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
