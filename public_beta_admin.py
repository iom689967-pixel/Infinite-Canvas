"""Local operator CLI. Deliberately no Web Admin or destructive user deletion."""
import argparse
import json
from instance_storage_quota import StorageQuota
from public_beta_store import BetaConfig, GatewayStore, BetaError
from public_beta_ipc import gateway_supervisor
from public_beta_email import EmailRegistration
from public_beta_email_address import masked_email
from public_beta_mail import mailer_for


def main():
    parser=argparse.ArgumentParser(description='Mio Canvas Public Beta 本机管理；不会输出密码、会话或 API Key')
    parser.add_argument('action',choices=['users','disable','enable','instance','recount-storage','stop',
                                          'set-email','verify-email','send-verification','cleanup-pending'])
    parser.add_argument('username',nargs='?')
    parser.add_argument('email',nargs='?')
    args=parser.parse_args()
    try:
        store=GatewayStore(BetaConfig.from_env());supervisor=gateway_supervisor(store)
        emails=EmailRegistration(store,supervisor,mailer_for(store.config))
        if args.action=='users':
            with store.db() as db:
                db.execute('BEGIN')
                values=store.capacity(db)
                values['users']=[]
                for r in db.execute('SELECT u.username,u.email,u.email_status,u.status,i.status AS instance_status,u.created_at,u.last_login_at FROM users u LEFT JOIN instances i ON u.id=i.user_id ORDER BY u.created_at'):
                    user=dict(r);user['masked_email']=masked_email(user.pop('email'));values['users'].append(user)
        elif args.action=='cleanup-pending':
            import time
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE');values={'expired_pending_removed':emails.cleanup(db,time.time())}
        else:
            if not args.username: raise BetaError('需要用户名')
            name=store.username(args.username)
            with store.db() as db: row=db.execute('SELECT id,status FROM users WHERE username=?',(name,)).fetchone()
            if not row: raise BetaError('用户不存在',404)
            if args.action=='set-email':
                if not args.email: raise BetaError('需要邮箱')
                values=emails.set_email(row['id'],args.email)
            elif args.action=='verify-email':
                values=emails.admin_verify(row['id'])
            elif args.action=='send-verification':
                values={'sent':emails.send(row['id'])}
            elif args.action=='enable' and row['status']=='verified_waiting':
                values=emails.result(emails.provision(row['id']))
            elif args.action in {'enable','disable'}:
                supervisor.account_status(name,args.action=='enable');values={'ok':True,'status':args.action}
            elif args.action=='stop':
                supervisor.stop(row[0]);values={'ok':True}
            elif args.action=='recount-storage':
                # Reads only names/sizes, never workspace file contents or credentials.
                instance=store.instance(row[0])
                root=supervisor.root(instance)
                config=json.loads((root/'.auth/public-beta.json').read_text())
                values=StorageQuota(root,config['storage_quota'],config['max_upload'],config.get('min_free_disk_bytes',0)).recount()
            else:
                instance=store.instance(row[0])
                values={k:instance[k] for k in ('username','instance_id','status','assigned_port','data_root','pid','last_started_at')}
        print(json.dumps(values,ensure_ascii=False,indent=2));return 0
    except BetaError as exc:
        # BetaError messages are server-owned constants, never raw request errors.
        print(exc.message);return 1
    except (ValueError,RuntimeError,OSError):
        print('管理操作未完成；请检查用户与本机配置。');return 1


if __name__=='__main__': raise SystemExit(main())
