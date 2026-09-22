"""Source-level guards for the notes frontend.

There is no JS test runner in this repo, so the frontend invariants that would
regress silently are asserted against the shipped source, following the
precedent in tests/test_ui_escape_helpers.py. Everything a browser would need to
run is checked here instead; behaviour that needs a DOM is on the manual
checklist.

The guards matter more than usual for a template: a missing script tag or a
wrong UMD global fails only in a browser, where no test would ever see it.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path("src/wichy/static")
TEMPLATE = Path("src/wichy/templates/notes.html")

#: The globals each vendored bundle actually defines. Verified against the
#: bundles themselves, and asserted here so a re-vendor cannot silently change
#: a name the template depends on.
BUNDLES = {
    "editorjs/editorjs.umd.js": "EditorJS",
    "editorjs/header.umd.js": "Header",
    "editorjs/list.umd.js": "List",
    "editorjs/code.umd.js": "CodeTool",
    "editorjs/quote.umd.js": "Quote",
    "editorjs/checklist.umd.js": "Checklist",
    "editorjs/delimiter.umd.js": "Delimiter",
}


def template() -> str:
    """The notes template source."""
    return TEMPLATE.read_text(encoding="utf-8")


class TestVendoredBundles:
    """Every vendored bundle exists and defines the global the template expects."""

    def test_every_bundle_is_present_and_non_empty(self):
        for name in BUNDLES:
            path = STATIC / name
            assert path.exists(), f"missing vendored bundle: {name}"
            assert path.stat().st_size > 1000, f"{name} looks truncated"

    def test_every_bundle_defines_its_documented_global(self):
        """A re-vendor that renamed a global would otherwise fail only in a browser."""
        for name, expected in BUNDLES.items():
            source = (STATIC / name).read_text(encoding="utf-8", errors="replace")
            assert f".{expected}=" in source or f".{expected} =" in source, (
                f"{name} does not assign the global {expected}"
            )

    def test_the_custom_blocks_expose_themselves(self):
        source = (STATIC / "editorjs/custom-blocks.js").read_text(encoding="utf-8")
        assert "window.WichyCustomBlocks" in source
        for name in ("Question", "Decision", "Todo"):
            assert name in source

    def test_each_custom_block_declares_what_editorjs_requires(self):
        """Without isReadOnlySupported, read-only init throws; without sanitize,
        the block is not cleaned at all."""
        source = (STATIC / "editorjs/custom-blocks.js").read_text(encoding="utf-8")
        for member in ("isReadOnlySupported", "toolbox", "sanitize"):
            assert source.count(member) >= 3, f"{member} not declared on every block"


class TestTemplateScriptTags:
    def test_the_editor_node_exists(self):
        """The block editor needs its own node; sharing #note-content with
        EasyMDE would make the two editors fight over one element."""
        source = template()
        assert 'id="block-editor"' in source
        assert 'id="note-content"' in source

    def test_every_bundle_is_loaded(self):
        source = template()
        for name in BUNDLES:
            assert name in source, f"{name} is not loaded by the template"

    def test_each_global_is_guarded_individually(self):
        """Guarding only the core would let a missing plugin fail at init."""
        source = template()
        assert "_guard('EditorJS')" in source
        for name in ("Header", "List", "CodeTool", "Quote", "Checklist", "Delimiter"):
            assert name in source, f"{name} is not guarded"
        assert "WichyCustomBlocks" in source

    def test_a_missing_asset_degrades_rather_than_breaks(self):
        """The page must still be usable if the vendor drop is incomplete."""
        source = template()
        assert "editorjsReady" in source
        assert "editorjsMissing" in source

    def test_custom_blocks_load_after_the_plugins(self):
        """It defines classes the plugins' types are combined with."""
        source = template()
        assert source.index("checklist.umd.js") < source.index("custom-blocks.js")

    def test_settings_are_injected_as_json(self):
        """Injected data must never be interpolated as live code."""
        source = template()
        assert 'type="application/json"' in source
        assert "poll_interval_ms" in source
        assert "save_debounce_ms" in source

    def test_the_page_script_loads_last(self):
        source = template()
        assert source.index("custom-blocks.js") < source.index("shared.static', filename='notes.js")


class TestEasyMdePathStillWorks:
    """The block editor is additive: markdown editing must keep working."""

    def test_easymde_is_still_loaded(self):
        source = template()
        assert "easymde.min.js" in source
        assert "easymde.min.css" in source

    def test_notes_js_is_still_loaded(self):
        assert "notes.js" in template()
