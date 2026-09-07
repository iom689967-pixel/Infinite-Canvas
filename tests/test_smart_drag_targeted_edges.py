import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", SMART)
    if not match:
        raise AssertionError(f"missing function: {name}")
    paren = SMART.find("(", match.start())
    paren_depth = 0
    params_end = -1
    for index in range(paren, len(SMART)):
        if SMART[index] == "(":
            paren_depth += 1
        elif SMART[index] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                params_end = index
                break
    if params_end < 0:
        raise AssertionError(f"unterminated params: {name}")
    brace = SMART.find("{", params_end)
    depth = 0
    for index in range(brace, len(SMART)):
        if SMART[index] == "{":
            depth += 1
        elif SMART[index] == "}":
            depth -= 1
            if depth == 0:
                return SMART[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


def run_node(source):
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class SmartDragTargetedEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = (
            "smartTemporaryOutputConnectionCurve",
            "normalizedSmartConnectionAnchor",
            "smartConnectionAnchorPoint",
            "smartConnectionGeometry",
            "smartConnectionDomIndices",
            "cacheConnectionDomElements",
            "smartConnectionHasVisibleDom",
            "setSmartConnectionDomAttribute",
            "updateQuickConnectTemporaryConnectionsForMovedNodes",
            "updateConnectionsForMovedNodes",
        )
        functions = "\n".join(function_source(name) for name in names)
        harness_template = """
function makeClassList(initial=[]){{
  const values = new Set(initial);
  return {{values, contains(name){{ return values.has(name); }}}};
}}
function makeElement(role, spec, attributes={{}}, metadata={{}}){{
  const attrs = new Map(Object.entries(attributes).map(([key, value]) => [key, String(value)]));
  const classes = role === 'visual' ? ['conn-line', 'conn-selected', 'hover-probe']
    : role === 'hit' ? ['conn-hit'] : role === 'endpoint' ? ['conn-end'] : ['conn-cut'];
  return {{
    dataset:{{connIndex:spec, edgeRole:role, ...metadata}},
    classList:makeClassList(classes),
    eventToken:{{role, spec}},
    writes:[],
    getAttribute(name){{ return attrs.has(name) ? attrs.get(name) : null; }},
    setAttribute(name, value){{ attrs.set(name, String(value)); this.writes.push({{name, value:String(value)}}); }}
  }};
}}
let nodes = [];
let canvas = {{connections:[]}};
let connectionDomEntriesByIndex = new Map();
let pendingQuickConnection = null;
let allElements = [];
let quickPath = null;
let groupScopes = {{}};
const CSS = {{escape(value){{ return String(value); }}}};
const world = {{
  querySelector(selector){{
    if(selector.includes('path.quick-connect-temp')) return quickPath;
    if(selector === 'svg.connection-layer') return {{}};
    return null;
  }},
  querySelectorAll(selector){{ return selector.includes('data-conn-index') ? allElements : []; }}
}};
function nodeRect(node){{ return {{x:node.x, y:node.y, width:node.w, height:node.h}}; }}
function smartGroupScopeId(id){{ return groupScopes[id] || (nodes.find(node => node.id === id)?.type === 'smart-group' ? id : ''); }}
__FUNCTIONS__
function makeRenderedEntry(indices, fromId, toId, kind='flow'){{
  const spec = indices.join(',');
  const fromNode = nodes.find(node => node.id === fromId);
  const toNode = nodes.find(node => node.id === toId);
  const geometry = smartConnectionGeometry(fromNode, toNode, kind, null, null);
  if(!geometry) throw new Error(`missing geometry for ${{spec}}: ${{fromId}} -> ${{toId}}`);
  const metadata = {{edgeFrom:fromId, edgeTo:toId, edgeKind:kind, edgeFromSide:geometry.fromSide, edgeToSide:geometry.toSide}};
  const elements = [
    makeElement('visual', spec, {{d:geometry.curve}}, metadata),
    makeElement('hit', spec, {{d:geometry.curve}}),
    makeElement('endpoint', spec, {{cx:geometry.tx, cy:geometry.ty}}),
    makeElement('cut', spec, {{transform:`translate(${{geometry.mx}} ${{geometry.my}})`}})
  ];
  allElements.push(...elements);
  return elements;
}}
function install(nodeList, connections){{
  nodes = nodeList;
  canvas = {{connections}};
  pendingQuickConnection = null;
  quickPath = null;
  groupScopes = {{}};
  allElements = [];
  connections.forEach((connection, index) => makeRenderedEntry([index], connection.from, connection.to, connection.kind || 'flow'));
  cacheConnectionDomElements(allElements);
}}
function writesFor(index, role, name){{
  const entry = connectionDomEntriesByIndex.get(index);
  return entry[role].writes.filter(write => !name || write.name === name).length;
}}
const results = {{}};

install([{{id:'solo', x:0, y:0, w:100, h:80}}], []);
nodes[0].x += 20;
results.zero = updateConnectionsForMovedNodes(['solo']);

const threeNodes = [{{id:'m', x:0, y:0, w:100, h:80}}, ...Array.from({{length:3}}, (_, i) => ({{id:`t${{i}}`, x:300, y:i * 120, w:100, h:80}}))];
install(threeNodes, Array.from({{length:3}}, (_, i) => ({{from:'m', to:`t${{i}}`, kind:'flow'}})));
nodes[0].x += 20;
results.three = updateConnectionsForMovedNodes(new Set(['m']));

const largeNodes = Array.from({{length:277}}, (_, i) => ({{id:i === 0 ? 'm' : `n${{i}}`, x:(i % 20) * 180, y:Math.floor(i / 20) * 130, w:120, h:90}}));
const largeConnections = Array.from({{length:544}}, (_, i) => i < 7
  ? ({{from:'m', to:`n${{i + 1}}`, kind:i === 6 ? 'input' : 'flow', data:i === 5 ? {{origin:'canvas-reference'}} : undefined}})
  : ({{from:'n' + (1 + (i % 275)), to:'n' + (1 + ((i + 31) % 275)), kind:i % 17 === 0 ? 'history' : 'flow'}}));
install(largeNodes, largeConnections);
const selectedLine = connectionDomEntriesByIndex.get(0).visual;
const selectedToken = selectedLine.eventToken;
const canvasReferenceBefore = JSON.stringify(canvas.connections[5]);
const untouchedBefore = connectionDomEntriesByIndex.get(100).visual.getAttribute('d');
nodes[0].x += 24;
nodes[0].y += 11;
results.large = updateConnectionsForMovedNodes(['m']);
results.large.nonIncidentWrites = allElements
  .filter(element => smartConnectionDomIndices(element.dataset.connIndex).every(index => index >= 7))
  .reduce((total, element) => total + element.writes.length, 0);
results.large.untouchedSame = connectionDomEntriesByIndex.get(100).visual.getAttribute('d') === untouchedBefore;
results.large.visualD = Array.from({{length:7}}, (_, index) => writesFor(index, 'visual', 'd')).reduce((a, b) => a + b, 0);
results.large.hitD = Array.from({{length:7}}, (_, index) => writesFor(index, 'hit', 'd')).reduce((a, b) => a + b, 0);
results.large.cutTransform = Array.from({{length:7}}, (_, index) => writesFor(index, 'cut', 'transform')).reduce((a, b) => a + b, 0);
results.large.selectedKept = selectedLine.classList.contains('conn-selected');
results.large.hoverKept = selectedLine.classList.contains('hover-probe');
results.large.eventKept = selectedLine.eventToken === selectedToken;
results.large.dataIndexKept = selectedLine.dataset.connIndex === '0';
results.large.canvasReferenceKept = JSON.stringify(canvas.connections[5]) === canvasReferenceBefore;

install(
  [{{id:'target-source', x:0, y:0, w:100, h:80}}, {{id:'target-moved', x:300, y:0, w:100, h:80}}],
  [{{from:'target-source', to:'target-moved', kind:'flow'}}]
);
nodes.find(node => node.id === 'target-moved').x += 25;
nodes.find(node => node.id === 'target-moved').y += 10;
results.targetMoved = updateConnectionsForMovedNodes(['target-moved']);

const multiNodes = [
  {{id:'a', x:0, y:0, w:100, h:80}}, {{id:'b', x:220, y:0, w:100, h:80}},
  {{id:'x', x:440, y:0, w:100, h:80}}, {{id:'y', x:660, y:0, w:100, h:80}}
];
install(multiNodes, [
  {{from:'a', to:'b'}}, {{from:'a', to:'x'}}, {{from:'y', to:'b'}}, {{from:'x', to:'y'}}
]);
nodes.find(node => node.id === 'a').y += 30;
nodes.find(node => node.id === 'b').y += 30;
results.multi = updateConnectionsForMovedNodes(['a', 'b', 'a']);
results.multi.internalVisualWrites = writesFor(0, 'visual', 'd');
results.multi.unrelatedWrites = writesFor(3, 'visual');

nodes = [
  {{id:'source', x:0, y:0, w:100, h:80}},
  {{id:'group', type:'smart-group', x:300, y:0, w:240, h:180}},
  {{id:'member-a', x:320, y:20, w:80, h:60}},
  {{id:'member-b', x:420, y:20, w:80, h:60}}
];
canvas = {{connections:[{{from:'source', to:'member-a'}}, {{from:'source', to:'member-b'}}]}};
groupScopes = {{'member-a':'group', 'member-b':'group'}};
allElements = [];
makeRenderedEntry([0, 1], 'source', 'group');
cacheConnectionDomElements(allElements);
nodes.find(node => node.id === 'group').x += 40;
nodes.find(node => node.id === 'member-a').x += 40;
nodes.find(node => node.id === 'member-b').x += 40;
results.group = updateConnectionsForMovedNodes(['group', 'member-a', 'member-b']);
results.group.mergedVisualWrites = connectionDomEntriesByIndex.get(0).visual.writes.filter(write => write.name === 'd').length;
results.group.sameEntry = connectionDomEntriesByIndex.get(0) === connectionDomEntriesByIndex.get(1);

install([{{id:'missing-a', x:0, y:0, w:100, h:80}}, {{id:'missing-b', x:300, y:0, w:100, h:80}}], [{{from:'missing-a', to:'missing-b'}}]);
connectionDomEntriesByIndex = new Map();
allElements = [];
results.fallback = updateConnectionsForMovedNodes(['missing-a']);

install([{{id:'quick', x:0, y:0, w:100, h:80}}], []);
quickPath = makeElement('visual', '', {{d:'old'}}, {{previewSourceId:'quick'}});
pendingQuickConnection = {{drag:{{fromId:'quick', fromPort:'out', aggregate:false}}, worldPoint:{{x:500, y:200}}}};
nodes[0].x += 30;
results.quick = updateConnectionsForMovedNodes(['quick']);
results.quick.pathWrites = quickPath.writes.filter(write => write.name === 'd').length;

const historyFrom = {{id:'history-from', x:10, y:20, w:100, h:80}};
const historyTo = {{id:'history-to', x:300, y:250, w:120, h:90}};
results.historyGeometry = smartConnectionGeometry(historyFrom, historyTo, 'history', null, null);
console.log(JSON.stringify(results));
"""
        cls.runtime = run_node(
            harness_template.replace("{{", "{").replace("}}", "}").replace("__FUNCTIONS__", functions)
        )

    def test_01_zero_edge_drag_uses_no_global_or_targeted_edge_work(self):
        self.assertEqual(self.runtime["zero"]["scanned"], 0)
        self.assertEqual(self.runtime["zero"]["incident"], 0)
        self.assertEqual(self.runtime["zero"]["updated"], 0)
        self.assertTrue(self.runtime["zero"]["ok"])

    def test_02_three_incident_edges_update_only_three(self):
        result = self.runtime["three"]
        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["incident"], 3)
        self.assertEqual(result["updated"], 3)

    def test_03_seven_incident_edges_are_targeted_inside_544_total(self):
        result = self.runtime["large"]
        self.assertEqual(result["scanned"], 544)
        self.assertEqual(result["incident"], 7)
        self.assertEqual(result["updated"], 7)
        self.assertEqual(result["visualD"], 7)
        self.assertEqual(result["hitD"], 7)
        self.assertEqual(result["cutTransform"], 7)

    def test_04_non_incident_edge_geometry_is_never_written(self):
        result = self.runtime["large"]
        self.assertEqual(result["nonIncidentWrites"], 0)
        self.assertTrue(result["untouchedSame"])

    def test_05_multi_selection_collects_external_and_internal_edges_once(self):
        result = self.runtime["multi"]
        self.assertEqual(result["incident"], 3)
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["internalVisualWrites"], 1)
        self.assertEqual(result["unrelatedWrites"], 0)

    def test_06_group_drag_updates_merged_member_edge_once(self):
        result = self.runtime["group"]
        self.assertEqual(result["incident"], 2)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["mergedVisualWrites"], 1)
        self.assertTrue(result["sameEntry"])

    def test_07_visual_hit_endpoint_and_cut_use_shared_live_geometry(self):
        result = self.runtime["large"]
        self.assertEqual(result["visualWrites"], 7)
        self.assertEqual(result["hitWrites"], 7)
        self.assertEqual(result["cutWrites"], 7)
        self.assertEqual(self.runtime["targetMoved"]["endpointWrites"], 2)
        history = self.runtime["historyGeometry"]
        self.assertTrue(history["curve"].startswith("M60 100 C 60"))
        self.assertEqual(history["tx"], 360)
        self.assertEqual(history["ty"], 250)

    def test_08_selection_hover_identity_and_interaction_metadata_survive(self):
        result = self.runtime["large"]
        self.assertTrue(result["selectedKept"])
        self.assertTrue(result["hoverKept"])
        self.assertTrue(result["eventKept"])
        self.assertTrue(result["dataIndexKept"])

    def test_09_canvas_reference_connection_data_is_not_mutated(self):
        self.assertTrue(self.runtime["large"]["canvasReferenceKept"])
        targeted = function_source("updateConnectionsForMovedNodes")
        self.assertNotIn("connection.data", targeted)
        self.assertNotIn("canvasReferences", targeted)

    def test_10_missing_visible_edge_dom_requests_full_render_fallback(self):
        result = self.runtime["fallback"]
        self.assertFalse(result["ok"])
        scheduler = function_source("scheduleInteractionLayerRefresh")
        self.assertIn("!updateConnectionsForMovedNodes(movedIds).ok", scheduler)
        self.assertIn("refreshConnectionLayer()", scheduler)

    def test_11_drag_raf_has_no_unconditional_global_edge_render_or_rebind(self):
        targeted = function_source("updateConnectionsForMovedNodes")
        self.assertNotIn("renderConnections", targeted)
        self.assertNotIn("refreshConnectionLayer", targeted)
        self.assertNotIn("replaceWith", targeted)
        self.assertNotIn("bindConnectionEvents", targeted)
        move = function_source("moveNodeElementsDuringDrag")
        self.assertIn("scheduleInteractionLayerRefresh(groupItems.map(item => item.id))", move)

    def test_12_dom_roles_and_geometry_metadata_do_not_change_edge_schema(self):
        render = function_source("renderConnections")
        for role in ("visual", "hit", "endpoint", "cut"):
            self.assertIn(f'data-edge-role=\"{role}\"', render)
        self.assertIn("data-edge-from=", render)
        self.assertIn("data-edge-to=", render)
        self.assertIn("data-edge-kind=", render)
        self.assertNotIn("canvas.connections =", render)

    def test_13_quick_connect_preview_is_patched_without_rebuilding_layer(self):
        result = self.runtime["quick"]
        self.assertEqual(result["quickConnectWrites"], 1)
        self.assertEqual(result["pathWrites"], 1)
        quick_svg = function_source("quickConnectTemporaryConnectionSvg")
        self.assertIn("data-preview-source-id", quick_svg)

    def test_14_full_render_still_indexes_and_binds_topology_dom(self):
        binder = function_source("bindConnectionEvents")
        self.assertIn("cacheConnectionDomElements(elements)", binder)
        self.assertIn("addEventListener('dblclick'", binder)
        self.assertIn("addEventListener('click'", binder)

    def test_15_plain_drag_mouseup_does_not_schedule_global_edge_refresh(self):
        start = SMART.index("if(dragState){", SMART.index("window.onmouseup = e =>"))
        end = SMART.index("\n    }\n};", start)
        mouseup_drag = SMART[start:end]
        self.assertIn("needsLoopPreviewRefresh", mouseup_drag)
        self.assertIn("if(needsLoopPreviewRefresh) scheduleConnectionLayerRefresh()", mouseup_drag)
        self.assertNotIn("\n            scheduleConnectionLayerRefresh();", mouseup_drag)


if __name__ == "__main__":
    unittest.main()
