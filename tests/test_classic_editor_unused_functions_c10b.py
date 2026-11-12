"""C10 后续：普通画布编辑器 canvas.js 里 20 个没有任何调用方的函数已删除
（旧版生成流程、RunningHub 工作流导入、框选连线等残留）。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")

REMOVED = (
    "isRunningHubProvider normalizeProviderId apiErrorMessage msChatModelOptions defaultLTXSegment clearImageNode "
    "splitPromptIntoItems videoRefsFromNode loopInputVideoRefs miniMaxRefSummary currentRunningHubWorkflow "
    "rhFetchAppInfo rhFetchWorkflowInfo rhImportWorkflowJson mediaRefsFromNode runGeneratorLegacy "
    "canvasWorkflowEdges computeConnectedWorkflowOrder startSelectionLink connectSelectionToGenerator "
    "LTX_SEGMENT_COLORS"
).split()


class UnusedClassicFunctionsRemovedTests(unittest.TestCase):
    def test_removed_names_are_gone(self):
        for name in REMOVED:
            self.assertNotRegex(CLASSIC, r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", name)

    def test_live_neighbours_are_kept(self):
        # 和被删函数同名或相邻、但仍在使用的函数
        for name in ("runGenerator", "openAssetManager", "isRemoteVideoReferenceUrl"):
            self.assertRegex(CLASSIC, r"function\s+" + name + r"\s*\(", name)


if __name__ == "__main__":
    unittest.main()
