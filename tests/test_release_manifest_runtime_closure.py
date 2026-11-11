"""发行清单必须包含 main.py 运行时会导入的每个本仓库模块。

应用内更新只下载 release-manifest.json 的 include 列表；校验时只编译 main.py、不实际导入。
漏掉一个被导入的模块，更新后服务就会因为 ModuleNotFoundError 起不来。
"""
import ast
import json
import subprocess
import unittest
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parents[1]


def tracked_files():
    return subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()


def local_import_closure(entry="main"):
    """从 entry 出发，按 import 语句递归收集本仓库根目录下的 .py 模块和 providers 包里的模块。"""
    tracked = set(tracked_files())
    top_level = {p[:-3] for p in tracked if p.endswith(".py") and "/" not in p}
    seen, todo, files = set(), [entry], set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        path = module.replace(".", "/")
        source = ROOT / (path + ".py")
        if not source.exists():
            source = ROOT / path / "__init__.py"
        if not source.exists():
            continue
        files.add(str(source.relative_to(ROOT)))
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            else:
                continue
            for name in names:
                head = name.split(".")[0]
                if head in top_level or head == "providers":
                    todo.append(name)
    return files


class ReleaseManifestRuntimeClosureTests(unittest.TestCase):
    def test_every_module_imported_by_main_is_released(self):
        manifest = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
        closure = local_import_closure()
        self.assertIn("main.py", closure)
        self.assertIn("instance_storage_quota.py", closure)  # main.py 开头无条件导入
        missing = sorted(
            path for path in closure
            if not any(main.release_path_matches(path, scope) for scope in manifest["include"])
        )
        self.assertEqual(missing, [], f"release-manifest.json include 缺少运行时模块：{missing}")


if __name__ == "__main__":
    unittest.main()
