"""Actual root CLI -> unmodified f598 processes -> backup/roll/reopen/rollback.

Run as root only on an isolated Linux CI runner. All credentials and assets are
synthetic. Caddy is a temporary, fixed TLS proxy; no reload/barrier is used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rehearse_legacy_upgrade import Rehearsal, eventually, ROOT, configuration
from instance_maintenance import Maintenance
from maintenance_interruption import fingerprint

OLD='f598c15fb46d81968d3f80c2a66e646f2d2ab685'


class InterruptionRehearsal(Rehearsal):
    def git(self,*args):
        return subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),*args],text=True).strip()

    def cli(self,*args,expected=0):
        result=subprocess.run([sys.executable,str(ROOT/'public_beta_maintenance.py'),'--root',str(self.control),*args],
            env=self.env,capture_output=True,text=True,timeout=25)
        assert result.returncode==expected,(result.returncode,result.stdout,result.stderr[-1500:])
        return json.loads(result.stdout)

    def unknowns(self):
        with self.gate.db() as db:
            return [dict(r) for r in db.execute('SELECT id,instance,pid,start,reason FROM uncertainties ORDER BY id')]

    def authorization(self,source,target,name):
        now=time.time()
        value=dict(schema=1,window_id=uuid.uuid4().hex,epoch=self.gate.state()['epoch'],
            source_revision=source,target_revision=target,created_at=now-1,expires_at=now+600,
            remote_status_and_cost_unknown=True,records=self.approved)
        return self.write_config(value,name)

    def seal_args(self,approval,source,target,timeout='0'):
        return ['seal-for-interruption','--gateway-db',str(self.root/'gateway/gateway.sqlite3'),
            '--instances-root',str(self.root/'instances'),'--approval-file',str(approval),
            '--source-release',str(source),'--target-release',str(target),'--timeout',timeout]

    def ipc(self,program,operation,iid):
        return self.run(program,'from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;'
            'import json;s=SupervisorClient(GatewayStore(BetaConfig.from_env()));'
            'print(json.dumps(s.call('+repr(operation)+',instance_id='+repr(iid)+')))')

    def tls_proxy(self):
        cert,key=self.root/'fixture.pem',self.root/'fixture.key'
        cfg=self.root/'tls.cnf';cfg.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n')
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-config',str(cfg),
            '-out',str(cert),'-keyout',str(key)],check=True,capture_output=True,timeout=15)
        config={'admin':{'listen':f'127.0.0.1:{self.admin_port}'},'apps':{
            'tls':{'certificates':{'load_files':[{'certificate':str(cert),'key':str(key)}]}},
            'http':{'servers':{'mio':{'automatic_https':{'disable':True},'tls_connection_policies':[{}],
                'listen':[f'127.0.0.1:{self.entry_port}'],'routes':[{'handle':[{'handler':'reverse_proxy',
                    'upstreams':[{'dial':f'127.0.0.1:{self.gateway_port}'}]}]}]}}}}}
        path=self.write_config(config,'caddy-fixed.json')
        self.start('caddy',[self.caddy,'run','--config',str(path)],self.root)
        eventually(lambda:self.caddy_ready())

    def consistent_backup(self):
        destination=self.root/'consistent-backup';destination.mkdir(mode=0o700)
        count=0
        for folder in ('gateway','instances','control'):
            base=self.root/folder
            files=[p for p in base.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))]
            for src in files:
                dst=destination/folder/src.relative_to(base);dst.parent.mkdir(parents=True,exist_ok=True)
                if src.suffix in {'.sqlite3','.sqlite','.db'}:
                    with sqlite3.connect(src.resolve().as_uri()+'?mode=ro',uri=True) as db,sqlite3.connect(dst) as backup:
                        db.backup(backup);assert backup.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
                    count+=1
                else:
                    before=hashlib.sha256(src.read_bytes()).hexdigest();shutil.copy2(src,dst)
                    assert before==hashlib.sha256(src.read_bytes()).hexdigest()==hashlib.sha256(dst.read_bytes()).hexdigest()
        self.record('consistent_backup',online_sqlite_backups=count,ordinary_files_stable=True,remote_unknowns_retained=4)

    def roll(self,program,revision):
        before=self.accounts();self.stop('gateway');self.stop('supervisor')
        self.start('supervisor',[sys.executable,str(program/'public_beta_daemon.py')],program)
        eventually(lambda:(self.root/'ipc/supervisor.sock').exists())
        self.start('gateway',[sys.executable,str(program/'public_beta.py')],program);self.readiness()
        assert [r['pid'] for r in self.accounts()]==[r['pid'] for r in before]
        updated=[]
        for row in before:
            self.ipc(program,'stop',row['instance_id']);self.ipc(program,'start',row['instance_id'])
            after=next(r for r in self.accounts() if r['instance_id']==row['instance_id'])
            assert after['pid']!=row['pid'] and after['data_root']==row['data_root']
            self.program_loaded(after['pid'],program,'public_beta_worker.py')
            assert self.gate.state()['phase']=='sealed';assert self.unknowns()==self.original_unknowns
            assert len([r for r in self.accounts() if r['status']=='running'])<=2
            updated.append(dict(before=row['pid'],after=after['pid'],start=after['process_started'],data_root_preserved=True))
        self.record('serial_roll',revision=revision,instances=updated,unknown_fingerprints_preserved=True)

    def verify(self,client,canvas_id,task_id,media_url,provider_file,provider_bytes):
        self.cli('draining');self.enter(client,'alice')
        assert client.get('/api/canvases/'+canvas_id).json()['canvas']['nodes'][0]['id']=='original-node'
        assert sum(r.get('task_id')==task_id for r in client.get('/api/history').json())==1
        assert client.get(media_url).status_code==200
        settings=client.get('/api/instance/provider-settings').json()['providers'][0]
        config=next(p for p in client.get('/api/config').json()['api_providers'] if p['id']=='same-api')
        assert settings['image_models']==config['image_models']==['gpt-image-2','gpt-image-2.5-flare','gpt-image-2.5-sunburst']
        assert provider_file.read_bytes()==provider_bytes
        assert self.unknowns()==self.original_unknowns
        self.cli('open');assert self.gate.state()['phase']=='open'

    def execute(self):
        assert os.geteuid()==0 and sys.platform=='linux','Requires real Linux root, no sandbox substitute'
        target=self.git('rev-parse','HEAD');assert not self.git('status','--porcelain')
        old=self.root/'f598-source';self.git('worktree','add','--detach',str(old),OLD);self.old=old
        self.control=self.root/'control';self.env['MIO_MAINTENANCE_ROOT']=str(self.control)
        self.cli('init','--worker-uid','0','--worker-gid','0');self.cli('open');self.gate=Maintenance(self.control)
        self.run(old,"from public_beta_store import BetaConfig,GatewayStore;from public_beta_supervisor import Supervisor;"
            "s=Supervisor(GatewayStore(BetaConfig.from_env()));"
            "s.register('alice','Mock-rehearsal-password-42','Mock-rehearsal-password-42','fixture');"
            "s.register('bob','Mock-rehearsal-password-42','Mock-rehearsal-password-42','fixture')")
        for row in self.accounts():
            private=Path(row['data_root'])/'.auth';(private/'credentials').mkdir(exist_ok=True,mode=0o700)
            for name in ('gemini.key','kie.key'):
                (private/'credentials'/name).write_text('fake-rehearsal-only');(private/'credentials'/name).chmod(0o600)
            (private/'model-access.json').write_text(json.dumps(configuration(self.mock.server_port)))
        self.start('supervisor',[sys.executable,str(old/'public_beta_daemon.py')],old)
        eventually(lambda:(self.root/'ipc/supervisor.sock').exists())
        self.start('gateway',[sys.executable,str(old/'public_beta.py')],old);self.readiness();self.tls_proxy()
        client=httpx.Client(base_url=self.origin,headers={'Origin':self.origin},trust_env=False,verify=False,timeout=20)
        self.clients=[client];self.enter(client,'alice')
        response=client.put('/api/instance/provider-settings',json=[dict(id='same-api',name='Private',protocol='openai',
            base_url=self.mock.origin,api_key='fake-rehearsal-only',image_models=['gpt-image-2','gpt-image-2.5-flare','gpt-image-2.5-sunburst'])])
        assert response.status_code==200,response.text
        canvas=client.post('/api/canvases',json={'title':'Preserved synthetic canvas'}).json()['canvas'];cid=canvas['id']
        canvas['nodes']=[dict(id='original-node',type='image',x=0,y=0,prompt='saved input',generationHistory=[{'id':'old-generation'}])]
        assert client.put('/api/canvases/'+cid,json=canvas).status_code==200
        self.mock.finish_waiting=True
        response=client.post('/api/canvas-image-tasks',json=dict(provider_id='atelier-images',model='gpt-image-2',prompt='fixture',
            resolution='1K',aspect_ratio='1:1',size='1024x1024',n=1,canvas_id=cid,node_id='original-node',generation_id='old-generation'))
        assert response.status_code==200,response.text;tid=response.json()['task_id']
        def completed():
            job=client.get('/api/canvas-image-tasks/'+tid).json()
            return job if job['status']=='succeeded' and not job['local_wait_active'] else None
        job=eventually(completed)
        for row in self.accounts():self.ipc(old,'start',row['instance_id'])
        before=self.accounts();pf=Path(before[0]['data_root'])/'.auth/model-access.json';pb=pf.read_bytes()
        # Fixture unknown receipts are created before preparing the approval, by
        # the actual old journal API. No production record is synthesized.
        self.run(old,'from instance_maintenance import Maintenance;g=Maintenance('+repr(str(self.control))+');'
            +';'.join('g.uncertain_llm('+repr(before[0]['instance_id'])+')' for _ in range(4)))
        self.original_unknowns=self.unknowns();self.approved=[dict(id=r['id'],fingerprint=fingerprint(r)) for r in self.original_unknowns]
        self.record('old_f598_native',source_revision=OLD,root_cli=True,source_unmodified=True,
            gateway_pid=self.procs['gateway'].pid,workers=[r['pid'] for r in before])
        self.cli('draining');approval=self.authorization(OLD,target,'approval-forward.json')
        args=self.seal_args(approval,old,ROOT,'3')
        # Actual CLI termination while blocked on the admission lock. It cannot
        # print success, mutate state, or discard original unknowns.
        with self.gate.locked():
            proc=subprocess.Popen([sys.executable,str(ROOT/'public_beta_maintenance.py'),'--root',str(self.control),*args],env=self.env,stdout=subprocess.PIPE)
            time.sleep(.15);proc.kill();out=proc.communicate(timeout=3)[0];assert not out
        assert self.gate.state()['phase']=='draining';assert self.unknowns()==self.original_unknowns
        entered,release=threading.Event(),threading.Event();payload=json.dumps(canvas).encode()
        def body():
            yield payload[:12];entered.set();assert release.wait(10);yield payload[12:]
        with ThreadPoolExecutor(max_workers=2) as pool:
            saving=pool.submit(client.put,'/api/canvases/'+cid,content=body(),headers={'Content-Type':'application/json'})
            assert entered.wait(3);eventually(lambda:self.gate.status()['active']>0)
            refused=self.cli(*self.seal_args(approval,old,ROOT),expected=3);assert not refused['interruption_authorized']
            sealing=pool.submit(self.cli,*args);time.sleep(.15);assert not sealing.done();release.set()
            assert saving.result(timeout=5).status_code==200;result=sealing.result(timeout=10)
        assert result['interruption_authorized'] and not result['restart_safe'];assert result['historical_unknowns_retained']==4
        assert [r['pid'] for r in self.accounts()]==[r['pid'] for r in before]
        status=self.cli('status','--gateway-db',str(self.root/'gateway/gateway.sqlite3'),'--instances-root',str(self.root/'instances'))
        assert not status['restart_safe'] and 'llm_remote_status_requires_reconcile' in status['blockers']
        for method,path in [('GET','/api/config'),('PUT','/api/canvases/'+cid),('POST','/api/canvas-image-tasks/'+tid+'/refresh'),('GET',job['result']['images'][0])]:
            assert client.request(method,path,json={}).status_code==503
        assert client.get('/healthz').status_code==200
        self.record('sealed_old_without_restart',**result,default_status_restart_safe=False,concurrent_save_completed=True,
            cli_abnormal_exit_failed_closed=True,expected_503=4,health_200=True,caddy_reloads=0)
        self.consistent_backup();self.roll(ROOT,target)
        self.verify(client,cid,tid,job['result']['images'][0],pf,pb)
        self.cli(*args,expected=2) # consumed/open/epoch-changed approval never reused
        self.record('new_reopened',maintenance='open',models=3,history_count=1,media_readable=True,
            provider_bytes_preserved=True,unknown_fingerprints_preserved=True)
        # Separate, bounded rollback window with inverse source/target binding.
        self.cli('draining');rollback=self.authorization(target,OLD,'approval-rollback.json')
        self.cli(*self.seal_args(rollback,ROOT,old));self.roll(old,OLD)
        self.verify(client,cid,tid,job['result']['images'][0],pf,pb)
        assert self.unknowns()==self.original_unknowns
        assert sum(k=='create' for k,_,_ in self.mock.calls)==1
        assert not subprocess.check_output(['git','-c','safe.directory='+str(old),'-C',str(old),'status','--porcelain'],text=True).strip()
        self.record('rollback_reopened',maintenance='open',database_restore=False,unknown_fingerprints_preserved=True,
            original_mock_generations=1,generation_replay=0,real_models=0,real_uploads=0,real_mail=0)
        return self.evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--caddy',required=True);parser.add_argument('--evidence',required=True);args=parser.parse_args()
    if os.geteuid()!=0 or sys.platform!='linux':raise SystemExit('Requires isolated Linux root')
    with tempfile.TemporaryDirectory(prefix='mio-f598-interruption-') as temporary:
        run=InterruptionRehearsal(args.caddy,Path(temporary).resolve())
        try:
            evidence=run.execute();Path(args.evidence).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        except Exception:
            Path(args.evidence).write_text(json.dumps([*run.evidence,{'stage':'failed','restart_safe':False,'action':'stop_upgrade'}],indent=2))
            for name in ('gateway','supervisor'):
                path=run.root/(name+'.log')
                if path.exists():print(name,path.read_text()[-2500:],file=sys.stderr)
            raise
        finally:
            run.close()
            if hasattr(run,'old'):run.git('worktree','remove',str(run.old))
