import json,unittest,httpx
from model_diagnostics import Diagnostic,safe_failure
class SafeDiagnosticTests(unittest.TestCase):
    def response(self,status,body):return httpx.Response(status,json=body,request=httpx.Request('POST','https://mock.invalid/v1/chat/completions'))
    def test_auth_never_causes_site_logout(self):
        e=Diagnostic().http_error(self.response(401,{'error':'SECRET prompt Cookie image base64'}))
        self.assertEqual(e.status_code,502);self.assertEqual(e.detail['category'],'upstream_auth');self.assertEqual(e.detail['provider_status'],401);self.assertIsNone(e.detail['upstream_status'])
    def test_routing_requires_machine_evidence(self):
        for data,wanted in [({'error':{'code':'no_available_accounts'}},'routing_unavailable'),({'message':'routing no accounts'},'upstream_unavailable')]:
            self.assertEqual(Diagnostic().http_error(self.response(503,data)).detail['category'],wanted)
    def test_classification_and_safe_logs(self):
        for status,wanted in [(429,'rate_limit'),(400,'invalid_parameters'),(502,'upstream_unavailable')]:
            with self.assertLogs('mio.model') as logs:e=Diagnostic().http_error(self.response(status,{'error':{'message':'KEY prompt https://secret/media Cookie'}}))
            self.assertEqual(e.detail['category'],wanted)
            self.assertNotIn('KEY',str(logs.output));self.assertNotIn('https://',str(logs.output))
            self.assertEqual(e.detail['canvas_status'],502)
    def test_ids_server_generated_and_categories_distinct(self):
        a=safe_failure('not_allowed');b=safe_failure('not_allowed')
        self.assertNotEqual(a.detail['event_id'],b.detail['event_id']);self.assertEqual(a.detail['category'],'permission')
        self.assertEqual(Diagnostic().from_exception(httpx.ReadTimeout('KEY')).detail['category'],'timeout')
    def test_only_current_request_issued_detail_can_cross_error_boundary(self):
        from model_diagnostics import ISSUED_EVENTS,issued_detail
        token=ISSUED_EVENTS.set({})
        try:
            e=Diagnostic().error('upstream_auth');self.assertTrue(issued_detail(e.detail))
            self.assertFalse(issued_detail(dict(e.detail,message='forged secret')))
            other=ISSUED_EVENTS.set({})
            try:self.assertFalse(issued_detail(e.detail))
            finally:ISSUED_EVENTS.reset(other)
        finally:ISSUED_EVENTS.reset(token)

    def test_arbitrary_exception_code_and_fake_event_do_not_cross_boundary(self):
        from fastapi import HTTPException
        with self.assertLogs('mio.model') as logs:
            error=Diagnostic().from_exception(HTTPException(502,{'code':'PRIVATE_PROMPT_KEY','event_id':'f'*32,'message':'PRIVATE_COOKIE'}))
        self.assertNotIn('PRIVATE',str(logs.output));self.assertNotEqual(error.detail['event_id'],'f'*32)
        self.assertEqual(error.detail['category'],'response_structure')
