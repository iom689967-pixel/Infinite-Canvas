"""Immutable audit evidence is classified, not redefined by the candidate."""
import json,pathlib,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class BaselineEvidenceTests(unittest.TestCase):
    def test_nine_original_failures_have_explicit_ownership(self):
        rows=json.loads((ROOT/'docs/stability/before-assertions.json').read_text())
        self.assertEqual(len(rows),9)
        self.assertEqual(sum(r['disposition']=='fix_public' for r in rows),3)
        self.assertEqual(sum(r['disposition']=='owner_only_record_no_change' for r in rows),6)
        self.assertTrue(all(not r['pass'] for r in rows))
    def test_feature_matrix_references_real_ledger_entries(self):
        import re
        refs=set(re.findall(r'D\d\d',(ROOT/'docs/stability/feature-matrix.csv').read_text()))
        self.assertLessEqual(refs,{'D%02d'%i for i in range(1,10)})
