import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
CLASSIC_CSS = (ROOT / "static/css/canvas.css").read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
CLASSIC_HTML = (ROOT / "static/canvas.html").read_text(encoding="utf-8")
SMART_HTML = (ROOT / "static/smart-canvas.html").read_text(encoding="utf-8")


class CanvasReferencePickerContractTests(unittest.TestCase):
    def test_01_classic_has_canvas_reference_entry(self):
        self.assertIn('data-generator-reference-tab="canvas"', CLASSIC)
        self.assertIn("canvas-reference-entry", CLASSIC)
        self.assertIn("beginCanvasReferencePicker(node.id)", CLASSIC)

    def test_02_smart_has_canvas_reference_entry(self):
        self.assertIn("data-mention-canvas-reference", SMART)
        self.assertIn("beginSmartCanvasReferencePicker(target.id)", SMART)

    def test_03_classic_persists_soft_references(self):
        self.assertIn("target.canvasReferences = picker.refs.map", CLASSIC)

    def test_04_smart_persists_soft_references(self):
        self.assertIn("target.canvasReferences = picker.refs.map", SMART)

    def test_05_classic_soft_reference_has_stable_fields(self):
        for field in ("sourceNodeId", "assetId", "assetIndex", "assetPath", "originalUrl", "fileName", "sha256", "order", "materializedEdgeId"):
            self.assertIn(f"{field}:", CLASSIC)

    def test_06_smart_soft_reference_has_stable_fields(self):
        for field in ("sourceNodeId", "assetId", "assetIndex", "assetPath", "originalUrl", "fileName", "sha256", "order", "materializedEdgeId"):
            self.assertIn(f"{field}:", SMART)

    def test_07_classic_rejects_transient_urls_for_storage(self):
        self.assertIn("/^(blob:|data:)/i.test(raw)", CLASSIC)

    def test_08_smart_rejects_transient_urls_for_storage(self):
        self.assertIn("/^(blob:|data:)/i.test(raw)", SMART)

    def test_09_classic_materializes_provider_tagged_edges(self):
        self.assertIn("async function materializeCanvasReferenceEdges", CLASSIC)
        self.assertIn("origin:'canvas-reference'", CLASSIC)

    def test_10_smart_materializes_provider_tagged_edges(self):
        self.assertIn("async function materializeSmartCanvasReferenceEdges", SMART)
        self.assertIn("origin:'canvas-reference'", SMART)

    def test_11_classic_groups_assets_per_source_edge(self):
        self.assertIn("assetIds:entries.map", CLASSIC)
        self.assertIn("assetIndexes:entries.map", CLASSIC)

    def test_12_smart_groups_assets_per_source_edge(self):
        self.assertIn("assetIds:entries.map", SMART)
        self.assertIn("assetIndexes:entries.map", SMART)

    def test_13_classic_uses_center_anchors(self):
        self.assertIn("centeredConnectionAnchors(sourceNodeId, targetNode.id)", CLASSIC)

    def test_14_smart_uses_center_anchors(self):
        self.assertIn("centeredSmartConnectionAnchors(sourceNodeId, targetNode.id)", SMART)

    def test_15_classic_blocks_cycles_before_selection_and_run(self):
        self.assertGreaterEqual(CLASSIC.count("wouldCreateGeneratorCycle"), 3)
        self.assertIn("会形成环路", CLASSIC)

    def test_16_smart_blocks_cycles_before_selection_and_run(self):
        self.assertGreaterEqual(SMART.count("wouldCreateSmartCanvasReferenceCycle"), 2)
        self.assertIn("会形成环路", SMART)

    def test_17_classic_reads_model_reference_limit(self):
        self.assertIn("/api/image-params?provider_id=", CLASSIC)
        self.assertIn("reference_image_limit", CLASSIC)

    def test_18_smart_reads_model_reference_limit(self):
        self.assertIn("smartCanvasReferenceLimitFor", SMART)
        self.assertIn("reference_image_limit", SMART)

    def test_19_classic_validates_before_paid_task(self):
        run = CLASSIC[CLASSIC.index("async function runGenerator("):CLASSIC.index("async function midjourneyRequest(")]
        self.assertLess(run.index("await materializeCanvasReferenceEdges(gen.id)"), run.index("createCanvasImageTask(payload"))

    def test_20_smart_validates_before_paid_task(self):
        run = SMART[SMART.index("async function runGeneration("):SMART.index("async function runPromptLLMNode(")]
        self.assertLess(run.index("await materializeSmartCanvasReferenceEdges(node.id"), run.index("await runApiGeneration(prompt, refs"))

    def test_21_classic_does_not_silently_slice_reference_request(self):
        self.assertIn("reference_images:refs\n", CLASSIC)

    def test_22_smart_does_not_silently_slice_reference_request(self):
        self.assertIn("reference_images:imageRefs", SMART)

    def test_23_classic_auto_edge_delete_removes_soft_reference(self):
        self.assertIn("removed?.data?.origin === 'canvas-reference'", CLASSIC)

    def test_24_smart_auto_edge_delete_removes_soft_reference(self):
        self.assertIn("conn.data?.origin === 'canvas-reference'", SMART)

    def test_25_classic_supports_remove_and_reorder(self):
        self.assertIn("removeCanvasReference(node", CLASSIC)
        self.assertIn("reorderCanvasReference(node", CLASSIC)

    def test_26_smart_supports_remove_and_reorder(self):
        self.assertIn("removeSmartCanvasReference(node", SMART)
        self.assertIn("reorderSmartCanvasReference(node", SMART)

    def test_27_both_can_cancel_with_escape_or_blur(self):
        self.assertIn("e.key === 'Escape' && canvasReferencePicker", CLASSIC)
        self.assertIn("if(canvasReferencePicker) cancelCanvasReferencePicker()", CLASSIC)
        self.assertIn("e.key === 'Escape' && smartCanvasReferencePicker", SMART)
        self.assertIn("if(smartCanvasReferencePicker) cancelSmartCanvasReferencePicker()", SMART)
        self.assertIn("function performRedo()", CLASSIC)
        self.assertIn("function performRedo()", SMART)
        self.assertIn("if(e.shiftKey) performRedo()", CLASSIC)
        self.assertIn("if(e.shiftKey) performRedo()", SMART)

    def test_28_both_render_banner_and_order_overlay(self):
        for css in (CLASSIC_CSS, SMART_CSS):
            self.assertIn(".canvas-reference-banner", css)
            self.assertIn(".canvas-reference-order", css)
            self.assertIn(".canvas-reference-selected", css)

    def test_29_overlay_does_not_change_source_opacity(self):
        for css in (CLASSIC_CSS, SMART_CSS):
            reference_rules = "\n".join(line for line in css.splitlines() if "canvas-reference" in line)
            self.assertNotIn("opacity:0", reference_rules)

    def test_30_both_mark_thumbnail_source_as_canvas(self):
        self.assertIn("canvas-reference-source-badge", CLASSIC)
        self.assertIn("canvas-reference-source-badge", SMART)

    def test_31_classic_entry_is_in_reference_source_tabs(self):
        for tab in ('input', 'asset', 'canvas'):
            self.assertIn(f'data-generator-reference-tab="{tab}"', CLASSIC)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', CLASSIC_CSS)

    def test_32_smart_entry_is_in_shared_mention_source_tabs(self):
        self.assertIn('data-mention-source="input"', SMART)
        self.assertIn('data-mention-source="asset"', SMART)
        self.assertIn('data-mention-canvas-reference', SMART)
        self.assertIn("beginSmartCanvasReferencePicker(target.id)", SMART)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', SMART_CSS)

    def test_33_canvas_entry_assets_are_cache_busted(self):
        self.assertRegex(CLASSIC_HTML, r'/static/css/canvas\.css\?v=[^"\s]+')
        self.assertRegex(CLASSIC_HTML, r'/static/js/canvas\.js\?v=[^"\s]+')
        self.assertRegex(SMART_HTML, r'/static/css/smart-canvas\.css\?v=[^"\s]+')
        self.assertRegex(SMART_HTML, r'/static/js/smart-canvas\.js\?v=[^"\s]+')
        self.assertNotIn('canvas.js?v=2026.08.04.1787982726', CLASSIC_HTML)
        self.assertNotIn('smart-canvas.js?v=2026.08.04.1787982875', SMART_HTML)


if __name__ == "__main__":
    unittest.main()
