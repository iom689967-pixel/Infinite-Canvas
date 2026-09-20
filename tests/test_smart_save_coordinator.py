import subprocess
import unittest
from pathlib import Path

class SmartSaveCoordinatorTests(unittest.TestCase):
    def test_page_save_ui_contracts(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node','--test',str(root/'tests/test_smart_save_ui.cjs')],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

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

    def test_smart_draft_entry_is_smart_only_and_cache_busted(self):
        root=Path(__file__).resolve().parents[1]
        smart=(root/'static/smart-canvas.html').read_text()
        ordinary=(root/'static/canvas.html').read_text()
        self.assertIn('id="smartDraftToggle"',smart)
        self.assertIn('hidden aria-expanded="false"',smart)
        self.assertIn('smart-draft-toggle.1',smart)
        self.assertNotIn('smartDraftToggle',ordinary)
