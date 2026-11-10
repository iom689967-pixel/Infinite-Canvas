"""Temporary A/B full-UI acceptance; only fake credentials and an exact loopback mock.

Test-only login passwords are Local-UI-Only-A-2026 / Local-UI-Only-B-2026.
stdin: evidence | finish. Keep the fixture alive while doing browser acceptance.
"""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from instance_auth import AuthStore
from test_instance_own_providers import OwnProviderTests
from PIL import Image

fixture=OwnProviderTests()
try:
    fixture.setUp()
    for name in ('A','B'):
        store=AuthStore(fixture.roots[name],name)
        store.set_provider_permission(name,True)
        store.change_account(name,password=f'Local-UI-Only-{name}-2026')
    picture=fixture.root/'ui-reference.png'
    Image.new('RGB',(80,100),'#af784e').save(picture)
    print(json.dumps({'A':f'http://127.0.0.1:{fixture.ports["A"]}',
                      'B':f'http://127.0.0.1:{fixture.ports["B"]}',
                      'mock':fixture.mock.origin,'upload':str(picture),'root':str(fixture.root)}),flush=True)
    for line in sys.stdin:
        if line.strip()=='finish':break
        if line.strip()=='evidence':
            print(json.dumps({'mock_requests':len(fixture.mock.calls),
                              'mock_creates':sum(k=='create' for k,_,_ in fixture.mock.calls),
                              'real_provider_requests':0}),flush=True)
finally:
    fixture.doCleanups()
