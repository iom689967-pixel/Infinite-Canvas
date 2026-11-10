"""Production boundary and backup tests use temporary roots and loopback only."""
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from public_beta import create_app
from public_beta_backup import snapshot
from public_beta_store import BetaConfig
from instance_storage_quota import StorageQuota
from test_instance_isolation import free_port
from test_public_beta import PASSWORD


class ProductionGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mio-beta-production-test-')
        self.apps=[]

    async def asyncTearDown(self):
        for app,client in self.apps:
            await client.aclose()
            await asyncio.to_thread(app.state.supervisor.close)
        self.temp.cleanup()

    def config(self, **values):
        root=Path(self.temp.name)
        defaults={'port':free_port(),'external_origin':'https://mio-canvas.eu.cc',
                  'trusted_proxies':('127.0.0.1',),'min_free_disk':0,
                  'register_limit':100,'login_limit':100}
        return BetaConfig(root/('gateway-'+secrets.token_hex(3)),root/('instances-'+secrets.token_hex(3)),
                          **(defaults|values))

    def client(self, config, peer='127.0.0.1'):
        app=create_app(config)
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(peer,42000)),
            base_url=config.origin,headers={'Host':'mio-canvas.eu.cc','Origin':config.origin,
                                            'X-Forwarded-Proto':'https','X-Forwarded-For':'203.0.113.7'})
        self.apps.append((app,client))
        return app,client

    async def test_closed_registration_and_trusted_proxy_boundary(self):
        config=self.config(registration_mode='closed');_,client=self.client(config)
        self.assertNotIn('href="/register"',(await client.get('/')).text)
        self.assertIn('暂未开放注册',(await client.get('/register')).text)
        payload={'username':'alice','password':PASSWORD,'confirmation':PASSWORD}
        self.assertEqual((await client.post('/api/beta/register',json=payload)).status_code,403)
        _,outside=self.client(self.config(registration_mode='closed'),peer='203.0.113.9')
        self.assertEqual((await outside.get('/')).status_code,403)
        client.headers['X-Forwarded-For']='198.51.100.1, 203.0.113.7'
        self.assertEqual((await client.get('/')).status_code,403)

    async def test_invite_registration_secure_cookies_and_full_sso(self):
        invite=secrets.token_urlsafe(24)
        config=self.config(registration_mode='invite',invite_hash=hashlib.sha256(invite.encode()).hexdigest())
        _,client=self.client(config)
        payload={'username':'alice','password':PASSWORD,'confirmation':PASSWORD,'invite_code':'wrong'}
        self.assertEqual((await client.post('/api/beta/register',json=payload)).status_code,403)
        payload['invite_code']=invite
        registered=await client.post('/api/beta/register',json=payload)
        self.assertEqual(registered.status_code,200,registered.text)
        self.assertIn('Secure',registered.headers['set-cookie'])
        csrf=(await client.get('/api/beta/me')).json()['csrf']
        entered=await client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
        self.assertEqual(entered.status_code,200,entered.text)
        self.assertTrue(all('Secure' in value for value in entered.headers.get_list('set-cookie')))
        self.assertEqual((await client.get('/api/auth/me')).status_code,200)

    def test_external_origin_requires_https_and_loopback_proxy(self):
        root=Path(self.temp.name)
        values_list=(
            {'external_origin':'http://mio-canvas.eu.cc','trusted_proxies':('127.0.0.1',)},
            {'external_origin':'https://mio-canvas.eu.cc'},
            {'external_origin':'https://mio-canvas.eu.cc','trusted_proxies':('198.51.100.2',)},
        )
        for values in values_list:
            with self.assertRaises(ValueError):
                BetaConfig(root/secrets.token_hex(3),root/secrets.token_hex(3),**values)


class DeploymentResourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mio-beta-resource-test-')
        root=Path(self.temp.name)
        self.config=BetaConfig(root/'gateway',root/'instances',port=free_port(),min_free_disk=0,
                               max_running_instances=1,register_limit=100,login_limit=100)
        self.app=create_app(self.config)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url=self.config.origin,headers={'Origin':self.config.origin})

    async def asyncTearDown(self):
        await self.client.aclose()
        await asyncio.to_thread(self.app.state.supervisor.close)
        self.temp.cleanup()

    async def register(self,name):
        return await self.client.post('/api/beta/register',
            json={'username':name,'password':PASSWORD,'confirmation':PASSWORD})

    async def enter(self):
        csrf=(await self.client.get('/api/beta/me')).json()['csrf']
        return await self.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})

    async def test_server_running_limit_does_not_affect_existing_worker(self):
        self.assertEqual((await self.register('alice')).status_code,200)
        self.assertEqual((await self.enter()).status_code,200)
        alice=self.app.state.store.principal(self.client.cookies.get('mio_beta_session'))
        alice_pid=self.app.state.store.instance(alice['id'])['pid']
        self.assertEqual((await self.register('bob')).status_code,200)
        self.assertEqual((await self.enter()).status_code,503)
        self.assertEqual(self.app.state.store.instance(alice['id'])['pid'],alice_pid)

    async def test_registration_pauses_below_disk_floor(self):
        self.config.min_free_disk=1024
        with patch('public_beta_supervisor.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
            response=await self.register('alice')
        self.assertEqual(response.status_code,503)
        with self.app.state.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)

    def test_content_write_pauses_below_disk_floor_but_zero_growth_allowed(self):
        root=Path(self.temp.name)/'quota'
        (root/'.auth').mkdir(parents=True);(root/'.runtime/tmp').mkdir(parents=True)
        (root/'empty.json').write_text('{      }')
        quota=StorageQuota(root,1024,512,min_free_disk=1000)
        with patch('instance_storage_quota.shutil.disk_usage',return_value=SimpleNamespace(free=900)):
            with self.assertRaises(Exception): quota.write(root/'blocked.bin',b'x')
            quota.replace_json(root/'empty.json',{})
        self.assertTrue((root/'empty.json').is_file())


class BackupTests(unittest.TestCase):
    def test_incremental_snapshot_uses_sqlite_backup_and_retention(self):
        with tempfile.TemporaryDirectory(prefix='mio-beta-backup-test-') as temporary:
            root=Path(temporary)
            config=BetaConfig(root/'gateway',root/'instances',backup_root=root/'backups',
                              port=free_port(),min_free_disk=0,backup_retention=2)
            app=create_app(config);store=app.state.store
            iid=secrets.token_hex(16);uid=secrets.token_hex(16);instance=config.instances_root/iid
            instance.mkdir();(instance/'data').mkdir()
            proof=instance/'data/proof.txt';proof.write_text('stable')
            with sqlite3.connect(instance/'data/content.sqlite3') as db:
                db.execute('CREATE TABLE proof(value TEXT)')
                db.execute("INSERT INTO proof VALUES ('ok')")
            with store.db() as db:
                db.execute("INSERT INTO users VALUES (?,?,?,'active',0,NULL)",(uid,'alice','not-used'))
                db.execute("INSERT INTO instances VALUES (?,?,?,?,?,'stopped',0,NULL,NULL,NULL)",
                           (iid,uid,iid,str(instance),free_port()))
            first=snapshot(config);second=snapshot(config)
            first_root=config.backup_root/first['snapshot'];second_root=config.backup_root/second['snapshot']
            self.assertEqual((first_root/'instances'/iid/'data/proof.txt').stat().st_ino,
                             (second_root/'instances'/iid/'data/proof.txt').stat().st_ino)
            snapshot(config)
            saved=[path for path in config.backup_root.iterdir()
                   if path.is_dir() and not path.name.startswith('.')]
            self.assertEqual(len(saved),2)
            current=next(path for path in saved if (path/'instances'/iid/'data/content.sqlite3').exists())
            with sqlite3.connect(current/'instances'/iid/'data/content.sqlite3') as db:
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
                self.assertEqual(db.execute('SELECT value FROM proof').fetchone()[0],'ok')
            manifest=json.loads((current/'manifest.json').read_text())
            self.assertEqual(manifest['instance_count'],1)
            self.assertNotIn('alice',json.dumps(manifest))
