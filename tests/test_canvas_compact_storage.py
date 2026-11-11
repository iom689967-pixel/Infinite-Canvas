"""画布文件改为紧凑 JSON 写入：数据结构不变，旧的缩进格式文件照常读取，新文件任何 json.load 都能读。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from instance_storage_quota import StorageQuota

SAMPLE = {
    "id": "c0ffee",
    "kind": "smart",
    "title": "中文标题 🎨",
    "updated_at": 1,
    "nodes": [{"id": "n1", "x": 1.5, "text": "多行\n文本", "images": [{"url": "/assets/output/a.png"}]}],
    "connections": [{"from": "n1", "to": "n1"}],
    "logs": [],
}


class CanvasCompactStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        patcher = patch.object(main, "CANVAS_DIR", str(self.dir))
        patcher.start()
        self.addCleanup(patcher.stop)
        storage = patch.object(main, "INSTANCE_STORAGE", None)
        storage.start()
        self.addCleanup(storage.stop)

    def test_canvas_json_text_is_compact_and_round_trips(self):
        text = main.canvas_json_text(SAMPLE)
        self.assertNotIn("\n", text.replace("\\n", ""))
        self.assertNotIn(": ", text)
        self.assertNotIn(", ", text)
        self.assertIn("中文标题 🎨", text)  # 不转义为 \\uXXXX
        self.assertEqual(json.loads(text), SAMPLE)

    def test_save_canvas_writes_compact_file_readable_by_plain_json_load(self):
        canvas = json.loads(json.dumps(SAMPLE))
        main.save_canvas(canvas)
        raw = (self.dir / "c0ffee.json").read_text(encoding="utf-8")
        self.assertFalse(raw.startswith("{\n"))
        with open(self.dir / "c0ffee.json", encoding="utf-8") as handle:
            loaded = json.load(handle)  # 旧版代码读取画布的方式
        self.assertEqual(loaded["nodes"], SAMPLE["nodes"])
        self.assertEqual(main.load_canvas("c0ffee")["title"], SAMPLE["title"])

    def test_existing_indented_file_still_loads_and_is_rewritten_compact(self):
        (self.dir / "c0ffee.json").write_text(json.dumps(SAMPLE, ensure_ascii=False, indent=2), encoding="utf-8")
        canvas = main.load_canvas("c0ffee")
        self.assertEqual(canvas["nodes"], SAMPLE["nodes"])
        main.save_canvas(canvas)
        self.assertFalse((self.dir / "c0ffee.json").read_text(encoding="utf-8").startswith("{\n"))

    def test_meta_update_and_project_move_write_compact(self):
        main.save_canvas(json.loads(json.dumps({**SAMPLE, "project": "p-gone"})))
        with main.CANVAS_LOCK:
            main._update_canvas_meta_locked("c0ffee", main.CanvasMetaUpdate(title="改名"))
        raw = (self.dir / "c0ffee.json").read_text(encoding="utf-8")
        self.assertFalse(raw.startswith("{\n"))
        self.assertEqual(json.loads(raw)["title"], "改名")

    def test_write_canvas_json_is_atomic(self):
        target = self.dir / "c0ffee.json"
        main.write_canvas_json(str(target), SAMPLE)
        before = target.read_text(encoding="utf-8")
        with patch.object(main.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                main.write_canvas_json(str(target), {**SAMPLE, "title": "changed"})
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(os.listdir(self.dir)), ["c0ffee.json"])


class InstanceStorageCompactTests(unittest.TestCase):
    def test_replace_json_separators_and_default_indent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".runtime/tmp").mkdir(parents=True)
            quota = StorageQuota.__new__(StorageQuota)
            quota.root = root
            quota.max_upload = 50 * 1024 ** 2
            from contextlib import nullcontext
            quota.reserve = lambda _amount: nullcontext()
            quota.changed = lambda *_args, **_kwargs: None
            canvas_file, history_file = root / "c.json", root / "h.json"
            quota.replace_json(canvas_file, SAMPLE, separators=main.CANVAS_JSON_SEPARATORS)
            quota.replace_json(history_file, [SAMPLE])
            self.assertFalse(canvas_file.read_text(encoding="utf-8").startswith("{\n"))
            self.assertEqual(json.loads(canvas_file.read_text(encoding="utf-8")), SAMPLE)
            self.assertTrue(history_file.read_text(encoding="utf-8").startswith("[\n  {"))  # 历史记录保持原格式


if __name__ == "__main__":
    unittest.main()
