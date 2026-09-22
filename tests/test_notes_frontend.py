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
            assert (
                f".{expected}=" in source or f".{expected} =" in source
            ), f"{name} does not assign the global {expected}"

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
        assert source.index("custom-blocks.js") < source.index(
            "shared.static', filename='notes.js"
        )


class TestEasyMdePathStillWorks:
    """The block editor is additive: markdown editing must keep working."""

    def test_easymde_is_still_loaded(self):
        source = template()
        assert "easymde.min.js" in source
        assert "easymde.min.css" in source

    def test_notes_js_is_still_loaded(self):
        assert "notes.js" in template()


class TestBlockEditorScript:
    """Guards for the editor lifecycle script."""

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_it_does_nothing_without_the_assets(self):
        """A missing vendor asset must degrade to the markdown editor."""
        source = self.script()
        assert "window.editorjsReady" in source
        assert "return;" in source

    def test_it_never_touches_the_markdown_editor(self):
        """The two editors have separate nodes; neither may own the other's."""
        source = self.script()
        assert "new window.EasyMDE" not in source
        assert "EasyMDE(" not in source

    def test_it_destroys_before_reinitialising(self):
        """Editor.js attaches to its holder, so a second init would stack."""
        source = self.script()
        assert "destroy()" in source

    def test_it_resolves_a_changed_index_to_a_block_id(self):
        """Editor.js reports indices; agent ops carry ids.

        Comparing the two directly would never match, and the dirty guard would
        silently stop protecting a block being edited.
        """
        source = self.script()
        assert "function idAt(" in source
        assert "dirty.add(blockId)" in source

    def test_it_does_not_overwrite_a_dirty_block(self):
        source = self.script()
        assert "dirty.has(op.block_id)" in source
        # The untouched set is what may be applied.
        assert "!dirty.has(op.block_id)" in source

    def test_it_acknowledges_after_handling_changes(self):
        source = self.script()
        assert "/api/changes/ack" in source
        assert "up_to_version" in source

    def test_a_conflicted_document_is_reloaded_not_patched(self):
        source = self.script()
        assert "body.conflicted" in source

    def test_the_save_sends_the_version(self):
        """Without it the server cannot detect a concurrent write."""
        source = self.script()
        assert "JSON.stringify({ version, blocks })" in source

    def test_it_reads_intervals_from_the_injected_settings(self):
        source = self.script()
        assert "notes-settings" in source
        assert "save_debounce_ms" in source
        assert "poll_interval_ms" in source

    def test_cancelling_a_conversion_issues_no_request(self):
        """Cancelling returns before the convert call, so nothing is requested."""
        source = self.script()
        assert source.index("await askToConvert") < source.index("/convert`")
        assert "return;" in source


class TestToolbarControls:
    def test_every_control_is_a_button_with_a_data_action(self):
        source = template()
        for action in ("export", "convert", "pin", "delete"):
            assert f'data-action="{action}"' in source

    def test_controls_start_disabled(self):
        """With no note open there is no slug, and the endpoints are scoped to one."""
        source = template()
        assert source.count("disabled") >= 4

    def test_the_status_region_is_a_live_region(self):
        """A status a screen reader never announces is not status.

        Checked on the element itself: `aria-live` appearing somewhere in the
        file would still pass if it were moved to another node.
        """
        import re

        match = re.search(r'<span id="editor-status"[^>]*>', template())
        assert match, "no status region found"
        assert 'aria-live="polite"' in match.group(0)

    def test_the_conflict_banner_is_a_live_region_and_focusable(self):
        import re

        match = re.search(r'<div id="conflict-banner"[^>]*>', template())
        assert match, "no conflict banner found"
        markup = match.group(0)
        assert 'aria-live="polite"' in markup
        assert 'tabindex="0"' in markup

    def test_toolbar_controls_use_the_shared_button_styles(self):
        """Not .btn-block, which is width:100% and would stretch every control.

        The sidebar's "New Note" button legitimately IS full width, so this
        checks the toolbar's own buttons rather than the whole file.
        """
        import re

        toolbar = re.search(r'<div id="toolbar".*?</div>', template(), flags=re.S)
        assert toolbar, "no toolbar block found"
        markup = toolbar.group(0)
        assert 'class="btn ' in markup
        assert "btn-block" not in markup
        # And every control is a real button, not a styled span.
        assert markup.count("<button") >= 4

    def test_the_note_opened_event_is_published(self):
        """It is how the block editor learns the slug without scraping the DOM."""
        source = (STATIC / "notes.js").read_text(encoding="utf-8")
        assert "wichy:note-opened" in source


