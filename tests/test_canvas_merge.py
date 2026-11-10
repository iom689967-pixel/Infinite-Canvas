"""static/js/canvas-merge.js 的单元测试：画布三方合并的纯函数。

用 Node 加载模块并执行用例，Python 只做断言。用例名后注明“现有行为”的，是把当前规则的边界情况
固定下来（例如同一字段两边都改时本地胜出），不代表这是最理想的结果——见审计报告的说明。
"""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static/js/canvas-merge.js"


def run_cases(js_body):
    script = f"const M = require({json.dumps(str(MODULE))});\nconst out = {{}};\n{js_body}\nconsole.log(JSON.stringify(out));"
    return json.loads(subprocess.check_output(["node", "-e", script], text=True))


class ThreeWayMergeFieldsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(r"""
const base = {id:'n', x:1, y:1, text:'a', tags:['t1'], meta:{a:1, b:1}, gone:'g'};
const m = (local, remote, opts) => M.threeWayMergeFields(local, remote, base, opts);
out.remoteOnly      = m({...base}, {...base, x:9});
out.localOnly       = m({...base, x:5}, {...base});
out.sameFieldBoth   = m({...base, x:5}, {...base, x:9});
out.sameValueBoth   = m({...base, x:7}, {...base, x:7});
out.differentFields = m({...base, x:5}, {...base, y:9});
const noGoneRemote = {...base}; delete noGoneRemote.gone;
out.remoteDeletesLocalKeeps   = m({...base}, noGoneRemote);
out.remoteDeletesLocalEdits   = m({...base, gone:'edited'}, noGoneRemote);
const noGoneLocal = {...base}; delete noGoneLocal.gone;
out.localDeletesRemoteEdits   = m(noGoneLocal, {...base, gone:'edited'});
out.bothAddDifferentFields    = m({...base, l:1}, {...base, r:2});
out.bothAddSameFieldDifferent = m({...base, k:'L'}, {...base, k:'R'});
out.arrayRemoteAppend   = m({...base}, {...base, tags:['t1','r']});
out.arrayBothAppend     = m({...base, tags:['t1','l']}, {...base, tags:['t1','r']});
out.nestedBothSubkeys   = m({...base, meta:{a:2, b:1}}, {...base, meta:{a:1, b:2}});
out.skipKeys            = M.threeWayMergeFields({...base, images:['l']}, {...base, images:['r']}, {...base, images:[]}, {skipKeys:['images']});
out.noBase              = M.threeWayMergeFields({x:5}, {x:9, y:1}, null);
const local = {...base, x:5}, remote = {...base, y:9};
const localCopy = JSON.stringify(local), remoteCopy = JSON.stringify(remote), baseCopy = JSON.stringify(base);
M.threeWayMergeFields(local, remote, base);
out.inputsUntouched = JSON.stringify(local) === localCopy && JSON.stringify(remote) === remoteCopy && JSON.stringify(base) === baseCopy;
""")

    def test_one_side_changes(self):
        self.assertEqual(self.r["remoteOnly"]["x"], 9)
        self.assertEqual(self.r["localOnly"]["x"], 5)
        self.assertEqual((self.r["differentFields"]["x"], self.r["differentFields"]["y"]), (5, 9))

    def test_same_field_changed_on_both_sides_local_wins(self):  # 现有行为
        self.assertEqual(self.r["sameFieldBoth"]["x"], 5)
        self.assertEqual(self.r["sameValueBoth"]["x"], 7)

    def test_delete_on_one_side_edit_on_the_other(self):
        self.assertNotIn("gone", self.r["remoteDeletesLocalKeeps"])
        self.assertEqual(self.r["remoteDeletesLocalEdits"]["gone"], "edited")
        # 现有行为：本地删除的字段即使对方改过也保持删除（本地改动优先）
        self.assertNotIn("gone", self.r["localDeletesRemoteEdits"])

    def test_both_sides_add(self):
        self.assertEqual((self.r["bothAddDifferentFields"]["l"], self.r["bothAddDifferentFields"]["r"]), (1, 2))
        self.assertEqual(self.r["bothAddSameFieldDifferent"]["k"], "L")  # 现有行为：本地胜出

    def test_array_and_object_fields_are_atomic(self):
        self.assertEqual(self.r["arrayRemoteAppend"]["tags"], ["t1", "r"])
        # 现有行为：数组/对象整体比较，两边都改时整体取本地，对方追加的元素或改的子键会丢
        self.assertEqual(self.r["arrayBothAppend"]["tags"], ["t1", "l"])
        self.assertEqual(self.r["nestedBothSubkeys"]["meta"], {"a": 2, "b": 1})

    def test_skip_keys_and_missing_base(self):
        self.assertEqual(self.r["skipKeys"]["images"], ["l"])
        self.assertEqual(self.r["noBase"], {"x": 9, "y": 1})  # 没有基线：以 remote 为准

    def test_inputs_not_mutated(self):
        self.assertTrue(self.r["inputsUntouched"])


class KeyedListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(r"""
const key = item => item.id;
const base = [{id:'a'}, {id:'b'}, {id:'c'}];
out.bothAdd = M.mergeKeyedLists([...base, {id:'l'}], [...base, {id:'r'}], base, key).map(key);
out.deletes = M.mergeKeyedLists([{id:'a'}, {id:'c'}], [{id:'a'}, {id:'b'}], base, key).map(key);
out.bothDelete = M.mergeKeyedLists([{id:'a'}, {id:'c'}], [{id:'a'}, {id:'c'}], base, key).map(key);
out.sameKeyAdded = M.mergeKeyedLists([...base, {id:'n', v:'L'}], [...base, {id:'n', v:'R'}], base, key).find(i => i.id === 'n').v;
out.deleteVsModify = M.mergeKeyedLists([{id:'a'}, {id:'c'}], [{id:'a'}, {id:'b', v:'edited'}, {id:'c'}], base, key).map(key);
""")

    def test_both_sides_add(self):
        self.assertEqual(self.r["bothAdd"], ["a", "b", "c", "r", "l"])
        self.assertEqual(self.r["sameKeyAdded"], "L")

    def test_deletions_on_either_side_are_kept(self):
        self.assertEqual(self.r["deletes"], ["a"])          # 本地删 b、对方删 c
        self.assertEqual(self.r["bothDelete"], ["a", "c"])
        # 现有行为：列表项按 key 判断，不看内容；一边删除一边修改时删除生效
        self.assertEqual(self.r["deleteVsModify"], ["a", "c"])


class SmartListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(r"""
out.images = M.mergeImageLists([{url:'1'}, {url:'2'}, {name:'no-url'}, {url:'1'}], [{url:'2'}, {url:'3'}, {name:'remote-no-url'}]);
const calls = [];
const merged = M.mergeNodeListsUnion(
  [{id:'a', v:'L'}, {id:'l'}],
  [{id:'a', v:'R'}, {id:'r'}],
  [{id:'a', v:'B'}, {id:'deleted-locally'}],
  (local, remote, base) => { calls.push([local.v, remote.v, base && base.v]); return {...remote, merged:true}; });
out.nodeIds = merged.map(n => n.id);
out.callArgs = calls;
out.resurrected = M.mergeNodeListsUnion([{id:'a'}], [{id:'a'}, {id:'deleted-locally'}], [{id:'a'}, {id:'deleted-locally'}], (l, r) => r).map(n => n.id);
out.connections = M.mergeConnectionsUnion(
  [{from:'a', to:'b'}, {from:'a', to:'x'}],
  [{from:'a', to:'b'}, {from:'b', to:'a', kind:'ref'}],
  new Set(['a', 'b'])).map(c => `${c.from}>${c.to}`);
""")

    def test_image_union(self):
        self.assertEqual([i.get("url") or i.get("name") for i in self.r["images"]], ["1", "2", "no-url", "3"])

    def test_node_union_and_callback(self):
        self.assertEqual(self.r["nodeIds"], ["a", "l", "r"])
        self.assertEqual(self.r["callArgs"], [["L", "R", "B"]])

    def test_smart_deletions_are_resurrected(self):  # 现有行为（审计 C8，智能画布未修复）
        self.assertEqual(self.r["resurrected"], ["a", "deleted-locally"])

    def test_connections_union_drops_dangling(self):
        self.assertEqual(self.r["connections"], ["a>b", "b>a"])


class ClassicCanvasMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(r"""
const n = (id, extra={}) => ({id, type:'prompt', x:0, y:0, text:id, ...extra});
const base = {title:'T', icon:'i', nodes:[n('a'), n('b'), n('c'), n('d'), n('e')],
  connections:[{id:'e1', from:'a', to:'b'}], logs:[{id:'l0', createdAt:1}]};
const local = {title:'T', icon:'i',
  nodes:[n('a', {x:5}), n('b', {text:'local'}), n('d', {x:9}), n('e'), n('new-local'), n('both', {x:1})],
  connections:[{id:'e1', from:'a', to:'b'}, {id:'e-local', from:'a', to:'d'}],
  logs:[{id:'l0', createdAt:1}, {id:'l-local', createdAt:3}]};
const remote = {title:'Renamed', icon:'i',
  nodes:[n('a', {y:7}), n('b', {text:'remote'}), n('c', {text:'remote-edit'}), n('new-remote'), n('both', {x:2})],
  connections:[{id:'e1', from:'a', to:'b'}, {id:'e-remote', from:'b', to:'a'}],
  logs:[{id:'l-remote', createdAt:2}, {id:'l0', createdAt:1}]};
// 本地：改 a.x、改 b.text、删 c、改 d、保留 e、新增 new-local；远端：改 a.y、改 b.text、改 c、删 d 和 e、新增 new-remote
const r = M.mergeClassicCanvas(local, remote, base);
out.nodes = r.nodes.map(e => ({id:e.id, source:e.source, node:e.node}));
out.connections = r.connections.map(c => c.id);
out.logs = r.logs.map(l => l.id);
out.title = r.title;
out.localRenamed = M.mergeClassicCanvas({...local, title:'Mine'}, remote, base).title;
const many = Array.from({length:600}, (_, i) => ({id:'x' + i, createdAt:i}));
out.logCap = M.mergeClassicCanvas({nodes:[], logs:many}, {nodes:[], logs:[]}, {nodes:[], logs:[]}).logs.length;
out.newestLog = M.mergeClassicCanvas({nodes:[], logs:many}, {nodes:[], logs:[]}, {nodes:[], logs:[]}).logs[0].id;
// 数组字段：两边各往 images 追加
const imgBase = {nodes:[n('o', {images:['1']})]};
out.arrayField = M.mergeClassicCanvas({nodes:[n('o', {images:['1', 'L']})]}, {nodes:[n('o', {images:['1', 'R']})]}, imgBase).nodes[0].node.images;
// syncNodeFields：保持对象引用和运行时字段
const live = {id:'s', x:1, gone:true, running:true};
const synced = M.syncNodeFields(live, {id:'s', x:1, gone:true}, {id:'s', x:2, added:'y'});
out.sync = {same:synced === live, value:{...synced}};
""")
        cls.by_id = {e["id"]: e for e in cls.r["nodes"]}

    def test_both_edit_same_node_different_fields(self):
        a = self.by_id["a"]["node"]
        self.assertEqual((a["x"], a["y"]), (5, 7))

    def test_both_edit_same_field_local_wins(self):  # 现有行为
        self.assertEqual(self.by_id["b"]["node"]["text"], "local")
        self.assertEqual(self.by_id["both"]["node"]["x"], 1)  # 两边都新增同 id 节点、无基线：保留本地

    def test_delete_vs_edit(self):
        self.assertEqual(self.by_id["c"]["source"], "remote")   # 本地删、远端改 → 恢复
        self.assertEqual(self.by_id["c"]["node"]["text"], "remote-edit")
        self.assertEqual(self.by_id["d"]["node"]["x"], 9)        # 远端删、本地改 → 保留
        self.assertNotIn("e", self.by_id)                       # 远端删、本地未改 → 删除

    def test_both_sides_add_nodes_connections_logs(self):
        self.assertEqual(list(self.by_id), ["a", "b", "d", "new-local", "both", "c", "new-remote"])
        self.assertEqual(self.r["connections"], ["e1", "e-remote", "e-local"])
        self.assertEqual(self.r["logs"], ["l-local", "l-remote", "l0"])

    def test_title_follows_remote_unless_renamed_locally(self):
        self.assertEqual(self.r["title"], "Renamed")
        self.assertEqual(self.r["localRenamed"], "Mine")

    def test_logs_sorted_and_capped(self):
        self.assertEqual(self.r["logCap"], 500)
        self.assertEqual(self.r["newestLog"], "x599")

    def test_array_field_both_appended_local_wins(self):  # 现有行为：数组整体比较
        self.assertEqual(self.r["arrayField"], ["1", "L"])

    def test_sync_node_fields_in_place(self):
        self.assertTrue(self.r["sync"]["same"])
        self.assertEqual(self.r["sync"]["value"], {"id": "s", "x": 2, "added": "y", "running": True})


if __name__ == "__main__":
    unittest.main()
