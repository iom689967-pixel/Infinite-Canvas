import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import main


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class StableUpdateChannelTests(unittest.TestCase):
    def test_01_release_repository_is_mio_fork(self):
        self.assertEqual(main.GITHUB_RELEASE_REPO, "iom689967-pixel/Infinite-Canvas")
        self.assertEqual(main.GITHUB_REPO_URL, "https://github.com/iom689967-pixel/Infinite-Canvas")

    def test_02_release_branch_is_stable(self):
        self.assertEqual(main.GITHUB_RELEASE_BRANCH, "stable")
        self.assertEqual(main.app_info()["release_branch"], "stable")

    def test_03_version_is_read_from_stable(self):
        self.assertEqual(
            main.GITHUB_VERSION_URL,
            "https://raw.githubusercontent.com/iom689967-pixel/Infinite-Canvas/stable/VERSION",
        )

    def test_04_update_notes_are_read_from_stable(self):
        self.assertEqual(
            main.GITHUB_UPDATE_NOTES_URL,
            "https://raw.githubusercontent.com/iom689967-pixel/Infinite-Canvas/stable/static/update-notes.json",
        )
        self.assertEqual(main.app_info()["update_notes_url"], main.GITHUB_UPDATE_NOTES_URL)

    def test_05_tree_and_raw_downloads_use_stable(self):
        self.assertEqual(
            main.GITHUB_TREE_URL,
            "https://api.github.com/repos/iom689967-pixel/Infinite-Canvas/git/trees/stable?recursive=1",
        )
        self.assertEqual(
            main.GITHUB_RAW_ROOT,
            "https://raw.githubusercontent.com/iom689967-pixel/Infinite-Canvas/stable",
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(main, "github_bytes", return_value=b"ok") as get_bytes:
            main.download_github_update_files(["main.py", "static/index.html"], temp_dir)
        urls = [call.args[0] for call in get_bytes.call_args_list]
        self.assertEqual(urls, [f"{main.GITHUB_RAW_ROOT}/main.py", f"{main.GITHUB_RAW_ROOT}/static/index.html"])

    def test_06_automatic_sources_never_use_upstream_or_main(self):
        info = main.app_info()
        self.assertEqual(set(info["sources"]), {"github"})
        automatic_urls = [
            main.GITHUB_VERSION_URL,
            main.GITHUB_TREE_URL,
            main.GITHUB_RAW_ROOT,
            main.GITHUB_UPDATE_NOTES_URL,
            info["sources"]["github"]["version_url"],
        ]
        self.assertTrue(all("hero8152" not in url for url in automatic_urls))
        self.assertTrue(all("/main/" not in url and "trees/main" not in url for url in automatic_urls))
        self.assertNotIn("modelscope", INDEX.lower())

    def test_07_runtime_user_data_never_enters_replacement_set(self):
        protected = [
            "data/asset_library.json",
            "data/projects.json",
            "data/prompt_libraries.json",
            "data/media_previews/a.png",
            "data/conversations/a.json",
            "data/storage_settings.json",
            ".runtime/state.json",
            ".tools/bin/tool",
            ".launchd/service.plist",
        ]
        self.assertTrue(all(not main.update_allowed_file(path) for path in protected))
        tree = {
            "tree": [
                {"type": "blob", "path": "main.py"},
                {"type": "blob", "path": "VERSION"},
                {"type": "blob", "path": "static/index.html"},
                *({"type": "blob", "path": path} for path in protected),
            ]
        }
        with patch.object(main, "github_json", return_value=tree):
            _, _, replacement_set = main.github_update_file_list()
        self.assertEqual(replacement_set, ["VERSION", "main.py", "static/index.html"])

    def test_08_api_env_is_never_replaceable(self):
        self.assertFalse(main.update_allowed_file("API/.env"))
        self.assertFalse(main.update_allowed_file("api/.env"))

    def test_09_canvases_are_never_replaceable(self):
        self.assertFalse(main.update_allowed_file("data/canvases/example.json"))

    def test_10_provider_configuration_is_never_replaceable(self):
        self.assertFalse(main.update_allowed_file("data/api_providers.json"))

    def test_11_assets_and_history_are_never_replaceable(self):
        self.assertFalse(main.update_allowed_file("assets/private.png"))
        self.assertFalse(main.update_allowed_file("history.json"))

    def test_12_equal_or_older_version_reports_up_to_date(self):
        with patch.object(main, "current_app_version", return_value="2026.09.07"), patch.object(
            main,
            "fetch_remote_version",
            return_value={"ok": True, "version": "2026.09.07", "error": "", "url": main.GITHUB_VERSION_URL},
        ), patch.object(main, "fetch_release_update_notes", return_value={"ok": True, "version": "2026.09.07", "items": []}):
            result = main.check_update()
        self.assertFalse(result["update_available"])
        self.assertTrue(result["reachable"])
        self.assertIn("setUpdateEntryState('latest'", INDEX)

    def test_13_newer_version_reports_update_available(self):
        with patch.object(main, "current_app_version", return_value="2026.09.07"), patch.object(
            main,
            "fetch_remote_version",
            return_value={"ok": True, "version": "2026.09.08", "error": "", "url": main.GITHUB_VERSION_URL},
        ), patch.object(main, "fetch_release_update_notes", return_value={"ok": True, "version": "2026.09.08", "items": []}):
            result = main.check_update()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"]["version"], "2026.09.08")
        self.assertIn("● 发现新版本", INDEX)

    def test_14_missing_update_notes_do_not_block_update(self):
        missing = {"ok": False, "error": "HTTP 404", "version": "2026.09.08", "items": []}
        with patch.object(main, "current_app_version", return_value="2026.09.07"), patch.object(
            main,
            "fetch_remote_version",
            return_value={"ok": True, "version": "2026.09.08", "error": "", "url": main.GITHUB_VERSION_URL},
        ), patch.object(main, "fetch_release_update_notes", return_value=missing):
            result = main.check_update()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["update_notes"]["items"], [])

    def test_15_unreachable_github_fails_before_backup_or_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(main, "DATA_DIR", temp_dir), patch.object(
            main, "stage_release_update", side_effect=OSError("offline")
        ), patch.object(main, "create_update_backup") as create_backup:
            with self.assertRaises(HTTPException) as raised:
                main.update_from_github(main.UpdateRequest())
        self.assertEqual(raised.exception.status_code, 502)
        create_backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
