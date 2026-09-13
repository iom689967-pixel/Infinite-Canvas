"""Local fixed-schema control protocol. Never accepts executable paths or commands."""
import json
import os
from pathlib import Path
import stat

import httpx

from public_beta_store import BetaError
from public_beta_supervisor import Supervisor


class SupervisorClient:
    registration_input=Supervisor.registration_input
    ticket=Supervisor.ticket
    root=Supervisor.root

    def __init__(self, store):
        self.store,self.config=store,store.config
        self.program=Path(__file__).resolve().parent

    def call(self, operation, **values):
        path=Path(self.config.supervisor_socket)
        try:
            info=path.lstat()
            expected_uid=self.config.root.stat().st_uid
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=expected_uid or stat.S_IMODE(info.st_mode)&0o077:
                raise OSError()
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(path),retries=0),trust_env=False,
                              timeout=httpx.Timeout(45,connect=2)) as client:
                response=client.post('http://supervisor/control',json={'operation':operation,**values})
                if len(response.content)>65536:raise ValueError()
                result=response.json()
        except (OSError,ValueError,httpx.HTTPError):
            raise BetaError('工作区管理服务暂不可用，请稍后再试',503) from None
        if response.status_code!=200:
            raise BetaError(result.get('detail','工作区管理操作未完成'),response.status_code)
        return result

    def recover_incomplete(self):
        # The daemon reconciles before binding IPC; a Gateway must never own recovery.
        return self.call('list_running')

    def register(self,username,password,confirmation,peer):
        name,encoded=self.registration_input(username,password,confirmation,peer)
        result=self.call('provision',username=name,password_hash=encoded)
        return self.store.session(result['user_id'])

    def start(self, uid):
        instance=self.store.instance(uid)
        self.call('start',instance_id=instance['instance_id'])
        return self.store.instance(uid)

    def stop(self, uid):
        self.call('stop',instance_id=self.store.instance(uid)['instance_id'])

    def account_status(self,username,enabled):
        name=self.store.username(username)
        with self.store.db() as db:row=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if not row:raise BetaError('用户不存在',404)
        self.call('account_status',instance_id=self.store.instance(row[0])['instance_id'],enabled=enabled)

    def touch(self,iid):
        # Durable timestamps survive both Gateway and daemon restart. No process authority here.
        Supervisor.touch(self,iid)

    def close(self):
        pass  # Gateway shutdown explicitly has no stop-all operation.


def gateway_supervisor(store):
    # Empty socket is explicit in-process development/test configuration only.
    # from_env() always selects IPC, and connection failures never fall back to Popen.
    return SupervisorClient(store) if store.config.supervisor_socket else Supervisor(store)
