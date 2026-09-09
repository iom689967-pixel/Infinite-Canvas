"""Browser fixture. stdin: ready | evidence | finish. All upstream traffic is mock."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_instance_result_recovery import RecoveryTests

fixture=RecoveryTests()
try:
    fixture.setUp();fixture.mock.result_mode='dns'
    ref=fixture.upload()
    canvas=fixture.ok('A','POST','/api/canvases',json={'title':'Kie 原任务恢复验收','kind':'smart'})['canvas']
    fixture.ok('A','PUT','/api/canvases/'+canvas['id'],json={'nodes':[{'id':'original-node','type':'smart-image','x':100,'y':100,'title':'原参考图','images':[{'url':ref,'name':'mock-reference.png','kind':'image','width':48,'height':64}]}]})
    for n in ['A','B']:
        p=fixture.root/(n+'-login.txt');p.write_text(fixture.passwords[n]);p.chmod(0o600)
    state={'root':str(fixture.root),'A':fixture.ports['A'],'B':fixture.ports['B'],'canvas_id':canvas['id']}
    p=Path('/private/tmp/mio-recovery-browser-current.json');p.write_text(json.dumps(state));p.chmod(0o600)
    print(json.dumps({'A_url':f'http://127.0.0.1:{fixture.ports["A"]}/','B_url':f'http://127.0.0.1:{fixture.ports["B"]}/'}),flush=True)
    for line in sys.stdin:
        if line.strip()=='finish':break
        if line.strip()=='ready':fixture.mock.result_mode='';print('Mock result DNS repaired',flush=True)
        if line.strip()=='evidence':
            canvas=fixture.ok('A','GET','/api/canvases/'+canvas['id'])['canvas'];history=fixture.ok('A','GET','/api/history')
            print(json.dumps({'mock_create_count':sum(k=='create' for k,_,_ in fixture.mock.calls),
                'mock_query_count':sum(k=='query' for k,_,_ in fixture.mock.calls),'canvas_node_count':len(canvas['nodes']),
                'history_count':len(history),'attempt_statuses':[[g.get('status') for g in n.get('generationHistory',[])] for n in canvas['nodes']],
                'real_calls':0}),flush=True)
finally:fixture.doCleanups()
