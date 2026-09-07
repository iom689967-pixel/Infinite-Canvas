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


class SmartConnectionKeyboardDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = (
            "smartConnectionSelectionKey",
            "isEditableTarget",
            "bindConnectionEvents",
            "connectionSelectionKeysFromSpec",
            "selectConnectionsFromSpec",
            "selectedConnectionIndices",
            "disconnectSelectedConnections",
            "disconnectSelectedConnectionsFromKeyboard",
            "disconnectConnections",
        )
        functions = "\n".join(function_source(name) for name in names)
        harness_template = """
const document = {{activeElement:null}};
function makeClassList(initial=[]){
  const values = new Set(initial);
  return {{
    contains(name){{ return values.has(name); }},
    toggle(name, force){{ if(force) values.add(name); else values.delete(name); }}
  }};
}
function makeEditableTarget(kind){
  const token = kind === 'contenteditable' ? '[contenteditable]'
    : kind === 'prompt' ? '.prompt-input' : kind;
  return {{kind, closest(selector){{ return selector.includes(token) ? this : null; }}}};
}
const canvasTarget = {{closest(){{ return null; }}}};
function makeEvent(key, target=canvasTarget, extras={{}}){
  return {{
    key,
    target,
    repeat:Boolean(extras.repeat),
    shiftKey:Boolean(extras.shiftKey),
    metaKey:Boolean(extras.metaKey),
    ctrlKey:Boolean(extras.ctrlKey),
    prevented:0,
    stopped:0,
    preventDefault(){{ this.prevented += 1; }},
    stopPropagation(){{ this.stopped += 1; }}
  }};
}
function makeEdgeElement(role, spec){
  const listeners = {{}};
  return {{
    role,
    dataset:{{connIndex:String(spec)}},
    classList:makeClassList([role === 'hit' ? 'conn-hit' : role === 'visual' ? 'conn-line' : role === 'cut' ? 'conn-cut' : 'conn-end']),
    addEventListener(name, listener){{ listeners[name] = listener; }},
    dispatch(name, extras={{}}){{
      const event = makeEvent('', canvasTarget, extras);
      listeners[name]?.(event);
      return event;
    }}
  }};
}
let nodes = [];
let canvas = {{connections:[]}};
let selectedConnectionKeys = new Set();
let selectedId = '';
let selectedIds = [];
let selectedImage = {{nodeId:'', index:-1}};
let edgeElements = [];
let renderedRoles = [];
let undoCount = 0;
let renderCount = 0;
let saveCount = 0;
let selectionUiCount = 0;
let demotedHistoryCount = 0;
let minimaxDetachCount = 0;
let detachedInputCleanupCount = 0;
const world = {{querySelectorAll(selector){{ return selector === '[data-conn-index]' ? edgeElements : []; }}};
function cacheConnectionDomElements(){{}}
function smartSelectionUiState(){{
  return {{nodeIds:selectedIds.length ? selectedIds.slice() : selectedId ? [selectedId] : [], image:{{...selectedImage}}, connectionKeys:new Set(selectedConnectionKeys)}};
}}
function updateSmartSelectionUI(){{ selectionUiCount += 1; }}
function pushUndo(){{ undoCount += 1; }}
function scheduleSave(){{ saveCount += 1; }}
function isHistoryGroupNode(node){{ return Boolean(node?.isHistoryGroup); }}
function demoteHistoryGroupNode(node){{ if(node){{ node.isHistoryGroup = false; demotedHistoryCount += 1; }} }}
function smartMinimaxDetachSourceRefs(){{ minimaxDetachCount += 1; }}
function clearDetachedRunInputRefs(){{ detachedInputCleanupCount += 1; }}
function rebuildRenderedRoles(){{
  renderedRoles = canvas.connections.flatMap((connection, index) => ['visual','hit','endpoint','cut'].map(role => ({{role, index, from:connection.from, to:connection.to}})));
}}
function render(){{
  renderCount += 1;
  const valid = new Set(canvas.connections.map(smartConnectionSelectionKey));
  [...selectedConnectionKeys].forEach(key => {{ if(!valid.has(key)) selectedConnectionKeys.delete(key); }});
  rebuildRenderedRoles();
}}
__FUNCTIONS__
function install(connections, nodeList=[]){
  canvas = {{connections:connections.map(connection => ({{...connection}}))}};
  nodes = nodeList.map(node => ({{...node, inputNodeIds:[...(node.inputNodeIds || [])], canvasReferences:[...(node.canvasReferences || [])]}}));
  selectedConnectionKeys = new Set();
  selectedId = '';
  selectedIds = [];
  selectedImage = {{nodeId:'', index:-1}};
  document.activeElement = null;
  undoCount = 0;
  renderCount = 0;
  saveCount = 0;
  selectionUiCount = 0;
  demotedHistoryCount = 0;
  minimaxDetachCount = 0;
  detachedInputCleanupCount = 0;
  edgeElements = [];
  connections.forEach((_, index) => {{
    edgeElements.push(makeEdgeElement('visual', index));
    edgeElements.push(makeEdgeElement('hit', index));
    edgeElements.push(makeEdgeElement('endpoint', index));
    edgeElements.push(makeEdgeElement('cut', index));
  }});
  rebuildRenderedRoles();
  bindConnectionEvents();
}
function hitFor(spec){{ return edgeElements.find(element => element.role === 'hit' && element.dataset.connIndex === String(spec)); }}
function clickHit(spec, extras={{}}){{ return hitFor(spec).dispatch('click', extras); }}
function press(key, target=canvasTarget){{
  const event = makeEvent(key, target);
  const handled = disconnectSelectedConnectionsFromKeyboard(event);
  return {{handled, prevented:event.prevented}};
}}
const results = {{}};

install([
  {{from:'a', to:'b', kind:'flow'}},
  {{from:'b', to:'c', kind:'flow'}},
  {{from:'c', to:'d', kind:'input'}}
], [
  {{id:'a'}}, {{id:'b', inputNodeIds:['a']}},
  {{id:'c', inputNodeIds:['b']}}, {{id:'d', inputNodeIds:['c']}}
]);
selectedId = 'b';
clickHit('1');
results.clickSelection = {{
  selected:[...selectedConnectionKeys],
  selectedId,
  selectedIds:[...selectedIds],
  selectionUiCount
}};
results.delete = press('Delete');
results.delete.state = {{
  connections:canvas.connections,
  selected:[...selectedConnectionKeys],
  roles:renderedRoles,
  undoCount,
  renderCount,
  saveCount,
  nodeCInputs:nodes.find(node => node.id === 'c').inputNodeIds
}};

install([{{from:'a', to:'b', kind:'flow'}}], [{{id:'a'}}, {{id:'b', inputNodeIds:['a']}}]);
clickHit('0');
results.backspace = press('Backspace');
results.backspace.remaining = canvas.connections.length;

install([{{from:'a', to:'b', kind:'flow'}}], [{{id:'a'}}, {{id:'b', inputNodeIds:['a']}}]);
results.noSelection = press('Delete');
results.noSelection.remaining = canvas.connections.length;
results.noSelection.undoCount = undoCount;

const editableKinds = ['textarea', 'input', 'contenteditable', 'prompt'];
results.editable = {{}};
for(const kind of editableKinds){{
  install([{{from:'a', to:'b', kind:'flow'}}], [{{id:'a'}}, {{id:'b', inputNodeIds:['a']}}]);
  clickHit('0');
  const target = makeEditableTarget(kind);
  const attempt = press(kind === 'textarea' ? 'Backspace' : 'Delete', target);
  results.editable[kind] = {{...attempt, remaining:canvas.connections.length, selected:selectedConnectionKeys.size}};
}}

install([{{from:'a', to:'b', kind:'flow'}}], [{{id:'a'}}, {{id:'b', inputNodeIds:['a']}}]);
clickHit('0');
document.activeElement = makeEditableTarget('input');
results.activeElementInput = press('Backspace', canvasTarget);
results.activeElementInput.remaining = canvas.connections.length;

install([
  {{from:'source', to:'member-a', kind:'flow'}},
  {{from:'source', to:'member-b', kind:'flow'}},
  {{from:'other', to:'target', kind:'flow'}}
], [
  {{id:'source'}}, {{id:'member-a', inputNodeIds:['source']}}, {{id:'member-b', inputNodeIds:['source']}},
  {{id:'other'}}, {{id:'target', inputNodeIds:['other']}}
]);
edgeElements = [makeEdgeElement('visual', '0,1'), makeEdgeElement('hit', '0,1'), makeEdgeElement('endpoint', '0,1'), makeEdgeElement('cut', '0,1')];
bindConnectionEvents();
clickHit('0,1');
results.mergedBefore = {{selected:selectedConnectionKeys.size, indices:selectedConnectionIndices()}};
results.mergedDelete = press('Delete');
results.mergedDelete.state = {{connections:canvas.connections, selected:selectedConnectionKeys.size, undoCount, renderCount, saveCount}};

install([
  {{from:'source', to:'group', kind:'history'}},
  {{from:'other', to:'target', kind:'flow'}}
], [
  {{id:'source'}}, {{id:'group', isHistoryGroup:true, historyFor:'source'}}, {{id:'other'}}, {{id:'target', inputNodeIds:['other']}}
]);
clickHit('0');
press('Backspace');
results.history = {{remaining:canvas.connections, demotedHistoryCount, groupIsHistory:nodes.find(node => node.id === 'group').isHistoryGroup}};

install([
  {{id:'canvas-ref-edge', from:'source', to:'target', kind:'input', data:{{origin:'canvas-reference', assetIds:['asset-1']}}}},
  {{from:'other', to:'target', kind:'input'}}
], [
  {{id:'source'}}, {{id:'other'}}, {{id:'target', inputNodeIds:['source','other'], canvasReferences:[{{id:'asset-1', materializedEdgeId:'canvas-ref-edge'}}, {{id:'asset-2'}}]}}
]);
clickHit('0');
press('Delete');
const refTarget = nodes.find(node => node.id === 'target');
results.canvasReference = {{connections:canvas.connections, inputNodeIds:refTarget.inputNodeIds, referenceIds:refTarget.canvasReferences.map(ref => ref.id)}};

install([{{from:'a', to:'b', kind:'flow'}}], [{{id:'a'}}, {{id:'b', inputNodeIds:['a']}}]);
selectedId = 'a';
selectedConnectionKeys.add(smartConnectionSelectionKey(canvas.connections[0]));
results.mixedSelection = press('Delete');
results.mixedSelection.selectedId = selectedId;
results.mixedSelection.remainingNodes = nodes.map(node => node.id);
results.mixedSelection.remainingConnections = canvas.connections.length;

console.log(JSON.stringify(results));
"""
        cls.runtime = run_node(
            harness_template.replace("{{", "{").replace("}}", "}").replace("__FUNCTIONS__", functions)
        )

    def test_01_click_selects_one_edge_and_clears_plain_node_selection(self):
        result = self.runtime["clickSelection"]
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["selectedId"], "")
        self.assertEqual(result["selectedIds"], [])
        self.assertEqual(result["selectionUiCount"], 1)

    def test_02_delete_removes_only_selected_connection_via_core_path(self):
        result = self.runtime["delete"]
        self.assertTrue(result["handled"])
        self.assertEqual(result["prevented"], 1)
        self.assertEqual(
            [(edge["from"], edge["to"]) for edge in result["state"]["connections"]],
            [("a", "b"), ("c", "d")],
        )
        self.assertEqual(result["state"]["nodeCInputs"], [])
        self.assertEqual(result["state"]["undoCount"], 1)
        self.assertEqual(result["state"]["renderCount"], 1)
        self.assertEqual(result["state"]["saveCount"], 1)

    def test_03_backspace_matches_delete(self):
        result = self.runtime["backspace"]
        self.assertTrue(result["handled"])
        self.assertEqual(result["prevented"], 1)
        self.assertEqual(result["remaining"], 0)

    def test_04_no_selected_edge_does_nothing(self):
        result = self.runtime["noSelection"]
        self.assertFalse(result["handled"])
        self.assertEqual(result["prevented"], 0)
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(result["undoCount"], 0)

    def test_05_textarea_input_contenteditable_and_prompt_never_delete_edges(self):
        for kind, result in self.runtime["editable"].items():
            with self.subTest(kind=kind):
                self.assertFalse(result["handled"])
                self.assertEqual(result["prevented"], 0)
                self.assertEqual(result["remaining"], 1)
                self.assertEqual(result["selected"], 1)

    def test_06_active_element_is_checked_even_when_event_target_is_canvas(self):
        result = self.runtime["activeElementInput"]
        self.assertFalse(result["handled"])
        self.assertEqual(result["remaining"], 1)
        editable = function_source("isEditableTarget")
        self.assertIn("matches(target) || matches(document.activeElement)", editable)

    def test_07_delete_clears_selection_and_all_removed_edge_dom_roles(self):
        state = self.runtime["delete"]["state"]
        self.assertEqual(state["selected"], [])
        self.assertEqual(len(state["roles"]), 8)
        self.assertEqual({entry["role"] for entry in state["roles"]}, {"visual", "hit", "endpoint", "cut"})
        self.assertNotIn(("b", "c"), {(entry["from"], entry["to"]) for entry in state["roles"]})

    def test_08_merged_edge_deletes_all_members_once_and_keeps_unrelated_edge(self):
        self.assertEqual(self.runtime["mergedBefore"], {"selected": 2, "indices": [0, 1]})
        state = self.runtime["mergedDelete"]["state"]
        self.assertEqual(state["connections"], [{"from": "other", "to": "target", "kind": "flow"}])
        self.assertEqual(state["selected"], 0)
        self.assertEqual(state["undoCount"], 1)
        self.assertEqual(state["renderCount"], 1)
        self.assertEqual(state["saveCount"], 1)

    def test_09_history_group_edge_uses_existing_demotion_semantics(self):
        result = self.runtime["history"]
        self.assertEqual(result["remaining"], [{"from": "other", "to": "target", "kind": "flow"}])
        self.assertEqual(result["demotedHistoryCount"], 1)
        self.assertFalse(result["groupIsHistory"])

    def test_10_canvas_reference_metadata_cleanup_is_reused(self):
        result = self.runtime["canvasReference"]
        self.assertEqual(len(result["connections"]), 1)
        self.assertEqual(result["inputNodeIds"], ["other"])
        self.assertEqual(result["referenceIds"], ["asset-2"])

    def test_11_edge_wins_over_node_in_mixed_selection(self):
        result = self.runtime["mixedSelection"]
        self.assertTrue(result["handled"])
        self.assertEqual(result["selectedId"], "a")
        self.assertEqual(result["remainingNodes"], ["a", "b"])
        self.assertEqual(result["remainingConnections"], 0)
        keydown = SMART[SMART.index("window.addEventListener('keydown', e => {"):]
        self.assertLess(
            keydown.index("disconnectSelectedConnectionsFromKeyboard(e)"),
            keydown.index("(selectedId || selectedIds.length)"),
        )

    def test_12_keyboard_delete_reuses_disconnect_connections_and_blocks_repeat(self):
        keyboard = function_source("disconnectSelectedConnectionsFromKeyboard")
        selected = function_source("disconnectSelectedConnections")
        self.assertIn("disconnectSelectedConnections()", keyboard)
        self.assertIn("disconnectConnections(indices)", selected)
        keydown = SMART[SMART.index("window.addEventListener('keydown', e => {"):]
        self.assertIn("if(isDeleteShortcut && e.repeat && !isEditableTarget(e.target))", keydown)


if __name__ == "__main__":
    unittest.main()
