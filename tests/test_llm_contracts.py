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
    def test_independent_url_goldens_and_model_exact(self):
        from llm_contracts import build_request
        for protocol,base,want in [('openai','https://mock.invalid','https://mock.invalid/v1/chat/completions'),('openai','https://mock.invalid/v1','https://mock.invalid/v1/chat/completions'),('openai','https://mock.invalid/prefix/v2','https://mock.invalid/prefix/v2/chat/completions'),('volcengine','https://mock.invalid','https://mock.invalid/api/v3/chat/completions'),('volcengine','https://mock.invalid/api/v3','https://mock.invalid/api/v3/chat/completions'),('runninghub','https://www.runninghub.cn','https://llm.runninghub.ai/v1/chat/completions'),('runninghub','https://relay.invalid/v1','https://relay.invalid/v1/chat/completions')]:
            url,body=build_request(protocol,base,'exact / model',[{'role':'user','content':' hi '}])
            self.assertEqual(url,want);self.assertEqual(body['model'],'exact / model');self.assertEqual(body['messages'][0]['content'],' hi ')
    def test_gemini_media_not_duplicated_and_explicit_parameters(self):
        from llm_contracts import build_request,compose_messages
        images=['data:image/png;base64,YQ==','data:image/webp;base64,Yg=='];videos=['data:video/mp4;base64,Yw==']
        messages=compose_messages(' sys ',[{'role':'user','content':'before'},{'role':'assistant','content':'reply'}],' current ',images,videos)
        url,body=build_request('gemini','https://mock.invalid/relay/v1beta','exact-new',messages,max_output_tokens=777,temperature=.2)
        self.assertEqual(url,'https://mock.invalid/relay/v1beta/models/exact-new:generateContent')
        self.assertEqual(body['systemInstruction'],{'parts':[{'text':' sys '}]});self.assertEqual([x['role'] for x in body['contents']],['user','model','user'])
        self.assertEqual(body['contents'][-1]['parts'],[{'text':' current '},{'inlineData':{'mimeType':'image/png','data':'YQ=='}},{'inlineData':{'mimeType':'image/webp','data':'Yg=='}},{'inlineData':{'mimeType':'video/mp4','data':'Yw=='}}])
        self.assertEqual(body['generationConfig'],{'maxOutputTokens':777,'temperature':.2})
    def test_runninghub_credentials_only_exact_adapter_target(self):
        from llm_contracts import approved_runninghub_text_target as allowed
        target='https://llm.runninghub.ai/v1/chat/completions'
        self.assertTrue(allowed('https://www.runninghub.cn',target))
        for base,url in [('https://runninghub.cn.evil.invalid',target),('http://www.runninghub.cn',target),('https://custom.invalid',target),('https://www.runninghub.cn',target+'?token=secret'),('https://www.runninghub.cn','https://llm.runninghub.ai/other')]:self.assertFalse(allowed(base,url))
