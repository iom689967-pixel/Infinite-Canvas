import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))


def blob_tree(*paths):
    return {
        "tree": [
            {"type": "blob", "mode": "100644", "path": path}
            for path in paths
        ]
    }


def tracked_tree():
    raw = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=ROOT)
    entries = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode = metadata.split()[0].decode("ascii")
        entries.append({
            "type": "blob",
            "mode": mode,
            "path": encoded_path.decode("utf-8"),
        })
    if not any(item["path"] == "release-manifest.json" for item in entries):
        entries.append({"type": "blob", "mode": "100644", "path": "release-manifest.json"})
    return {"tree": entries}


class ReleaseManifestUpdateTests(unittest.TestCase):
    def test_01_manifest_parses(self):
        parsed = main.validate_release_manifest(MANIFEST)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertTrue(parsed["include"])

    def test_02_manifest_version_must_match_version_file(self):
        manifest = main.validate_release_manifest(MANIFEST)
        self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        with tempfile.TemporaryDirectory() as staging:
            Path(staging, "VERSION").write_text("2026.09.09\n", encoding="utf-8")
            with self.assertRaisesRegex(main.ReleaseManifestError, "不一致"):
                main.validate_staged_update(staging, ["VERSION"], [], manifest)

    def test_03_include_expands_to_the_tracked_release_set(self):
        _, _, files, _ = main.release_update_file_list(MANIFEST, tracked_tree())
        self.assertIn("release-manifest.json", files)
        self.assertTrue(all(main.update_allowed_file(path) for path in files))
        self.assertFalse(any(path.startswith("tests/") for path in files))

    def test_04_providers_can_be_updated(self):
        manifest = {"schema_version": 1, "version": "2026.09.08", "include": ["VERSION", "providers/"], "protected": ["data/"]}
        _, _, files, _ = main.release_update_file_list(manifest, blob_tree("VERSION", "providers/kie/client.py"))
        self.assertIn("providers/kie/client.py", files)

    def test_05_static_can_be_updated(self):
        manifest = {"schema_version": 1, "version": "2026.09.08", "include": ["VERSION", "static/"], "protected": ["data/"]}
        _, static_files, _, _ = main.release_update_file_list(manifest, blob_tree("VERSION", "static/index.html"))
        self.assertEqual(static_files, ["static/index.html"])

    def test_06_main_can_be_updated(self):
        manifest = {"schema_version": 1, "version": "2026.09.08", "include": ["VERSION", "main.py"], "protected": ["data/"]}
        root_files, _, _, _ = main.release_update_file_list(manifest, blob_tree("VERSION", "main.py"))
        self.assertIn("main.py", root_files)

    def test_07_all_manifest_protected_paths_are_rejected(self):
        samples = [item + "sample" if item.endswith("/") else item for item in MANIFEST["protected"]]
        self.assertTrue(all(not main.update_allowed_file(path) for path in samples))

    def test_08_api_env_is_always_rejected(self):
        self.assertFalse(main.update_allowed_file("API/.env"))
        self.assertFalse(main.update_allowed_file("api/.env"))

    def test_09_canvas_data_is_always_rejected(self):
        self.assertFalse(main.update_allowed_file("data/canvases/private.json"))

    def test_10_assets_are_always_rejected(self):
        self.assertFalse(main.update_allowed_file("assets/private.png"))

    def test_11_history_is_always_rejected(self):
        self.assertFalse(main.update_allowed_file("history.json"))

    def test_12_parent_path_traversal_is_rejected(self):
        for path in ("../main.py", "static/../main.py", "providers/../../data/private.json"):
            with self.assertRaises(main.ReleaseManifestError):
                main.normalize_release_path(path)

    def test_13_absolute_paths_are_rejected(self):
        for path in ("/tmp/main.py", "C:/temp/main.py", r"C:\temp\main.py"):
            with self.assertRaises(main.ReleaseManifestError):
                main.normalize_release_path(path)

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink unsupported")
    def test_14_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(base, "providers"))
            with patch.object(main, "BASE_DIR", base):
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    main.safe_update_target("providers/escape.py")

    def test_15_missing_manifest_fails_safely(self):
        with patch.object(main, "github_bytes", side_effect=FileNotFoundError("404")):
            with self.assertRaisesRegex(main.ReleaseManifestError, "发行清单不可用"):
                main.fetch_release_manifest()

    def test_16_corrupt_manifest_fails_safely(self):
        with self.assertRaisesRegex(main.ReleaseManifestError, "JSON 损坏"):
            main.parse_release_manifest_bytes(b"{not-json")

    def test_17_unsupported_schema_fails_safely(self):
        payload = dict(MANIFEST, schema_version=999)
        with self.assertRaisesRegex(main.ReleaseManifestError, "schema_version"):
            main.validate_release_manifest(payload)

    def test_18_protected_wins_on_manifest_conflict(self):
        payload = {"schema_version": 1, "version": "2026.09.08", "include": ["VERSION", "data/"], "protected": ["data/"]}
        with self.assertRaisesRegex(main.ReleaseManifestError, "冲突"):
            main.validate_release_manifest(payload)

    def test_19_requirements_change_only_returns_a_notice_signal(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as staging:
            Path(base, "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            Path(staging, "requirements.txt").write_text("fastapi\nhttpx\n", encoding="utf-8")
            with patch.object(main, "BASE_DIR", base):
                self.assertTrue(main.requirements_changed_in_staging(staging, ["requirements.txt"]))
        self.assertNotIn("pip install", inspect.getsource(main.update_from_github))
        self.assertIn("本版本包含依赖变化，需要运行依赖安装/修复。", (ROOT / "static" / "index.html").read_text(encoding="utf-8"))

    def test_20_manifest_replaces_the_old_program_allowlist(self):
        self.assertFalse(hasattr(main, "UPDATE_FILE_ALLOWLIST"))
        self.assertFalse(hasattr(main, "UPDATE_PREFIX_ALLOWLIST"))
        manifest = {"schema_version": 1, "version": "2026.09.08", "include": ["VERSION", "new_runtime_module.py"], "protected": ["data/"]}
        _, _, files, _ = main.release_update_file_list(manifest, blob_tree("VERSION", "new_runtime_module.py"))
        self.assertIn("new_runtime_module.py", files)

    def test_21_backup_copies_only_declared_static_files(self):
        with tempfile.TemporaryDirectory() as base:
            static_dir = Path(base, "static")
            data_dir = Path(base, "data")
            static_dir.mkdir()
            data_dir.mkdir()
            Path(static_dir, "index.html").write_text("official", encoding="utf-8")
            Path(static_dir, "local-backup.txt").write_text("private", encoding="utf-8")
            backup_dir = Path(data_dir, "update_backups", "test")
            with patch.object(main, "BASE_DIR", base), patch.object(main, "STATIC_DIR", str(static_dir)), patch.object(main, "DATA_DIR", str(data_dir)):
                manifest = main.create_update_backup(
                    str(backup_dir), [], ["static/index.html"], kind="test"
                )
            self.assertTrue(Path(backup_dir, "static", "index.html").is_file())
            self.assertFalse(Path(backup_dir, "static", "local-backup.txt").exists())
            self.assertEqual(set(manifest["static_files"]), {"static/index.html"})

    def test_22_updater_replaces_files_without_deleting_static_directory(self):
        source = inspect.getsource(main.update_from_github)
        self.assertIn("for rel in files", source)
        self.assertNotIn("rmtree(static_dir", source)


if __name__ == "__main__":
    unittest.main()
