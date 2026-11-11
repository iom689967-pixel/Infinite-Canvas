"""C10：普通画布编辑器里"选画布"列表（gate）的旧代码已拆到 canvas-list.html，编辑器页面没有这些 DOM。
这些代码接不到界面，只在每次保存后多拉一次画布列表和回收站。删除后编辑器不再引用它们。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
HTML = (ROOT / "static/canvas.html").read_text(encoding="utf-8")

REMOVED_FUNCTIONS = (
    "refreshGateViewControls setCreateMode loadCanvasList loadTrashList refreshTrashCount setTrashMode renderCanvasList "
    "compareCanvasRecords sortCanvasListByUpdated setCanvasSortMode patchCanvasMeta togglePinCanvas setCanvasColorValue "
    "commitCanvasOwner updateCanvasListRecord renderCanvasListInto closeCanvasMetaPopover renderCanvasMetaPopover "
    "positionCanvasMetaPopover createCanvas createSmartCanvas toggleEmojiPicker setCanvasIcon startTitleEdit setCanvasTitle "
    "returnToCanvasManager requestDeleteCanvas requestPurgeCanvas cancelDeleteCanvas deleteCanvas restoreCanvas purgeCanvas "
    "renderCanvasIcon"
).split()
REMOVED_STATE = ("canvases deletedCanvases creatingCanvas createCanvasKind trashMode pendingDeleteCanvasId pendingPurgeCanvasId "
                 "emojiPickerCanvasId canvasMetaAnchorId canvasSortMode CANVAS_COLOR_OPTIONS CANVAS_EMOJIS").split()


def function_body(source, name):
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    assert match, name
    depth = 0
    for index in range(source.index("{", match.end()), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start():index + 1]
    raise AssertionError(name)


class DeadGateCodeRemovedTests(unittest.TestCase):
    def test_gate_functions_and_state_are_gone(self):
        code = CLASSIC.replace("/api/canvases", "")
        for name in REMOVED_FUNCTIONS + REMOVED_STATE:
            self.assertNotRegex(code, r"\b" + name + r"\b", name)

    def test_editor_only_references_elements_that_exist_on_its_page(self):
        ids = set(re.findall(r'id="([^"]+)"', HTML))
        gate_refs = [i for i in re.findall(r"getElementById\('([^']+)'\)", CLASSIC) if i.startswith("gate") or i == "canvasGate"]
        self.assertEqual([i for i in gate_refs if i not in ids], [])

    def test_saving_no_longer_refetches_the_canvas_list(self):
        body = function_body(CLASSIC, "saveCanvas")
        self.assertNotIn("fetch('/api/canvases')", body)
        self.assertNotIn("/api/canvases/trash", CLASSIC)

    def test_live_editor_paths_are_kept(self):
        # 打开画布、记录最近打开、切到智能画布页、列表页改名后同步标题，这些都还在
        self.assertIn("touchCanvasOpened(canvas.id)", function_body(CLASSIC, "openCanvas"))
        self.assertIn("openSmartCanvasPage(", function_body(CLASSIC, "openCanvas"))
        self.assertIn("/touch", function_body(CLASSIC, "touchCanvasOpened"))
        self.assertIn("currentCanvasTitle.textContent", function_body(CLASSIC, "applyRemoteCanvasTitleIcon"))
        self.assertIn("window.openCanvas = openCanvas;", CLASSIC)


if __name__ == "__main__":
    unittest.main()
