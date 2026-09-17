"""Real Starlette ASGI disconnect over unchanged business code, no TCP egress."""
import json,os,pathlib,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class RealASGIDisconnectTests(unittest.TestCase):
    def test_actual_response_disconnect_closes_nested_producer(self):
        with tempfile.TemporaryDirectory(prefix='mio-stability-asgi-') as tmp:
            root=pathlib.Path(tmp);(root/'snapshots').mkdir();(root/'evidence').mkdir();(root/'snapshots/public').symlink_to(ROOT,target_is_directory=True)
            result=subprocess.run([sys.executable,str(ROOT/'tools/stability/differential_stream_disconnect.py'),'public'],env=dict(os.environ,STABILITY_REPLAY_ROOT=tmp),capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr)
            d=json.loads((root/'evidence/stream-disconnect-public.json').read_text())
            self.assertTrue(d['disconnect_delivered']);self.assertEqual(d['transport_calls'],1);self.assertEqual(d['llm_active_after'],0);self.assertFalse(d['has_done']);self.assertIsNone(d['error'])
            self.assertNotIn('different Context',result.stderr);self.assertNotIn('ignored GeneratorExit',result.stderr)
