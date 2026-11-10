"""Real-browser fixture for Public Beta. Only loopback mock, no paid calls.

stdin commands: status, disable-alice, finish. Password/mock key stay in private files.
"""
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import httpx
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_instance_models import ModelMock
from test_instance_isolation import free_port

program=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='mio-beta-browser-') as temporary:
    root=Path(temporary).resolve();port=free_port()
    mock=ThreadingHTTPServer(('127.0.0.1',0),ModelMock)
    key=secrets.token_urlsafe(32);password=secrets.token_urlsafe(24)
    mock.calls,mock.jobs,mock.media=[],{},{}
    mock.credentials={hashlib.sha256(key.encode()).hexdigest():'alice'}
    image=BytesIO();Image.new('RGB',(48,64),'#477abd').save(image,'PNG');mock.image=image.getvalue()
    mock.origin=f'http://127.0.0.1:{mock.server_port}';mock.finish_waiting=True
    thread=threading.Thread(target=mock.serve_forever,daemon=True);thread.start()
    for name,value in [('password.txt',password),('mock-key.txt',key)]:
        p=root/name;p.write_text(value);p.chmod(0o600)
    (root/'reference.png').write_bytes(mock.image)
    env={'PATH':os.environ.get('PATH',os.defpath),'PUBLIC_BETA_ROOT':str(root/'gateway'),
         'PUBLIC_BETA_INSTANCES_ROOT':str(root/'instances'),'GATEWAY_PORT':str(port),
         'PUBLIC_BETA_MOCK_UPSTREAMS':f'127.0.0.1:{mock.server_port}','PYTHONDONTWRITEBYTECODE':'1'}
    record={'root':str(root),'origin':f'http://127.0.0.1:{port}','mock_origin':mock.origin}
    marker=Path('/private/tmp/mio-beta-browser-current.json');marker.write_text(json.dumps(record));marker.chmod(0o600)
    with open(root/'gateway.log','w') as log:
        proc=subprocess.Popen([sys.executable,str(program/'public_beta.py')],cwd=program,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    if httpx.get(record['origin']+'/healthz',trust_env=False).status_code==200:break
                except httpx.HTTPError:time.sleep(.1)
            print(json.dumps({'gateway':record['origin'],'mode':'mock'}),flush=True)
            for line in sys.stdin:
                command=line.strip()
                if command=='finish':break
                if command=='disable-alice':
                    result=subprocess.run([sys.executable,str(program/'public_beta_admin.py'),'disable','alice'],cwd=program,env=env,capture_output=True)
                    print(json.dumps({'disabled':result.returncode==0}),flush=True)
                if command=='status':
                    with sqlite3.connect(root/'gateway/gateway.sqlite3') as db:
                        users=db.execute('SELECT username,status FROM users').fetchall()
                        instances=db.execute('SELECT data_root,pid FROM instances').fetchall()
                    print(json.dumps({'users':users,'instance_count':len(instances),'unique_pids':len({p for _,p in instances if p}),
                        'mock_create_count':sum(k=='create' for k,_,_ in mock.calls),'real_model_calls':0,
                        'gateway_log_secret_free':not any(v in (root/'gateway.log').read_text() for v in (key,password))}),flush=True)
        finally:
            proc.terminate()
            try:proc.wait(timeout=20)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            mock.shutdown();mock.server_close();thread.join(timeout=3)
