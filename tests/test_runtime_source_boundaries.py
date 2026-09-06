import asyncio
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimeSourceBoundaryTests(unittest.TestCase):
    def test_versioned_static_html_is_rewritten_in_response_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            static_dir.mkdir()
            script = static_dir / "app.js"
            script.write_text("console.log('ok');\n", encoding="utf-8")
            page = static_dir / "page.html"
            page.write_text('<script src="/static/app.js?v=old"></script>\n', encoding="utf-8")
            before = sha256(page)

            with (
                patch.object(main, "STATIC_DIR", str(static_dir)),
                patch.object(main, "current_app_version", return_value="2099.01.02"),
            ):
                files = main.VersionedStaticFiles(directory=str(static_dir))
                response = asyncio.run(
                    files.get_response(
                        "page.html",
                        {"type": "http", "method": "GET", "headers": []},
                    )
                )

            self.assertEqual(response.status_code, 200)
            self.assertIn(b'/static/app.js?v=2099.01.02.', response.body)
            self.assertEqual(response.headers["cache-control"], "no-cache")
            self.assertEqual(sha256(page), before)

    def test_saving_provider_writes_runtime_data_but_not_static_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            data_dir = temp_root / "data"
            static_dir = temp_root / "static" / "runninghub"
            data_dir.mkdir()
            static_dir.mkdir(parents=True)
            provider_file = data_dir / "api_providers.json"
            workflow_file = data_dir / "runninghub_workflows.json"
            static_template = static_dir / "api_providers.json"
            static_template.write_text(
                json.dumps(
                    [{"id": "runninghub", "name": "RunningHub", "rh_apps": [], "rh_workflows": []}]
                ),
                encoding="utf-8",
            )
            before = sha256(static_template)

            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", str(data_dir)))
                stack.enter_context(patch.object(main, "API_PROVIDERS_FILE", str(provider_file)))
                stack.enter_context(
                    patch.object(main, "RUNNINGHUB_WORKFLOW_STORE_FILE", str(workflow_file))
                )
                stack.enter_context(
                    patch.object(
                        main,
                        "STATIC_RUNNINGHUB_API_PROVIDERS_FILE",
                        str(static_template),
                    )
                )
                stack.enter_context(patch.object(main, "update_env_values"))
                stack.enter_context(patch.object(main, "reload_env_globals"))

                payload = [
                    main.ApiProviderPayload(
                        id="runninghub",
                        name="RunningHub",
                        base_url="https://www.runninghub.ai",
                        protocol="runninghub",
                        rh_workflows=[
                            {"id": "example-workflow", "workflowId": "example-workflow"}
                        ],
                    )
                ]
                result = asyncio.run(main.save_providers(payload))

            self.assertTrue(provider_file.exists())
            self.assertEqual(result["providers"][0]["id"], "runninghub")
            self.assertEqual(sha256(static_template), before)

    def test_runtime_source_writers_are_not_reintroduced(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("sync_static_html_versions", source)
        self.assertNotIn("sync_runninghub_provider_workflows_to_static_template", source)
        self.assertNotIn("mutate_static_runninghub_provider", source)


if __name__ == "__main__":
    unittest.main()
