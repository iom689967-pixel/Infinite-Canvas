"""Private echoes must remain redacted after JSON serialization and log writes."""
import json
import threading
import unittest
from io import StringIO
from types import SimpleNamespace
from urllib.parse import quote,quote_plus
from instance_model_policy import ModelPolicy,GuardedClient
from fastapi import HTTPException
from instance_executor import EXECUTION
from instance_paths import _InstanceLogStream

class PersonalSecretRedactionTests(unittest.TestCase):
    def setUp(self):
        self.secrets={field:'fake-'+field+'-"\\+日本語' for field in ('api_key','wallet_api_key','volcengine_access_key_id','volcengine_secret_access_key')}

    def test_all_credential_types_are_redacted_in_json_literals_and_url_encodings(self):
        policy=object.__new__(ModelPolicy)
        policy.providers={'own':dict(personal=True,secret_refs={field:'fake-ref' for field in self.secrets},credential_file='fake-ref',base_url='https://mock.example')}
        policy.credential=lambda provider,field='api_key':self.secrets[field]
        payload={field:value+' '+quote(value,safe='')+' '+quote_plus(value) for field,value in self.secrets.items()}
        for ascii_only in (True,False):
            cleaned=policy.redact(json.dumps(payload,ensure_ascii=ascii_only))
            self.assertTrue(all(value=='[redacted] [redacted] [redacted]' for value in json.loads(cleaned).values()))
        guard=object.__new__(GuardedClient)
        guard.policy=policy;guard.provider=policy.providers['own'];guard.used_credential=''
        self.assertTrue(guard.validate_media_url('https://cdn.example/output.png'))
        for secret in self.secrets.values():
            for value in (quote(secret,safe=''),quote(quote(secret,safe=''),safe='')):
                with self.assertRaises(HTTPException):guard.validate_media_url('https://cdn.example/output.png?token='+value)

    def test_execution_log_never_retains_json_escaped_private_values(self):
        log,original=StringIO(),StringIO()
        stream=_InstanceLogStream(original,log,threading.Lock())
        token=EXECUTION.set(SimpleNamespace(secrets=self.secrets))
        try:stream.write(json.dumps(self.secrets,ensure_ascii=True))
        finally:EXECUTION.reset(token)
        self.assertEqual(log.getvalue(),original.getvalue())
        self.assertTrue(all(value=='[redacted]' for value in json.loads(log.getvalue()).values()))
