import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def test_replaces_file_with_new_inode(self):
        path = self.dir / "state.json"
        main.atomic_write_json(path, {"a": 1})
        first = os.stat(path).st_ino
        main.atomic_write_json(path, {"a": 2}, indent=2)
        self.assertNotEqual(first, os.stat(path).st_ino)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 2})

    def test_serialization_failure_keeps_previous_content(self):
        path = self.dir / "state.json"
        main.atomic_write_json(path, {"a": 1})
        with self.assertRaises(TypeError):
            main.atomic_write_json(path, {"big": "x" * 100000, "bad": object()})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["state.json"])

    def test_write_failure_removes_temp_file(self):
        path = self.dir / "state.json"
        main.atomic_write_json(path, {"a": 1})
        with patch.object(main.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                main.atomic_write_json(path, {"a": 2})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["state.json"])

    def test_preserves_permissions_and_unicode(self):
        path = self.dir / "state.json"
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o640)
        main.atomic_write_json(path, {"title": "画布"})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
        self.assertIn("画布", path.read_text(encoding="utf-8"))

    def test_text_newline_passthrough(self):
        path = self.dir / "caption.txt"
        main.atomic_write_text(path, "a\r\nb", newline="")
        self.assertEqual(path.read_bytes(), b"a\r\nb")

    def test_env_update_is_atomic_and_merges(self):
        env_file = self.dir / "API" / ".env"
        env_file.parent.mkdir()
        env_file.write_text("# keep\nA=1\n", encoding="utf-8")
        with patch.object(main, "API_ENV_FILE", str(env_file)), patch.dict(os.environ, {}, clear=False):
            main.update_env_values({"A": "2", "B": "x y"})
        self.assertEqual(env_file.read_text(encoding="utf-8"), '# keep\nA=2\nB="x y"\n')


if __name__ == "__main__":
    unittest.main()
