"""Independent expected contracts; six historical owner defects remain explicit."""
import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('directory',type=pathlib.Path);p.add_argument('--kie-directory',type=pathlib.Path);args=p.parse_args();E=args.directory/'evidence'
checks=[]
for side in ['owner','public']:
 for r in json.loads((E/f'llm-{side}.json').read_text()):
  if r['fixture'] in ['json_success','gzip_success']:
   checks.append(dict(side=side,case=r['case']+'/'+r['fixture'],criterion='fixture text preserved',passed=r['outcome'].get('result',{}).get('text','').strip()=='OFFLINE RESULT'))
  if r['fixture'] in ['business_error_200','empty_text','json_list']:
   checks.append(dict(side=side,case=r['fixture'],criterion='invalid result never successful',passed=r['outcome']['status']!=200))
 for r in json.loads((E/f'stream-{side}.json').read_text()):
  if r['case'] in ['sse_no_done','sse_business_error']:
   checks.append(dict(side=side,case=r['case'],criterion='no terminal success on invalid stream',passed=not any(e.get('type')=='done' for e in r['events'])))
 for r in json.loads((E/f'workflows-{side}.json').read_text()):
  if r['case'] in ['image_generate','image_edit','image_n2','video_async']:
   reads=[x for x in r['requests'] if '/media/' in x['url']]
   checks.append(dict(side=side,case=r['case'],criterion='CDN read without provider credentials',passed=bool(reads) and all(not x['auth_present'] for x in reads)))
  if r['case']=='download_failure':checks.append(dict(side=side,case=r['case'],criterion='download failure never saved success',passed=r['outcome']['status']!=200))
expected_owner={'business_error_200','empty_text','json_list','sse_no_done','sse_business_error','download_failure'}
assert {r['case'] for r in checks if r['side']=='owner' and not r['passed']}==expected_owner,checks
assert all(r['passed'] for r in checks if r['side']=='public'),checks
K=(args.kie_directory or args.directory)/'evidence';rows={r['case']:r for r in json.loads((K/'kie-public.json').read_text())}
assert rows['model25']['outcome']['status']==200
assert rows['prompt_over20k']['outcome']['status']==400 and not rows['prompt_over20k']['requests']
assert not rows['duplicate']['requests'] and rows['duplicate']['outcome']['status']==200
assert rows['download_failure']['outcome']['recovery_status']=='succeeded'
assert not any(r['method']=='POST' and r['url'].endswith('createTask') for r in rows['download_failure']['outcome']['recovery_requests'])
summary={'checks':checks,'passed':sum(r['passed'] for r in checks),'owner_recorded_defects':6,'public_failures':0,'kie_25_prompt_gate_nonce_recovery':True}
(E/'verified-contracts.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='checks'}))
