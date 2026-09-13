"""Fixed argv worker entry point. Supervisor supplies a sanitized environment."""
import sys


def main():
    action=sys.argv[1] if len(sys.argv)==2 else ''
    if action=='initialize':
        import json
        from instance_paths import PATHS
        from instance_auth import AuthStore
        AuthStore(PATHS.data_root,PATHS.instance_id,initialize=True)
        for rel in ('assets/input','assets/output','output','workflows/custom','runtime'):
            (PATHS.data_root/rel).mkdir(parents=True,exist_ok=True,mode=0o700)
        # New public users start without copied content, including bundled prompts.
        # Exclusive creation must never replace an existing user's prompt library.
        with (PATHS.data_root/'data/prompt_libraries.json').open('x',encoding='utf-8') as file:
            json.dump({'active_library_id':'system','libraries':[
                {'id':'system','name':'系统提示词库','type':'prompt','items':[],'categories':[]}
            ]},file,ensure_ascii=False)
    elif action=='serve':
        import main as application
        import uvicorn
        uvicorn.run(application.app,host=application.INSTANCE_HOST,port=application.INSTANCE_PORT,
                    log_level='warning',access_log=False,proxy_headers=False,ws='websockets-sansio')
    else:
        raise SystemExit('Unknown worker operation')


if __name__=='__main__': main()
