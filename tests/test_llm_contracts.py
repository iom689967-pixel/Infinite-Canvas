import unittest
from llm_contracts import parse_response,SSEText,ContractError
from model_budgets import for_route
class TextContracts(unittest.TestCase):
    def test_json_errors_not_success(self):
        for data,category in [([], 'response_structure'),({'error':{'message':'secret'}},'business_error'),({'choices':[{'message':{'content':''}}]},'empty_result'),({'choices':[{'message':{'refusal':'no'}}]},'refused'),({},'response_structure')]:
            with self.subTest(category=category),self.assertRaises(ContractError) as cm:parse_response(data)
            self.assertEqual(cm.exception.category,category)
    def test_preserve_text_whitespace_usage_only_numeric(self):
        r=parse_response({'choices':[{'message':{'content':' A \n'}}],'usage':{'total_tokens':3,'secret':'KEY'}})
        self.assertEqual(r.text,' A \n');self.assertEqual(r.usage,{'total_tokens':3})
    def test_stream_requires_protocol_terminal(self):
        s=SSEText();self.assertEqual(s.line('data: {"choices":[{"delta":{"content":"hi"}}]}'),'');self.assertEqual(s.line(''),'hi')
        with self.assertRaises(ContractError) as cm:s.finish()
        self.assertEqual(cm.exception.category,'incomplete_stream')
        s.line('data: [DONE]');s.line('');self.assertEqual(s.finish().text,'hi')
    def test_stream_finish_reason_and_errors(self):
        s=SSEText();s.line('data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}');s.line('');self.assertEqual(s.finish().text,'hi')
        for data in ['[]','{','{"error":{"code":"bad"}}']:
            s=SSEText();s.line('data: '+data)
            with self.assertRaises(ContractError):s.line('')
    def test_all_text_routes_share_bounded_budget(self):
        for route in ['/api/canvas-llm','/api/chat','/api/chat/stream','/api/chat/agent','/api/local-assets/caption','/api/local-assets/classify','/api/asset-library/items/classify']:
            b=for_route('POST',route);self.assertGreater(b.read,120);self.assertLess(b.read,b.total);self.assertGreater(b.gateway_total,b.total)
        self.assertLess(for_route('GET','/api/config').total,for_route('POST','/api/canvas-llm').total)