class TestStyling:
    def css(self) -> str:
        return (STATIC / "notes.css").read_text(encoding="utf-8")

    def test_braces_are_balanced(self):
        """A stylesheet with an unclosed rule silently drops everything after it."""
        css = self.css()
        assert css.count("{") == css.count("}")

    def test_the_status_region_does_not_reflow(self):
        """A live region that resizes as text appears shifts the whole page."""
        assert "min-height" in self.css()

    def test_motion_is_behind_a_reduced_motion_query(self):
        css = self.css()
        assert "prefers-reduced-motion" in css

    def test_the_hidden_class_actually_hides(self):
        """`.block-editor.hidden` must be explicit: a later rule could win."""
        assert ".block-editor.hidden" in self.css()
        assert ".conflict-banner.hidden" in self.css()


class TestConversionModal:
    """The dialog must name what will be lost, and cancel must do nothing."""

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_dialog_exists_in_the_template(self):
        source = template()
        assert 'id="convert-modal"' in source
        assert 'id="convert-modal-features"' in source

    def test_it_is_a_real_dialog_not_a_confirm(self):
        """confirm() cannot list the features, which is the whole point.

        Scoped to CONVERSION: the delete confirmation legitimately uses
        confirm(), because a short yes/no needs no feature list.
        """
        source = self.script()
        convert_body = source[source.index("async function convert()") :]
        convert_body = convert_body[: convert_body.index("async function togglePin")]
        assert "window.confirm" not in convert_body
        assert "askToConvert" in convert_body

    def test_it_names_each_lossy_feature(self):
        source = self.script()
        # Each feature becomes its own list item.
        assert "convert-modal-features" in source
        assert "list.appendChild(item)" in source

    def test_features_are_inserted_as_text_not_markup(self):
        """Feature names come from the document, so they must not be parsed."""
        source = self.script()
        assert "item.textContent = feature" in source

    def test_cancelling_resolves_false(self):
        source = self.script()
        assert "cancelButton.onclick = () => finish(false)" in source

    def test_the_backdrop_and_escape_also_cancel(self):
        source = self.script()
        assert "event.target === modal" in source
        assert '"Escape"' in source

    def test_cancel_is_focused_so_a_stray_enter_cannot_convert(self):
        source = self.script()
        assert "cancelButton.focus()" in source

    def test_confirmation_is_what_issues_the_convert_request(self):
        """The convert call must sit after the confirmation, not before."""
        source = self.script()
        assert source.index("await askToConvert") < source.index("/convert`")

    def test_a_missing_dialog_refuses_rather_than_converting(self):
        """Without the dialog there is no warning, so the safe answer is no."""
        source = self.script()
        assert "resolve(false)" in source


class TestConvertButtonState:
    def test_the_button_is_disabled_rather_than_hidden(self):
        """A hidden control moves the others, and has no reachable disabled state."""
        source = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        assert "updateConvertButton" in source
        assert 'button.disabled = format !== "markdown"' in source

    def test_it_is_reset_when_a_document_opens(self):
        source = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        assert "updateConvertButton(document_.format)" in source


class TestSidebarConvertControl:
    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def test_a_markdown_row_gets_a_convert_control(self):
        source = self.script()
        assert "data-convert-slug" in source
        assert "note.format === 'markdown'" in source

    def test_handlers_are_delegated_not_per_row(self):
        """Rows are re-rendered on every refresh, so per-row handlers leak.

        Both the definition AND the call are required: a definition that is never
        invoked would leave the control dead.
        """
        source = self.script()
        assert "function initSidebar()" in source
        assert "initSidebar();" in source
        # And the delegation is registered on the LIST, not on each row.
        body = source[source.index("function initSidebar()") :]
        body = body[: body.index("function restartSaveTimer")]
        assert "notesList.addEventListener" in body

    def test_converting_from_a_row_does_not_also_select_it(self):
        source = self.script()
        assert "event.target.closest('[data-convert-slug]')" in source

    def test_the_control_is_a_real_button(self):
        source = self.script()
        assert "createElement('button')" in source
        assert "convertButton.type = 'button'" in source


class TestConvertButtonIsEnabledForMarkdown:
    def test_the_button_is_updated_before_any_early_return(self):
        """A markdown document is exactly what Convert is for.

        `open()` returns early for a non-block document, so setting the button
        only on the block path left it disabled for the one format it applies to.
        """
        source = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        body = source[source.index("async function open(") :]
        body = body[: body.index("function toEditorBlock")]
        update_at = body.index("updateConvertButton(document_.format)")
        return_at = body.index('if (document_.format !== "editorjs")')
        assert update_at < return_at, "the button is set after the markdown return"
