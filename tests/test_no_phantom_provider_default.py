import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANVAS = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")


class NoPhantomProviderDefaultTests(unittest.TestCase):
    """A1 前端：画布不得再凭空造出 comfly 平台，也不得把节点上已失效的平台悄悄换成别的平台。"""

    def test_comfly_only_appears_as_legacy_alias(self):
        for name, source in (("canvas.js", CANVAS), ("smart-canvas.js", SMART)):
            lines = [line.strip() for line in source.splitlines()
                     if "comfly" in line.lower() and not line.strip().startswith("//")]
            self.assertEqual(lines, ["const LEGACY_DEFAULT_PROVIDER_IDS = new Set(['comfly']);"], name)

    def test_no_fabricated_provider_list(self):
        self.assertNotIn("defaultApiProviders", CANVAS)
        self.assertNotIn("id:'comfly'", SMART)

    def test_resolvers_keep_explicit_provider_ids(self):
        for fn in ("resolveImageProviderId", "resolveChatProviderId", "resolveVideoProviderId"):
            body = re.search(rf"function {fn}\(id\)\{{(.*?)\n\}}", CANVAS, re.S).group(1)
            self.assertIn("resolveProviderFromList", body, fn)
        self.assertIn("resolveSmartProviderFromList(providerId, chatApiProviders())", SMART)
        self.assertIn("settings.provider_id = resolveSmartProviderFromList(settings.provider_id, providers)", SMART)

    def test_runs_are_guarded(self):
        self.assertIn("canvasProviderProblem(node.apiProvider, imageApiProviders())", CANVAS)
        self.assertIn("canvasProviderProblem(node.apiProvider, videoApiProviders())", CANVAS)
        self.assertIn("canvasProviderProblem(node.llmProvider, chatApiProviders())", CANVAS)
        self.assertIn("smartProviderProblem(runSettings.provider_id, imageProviders())", SMART)
        self.assertIn("smartProviderProblem(runSettings.videoProvider, videoApiProviders())", SMART)


if __name__ == "__main__":
    unittest.main()
