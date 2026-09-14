"""Temporary real Gateway/Supervisor for browser acceptance. No Provider or model.

JSON stdin commands: status, stop (synthetic username), restart-gateway, finish.
Never points at a saved user/production root.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from public_beta_store import BetaConfig,GatewayStore
from public_beta_ipc import SupervisorClient
from test_instance_isolation import free_port

program=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='mio-workspace-entry-') as temporary:
    root=Path(temporary).resolve();port=free_port();origin=f'http://127.0.0.1:{port}'
    env={'PATH':os.environ.get('PATH',os.defpath),'PYTHONDONTWRITEBYTECODE':'1',
         'PUBLIC_BETA_ROOT':str(root/'gateway'),'PUBLIC_BETA_INSTANCES_ROOT':str(root/'instances'),
         'PUBLIC_BETA_SUPERVISOR_SOCKET':str(root/'gateway/ctl.sock'),
         'PUBLIC_BETA_BACKUP_ROOT':str(root/'backups'),'GATEWAY_PORT':str(port),
         'INSTANCE_PORT_START':'45000','INSTANCE_PORT_END':'45031'}
    config=BetaConfig(root/'gateway',root/'instances',port=port,port_start=45000,port_end=45031,
                      backup_root=root/'backups',supervisor_socket=env['PUBLIC_BETA_SUPERVISOR_SOCKET'])
    control=SupervisorClient(GatewayStore(config))
    def healthy():
        for _ in range(100):
            try:
                if httpx.get(origin+'/healthz',trust_env=False,timeout=1).status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.1)
        raise RuntimeError('Temporary gateway unavailable')
    with (root/'services.log').open('w') as log:
        daemon=subprocess.Popen([sys.executable,str(program/'public_beta_daemon.py')],cwd=program,env=env,stdout=log,stderr=log)
        subprocess.run([sys.executable,str(program/'public_beta_daemon.py'),'ready'],cwd=program,env=env,check=True,stdout=log,stderr=log)
        gateway=subprocess.Popen([sys.executable,str(program/'public_beta.py')],cwd=program,env=env,stdout=log,stderr=log)
        try:
            healthy();print(json.dumps({'origin':origin}),flush=True)
            for line in sys.stdin:
                request=json.loads(line);op=request['operation']
                if op=='finish':break
                if op=='restart-gateway':
                    gateway.terminate();gateway.wait(timeout=30)
                    gateway=subprocess.Popen([sys.executable,str(program/'public_beta.py')],cwd=program,env=env,stdout=log,stderr=log)
                    healthy()
                if op=='stop':
                    assert request['username'] in {'entry-alice','entry-bob'}
                    with control.store.db() as db:uid=db.execute('SELECT id FROM users WHERE username=?',(request['username'],)).fetchone()[0]
                    control.stop(uid)
                with control.store.db() as db:
                    users=[dict(r) for r in db.execute('SELECT u.username,i.pid,i.status FROM users u JOIN instances i ON i.user_id=u.id')]
                    starts=db.execute("SELECT COUNT(*) FROM security_events WHERE event='started'").fetchone()[0]
                print(json.dumps({'operation':op,'users':users,'started_events':starts,'supervisor_pid':daemon.pid,'gateway_pid':gateway.pid}),flush=True)
        finally:
            gateway.terminate();gateway.wait(timeout=30)
            try:
                with control.store.db() as db:owned=[r[0] for r in db.execute('SELECT user_id FROM instances WHERE pid IS NOT NULL')]
                for uid in owned:control.stop(uid)
            finally:
                daemon.terminate();daemon.wait(timeout=20)
