import subprocess
import unittest
from pathlib import Path

class SmartSaveCoordinatorTests(unittest.TestCase):
    def test_real_coordinator_contracts(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node','--test',str(root/'tests/test_smart_save_coordinator.cjs')],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_asset_is_allowed_and_precedes_page(self):
        from instance_access import WORKBENCH_STATIC
        root=Path(__file__).resolve().parents[1]
        asset='/static/js/smart-save-coordinator.js'
        self.assertIn(asset,WORKBENCH_STATIC)
        html=(root/'static/smart-canvas.html').read_text()
        self.assertLess(html.index(asset),html.index('/static/js/smart-canvas.js'))
        self.assertNotIn(asset,(root/'static/canvas.html').read_text())
