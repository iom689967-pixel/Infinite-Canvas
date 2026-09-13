"""Single authority for instance processes; private UDS, fixed operations, no shell."""
from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
import time

from public_beta_store import BetaConfig, GatewayStore, BetaError
from public_beta_supervisor import Supervisor


def dispatch(supervisor, data):
    if not isinstance(data,dict):raise BetaError('控制请求格式不正确')
    operation=data.get('operation')
    fields={'list_running':set(),'start':{'instance_id'},'stop':{'instance_id'},
            'status':{'instance_id'},'account_status':{'instance_id','enabled'},
            'provision':{'username','password_hash'}}
    if not isinstance(operation,str) or operation not in fields or set(data)!={'operation'}|fields[operation]:
        raise BetaError('控制操作或字段未开放')
    if operation=='list_running':return {'instances':supervisor.reconcile()}
    if operation=='provision':
        if not isinstance(data['username'],str) or not isinstance(data['password_hash'],str) or not re.fullmatch(r'scrypt\$131072\$8\$1\$[0-9a-f]{64}\$[0-9a-f]{128}',data['password_hash']):
            raise BetaError('注册预留字段不正确')
        return {'user_id':supervisor.provision(data['username'],data['password_hash'])}
    iid=data['instance_id']
    if not isinstance(iid,str) or not re.fullmatch(r'[0-9a-f]{32}',iid):raise BetaError('实例不存在',404)
    with supervisor.store.db() as db:
        row=db.execute('SELECT user_id FROM instances WHERE instance_id=?',(iid,)).fetchone()
    if not row:raise BetaError('实例不存在',404)
    uid=row[0]
    if operation=='status':supervisor.reconcile()
    if operation=='start':supervisor.start(uid)
    elif operation=='stop':supervisor.stop(uid)
    elif operation=='account_status':
        if type(data['enabled']) is not bool:raise BetaError('账户状态字段不正确')
        supervisor.account_status(supervisor.store.instance(uid)['username'],data['enabled'])
    instance=supervisor.store.instance(uid)
    return {key:instance[key] for key in ('instance_id','status','pid','assigned_port')}


def peer_allowed(connection):
    if hasattr(socket,'SO_PEERCRED'):
        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        return uid in {0,os.getuid()}
    if hasattr(connection,'getpeereid'):
        uid,_=connection.getpeereid();return uid in {0,os.getuid()}
    # BSD/macOS local development: filesystem 0700 directory + 0600 socket.
    return os.uname().sysname=='Darwin'


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def setup(self):
        self.request.settimeout(5)
        super().setup()
    def do_POST(self):
        try:
            if not peer_allowed(self.connection):raise BetaError('控制请求未授权',403)
            if self.path!='/control' or self.headers.get('Content-Type')!='application/json':raise BetaError('控制接口未开放',404)
            if self.headers.get('Transfer-Encoding'):raise BetaError('控制请求不支持流式传输')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise BetaError('控制请求过大',413)
            payload=self.rfile.read(length)
            if len(payload)!=length:raise BetaError('控制请求不完整')
            result=dispatch(self.server.supervisor,json.loads(payload));status=200
        except BetaError as exc:
            result={'detail':exc.message};status=exc.status
            if status==503:self.server.supervisor.store.event('supervisor_reconcile_required')
        except Exception:result={'detail':'控制服务操作未完成'};status=503
        body=json.dumps(result).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.end_headers()
        try:self.wfile.write(body)
        except OSError:pass


class ControlServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=False
    block_on_close=True
    request_queue_size=16
    def __init__(self,path,supervisor):
        self.supervisor=supervisor
        self.slots=threading.BoundedSemaphore(8)
        super().__init__(path,Handler)
    def process_request(self,request,address):
        self.slots.acquire()
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


@contextmanager
def daemon_server(config):
    store=GatewayStore(config);supervisor=Supervisor(store)
    # Lifetime lock prevents a second daemon or split process ownership.
    with open(config.root/'supervisor-authority.lock','a+b') as authority:
        fcntl.flock(authority,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            supervisor.recover_incomplete()
            supervisor.reconcile()
        except BetaError:
            store.event('supervisor_reconcile_required')
            raise
        path=Path(config.supervisor_socket)
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        parent=path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)&0o077:
            raise ValueError('Supervisor socket directory must be private')
        if path.exists() or path.is_symlink():
            info=path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():raise ValueError('Unsafe existing socket')
            path.unlink()  # Authority lock held; only remove our stale endpoint.
        server=ControlServer(str(path),supervisor)
        os.chmod(path,0o600)
        try:yield server
        finally:
            server.server_close()
            path.unlink(missing_ok=True)
            # Explicitly retain instances. Next daemon validates and adopts them.


def main():
    config=BetaConfig.from_env()
    if sys.argv[1:]==['ready']:
        from public_beta_ipc import SupervisorClient
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if Path(config.supervisor_socket).is_socket():
                try:
                    SupervisorClient(GatewayStore(config)).call('list_running')
                    return
                except BetaError:pass
            time.sleep(.1)
        raise SystemExit('Supervisor readiness check failed')
    if sys.argv[1:]:raise SystemExit('Unsupported supervisor operation')
    with daemon_server(config) as server:
        stop=threading.Event()
        def shutdown(*unused):
            stop.set()
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
        def maintenance():
            while not stop.wait(5):
                try:
                    server.supervisor.reconcile()
                    server.supervisor.stop_idle()
                except Exception:server.supervisor.store.event('supervisor_reconcile_required')
        threading.Thread(target=maintenance,daemon=True).start()
        server.serve_forever(poll_interval=.1)


if __name__=='__main__':main()
