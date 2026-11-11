"""C10：列表页 /meta 保存失败时把界面恢复成修改前的样子；之后又改过同一字段时，旧请求的失败不回滚。
浏览器里的实际行为由 repro 场景 C10_list_meta_failure_rolls_back 验证。"""
import re
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "static/js/canvas-list.js").read_text(encoding="utf-8")


def function_body(name):
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", SOURCE)
    depth = 0
    for index in range(SOURCE.index("{", match.end()), len(SOURCE)):
        depth += {"{": 1, "}": -1}.get(SOURCE[index], 0)
        if depth == 0:
            return SOURCE[match.start():index + 1]


class ListMetaRollbackTests(unittest.TestCase):
    def test_failure_restores_previous_values_unless_superseded(self):
        body = function_body("persistMeta")
        self.assertIn("async function persistMeta(id, patch, previous = null)", body)
        self.assertIn("if(metaWriteSeq.get(`${id}:${k}`) !== seqs[k]) return;", body)
        self.assertIn("if(c[k] !== patch[k]) return;", body)
        self.assertIn("c[k] = previous[k];", body)
        self.assertIn("renderBoard();", body)

    def test_user_edits_pass_their_previous_values(self):
        self.assertIn("await persistMeta(id, { title }, previous);", function_body("setCanvasTitle"))
        self.assertIn("await persistMeta(id, { project: projectId }, previous);", function_body("moveCanvasToProject"))
        self.assertIn("{ board_x: origX, board_y: origY }", SOURCE)

    def test_auto_layout_does_not_roll_back(self):
        # 自动排版回滚后会被立刻重新排版并再次保存，形成请求循环，所以只提示失败
        self.assertRegex(function_body("autoLayoutNulls"), r"persistMeta\(c\.id, \{ board_x: c\.board_x, board_y: c\.board_y \}\);")


if __name__ == "__main__":
    unittest.main()
