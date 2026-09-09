"""Local operator CLI. Deliberately no Web Admin or destructive user deletion."""
import argparse
import json
from instance_storage_quota import StorageQuota
from public_beta_store import BetaConfig, GatewayStore, BetaError
from public_beta_supervisor import Supervisor


def main():
    parser=argparse.ArgumentParser(description='Mio Canvas Public Beta 本机管理；不会输出密码、会话或 API Key')
    parser.add_argument('action',choices=['users','disable','enable','instance','recount-storage','stop'])
    parser.add_argument('username',nargs='?')
    args=parser.parse_args()
    try:
        store=GatewayStore(BetaConfig.from_env());supervisor=Supervisor(store)
        if args.action=='users':
            with store.db() as db:
                values=[dict(r) for r in db.execute('SELECT u.username,u.status,i.status AS instance_status,u.created_at,u.last_login_at FROM users u JOIN instances i ON u.id=i.user_id ORDER BY u.created_at')]
        else:
            if not args.username: raise BetaError('需要用户名')
            name=store.username(args.username)
            with store.db() as db: row=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
            if not row: raise BetaError('用户不存在',404)
            instance=store.instance(row[0])
            if args.action in {'enable','disable'}:
                supervisor.account_status(name,args.action=='enable');values={'ok':True,'status':args.action}
            elif args.action=='stop':
                supervisor.stop(row[0]);values={'ok':True}
            elif args.action=='recount-storage':
                # Reads only names/sizes, never workspace file contents or credentials.
                root=supervisor.root(instance)
                config=json.loads((root/'.auth/public-beta.json').read_text())
                values=StorageQuota(root,config['storage_quota'],config['max_upload']).recount()
            else:
                values={k:instance[k] for k in ('username','instance_id','status','assigned_port','data_root','pid','last_started_at')}
        print(json.dumps(values,ensure_ascii=False,indent=2));return 0
    except (BetaError,ValueError,RuntimeError,OSError):
        print('管理操作未完成；请检查用户与本机配置。');return 1


if __name__=='__main__': raise SystemExit(main())
