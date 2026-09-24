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

    def test_it_reads_the_block_id_from_the_change_event(self):
        """The event carries `detail.target.id`; anything else matches nothing.

        This assertion used to require `idAt(index)`, reading `event.block.id`
        and insisting it be a number. Editor.js emits neither: it passes
        `event.detail.target.id`, a string, so the dirty set stayed empty and the
        guard that protects a block being edited never armed.
        """
        source = self.script()
        assert "one.detail.target.id" in source
        assert "function changedBlockIds(" in source
        assert "dirty.add(blockId)" in source
        # And the shape it used to require is gone.
        assert "event?.block?.id" not in source
        assert "function idAt(" not in source

    def test_it_handles_a_batch_of_change_events(self):
        """Several blocks changed in one tick arrive as an array."""
        source = self.script()
        body = source[source.index("function changedBlockIds") :]
        body = body[: body.index("    /**\n     * Apply queued agent changes")]
        assert "Array.isArray(event) ? event : [event]" in body

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


class TestTheming:
    """The page is deliberately light-only.

    It used to declare its own dark palette, but the EasyMDE/CodeMirror editor
    has no dark theme to match: dark chrome surrounded a solid white editor and
    the page read as broken. The user asked for removal rather than completion,
    so the page now pins the shared light tokens and suppresses the base
    template's theme script.
    """

    def css(self) -> str:
        return (STATIC / "notes.css").read_text(encoding="utf-8")

    def test_the_page_pins_light(self):
        """color-scheme names light only, so browser widgets match the page."""
        source = template()
        assert '<meta name="color-scheme" content="light" />' in source

    def test_the_theme_script_is_suppressed(self):
        """A theme attribute with no dark palette behind it would half-darken."""
        source = template()
        assert "{% block theme_script %}{% endblock theme_script %}" in source

    def test_no_dark_palette_is_declared(self):
        """The page states light tokens under a dark attribute rather than
        declaring dark values: the palette is a pin, not a theme."""
        import re

        css = self.css()
        match = re.search(r'\[data-theme="dark"\][^{]*\{([^}]*)\}', css)
        assert match, "no light-pin rule found"
        palette = match.group(1)
        for token in ("--surface", "--background", "--border", "--text"):
            assert token in palette, f"{token} is not pinned"
        # No dark values anywhere: every declared value is the shared light one.
        for dark_value in ("#1b1f2a", "#12151d", "#2e3444", "#e6e8ee"):
            assert dark_value not in css, f"dark value {dark_value} came back"

    def test_no_editor_chrome_dark_overrides_remain(self):
        """The partial Editor.js dark rules are gone with the palette."""
        css = self.css()
        for selector in (".ce-toolbar__plus", ".ce-popover", ".ce-inline-toolbar"):
            assert f'[data-theme="dark"] {selector}' not in css, selector

    def test_braces_remain_balanced(self):
        assert self.css().count("{") == self.css().count("}")


class TestAccessibility:
    def css(self) -> str:
        return (STATIC / "notes.css").read_text(encoding="utf-8")

    def test_reduced_motion_disables_the_flash(self):
        """The mark still appears; only the animation is dropped."""
        css = self.css()
        assert "prefers-reduced-motion: reduce" in css
        block = css[css.index("prefers-reduced-motion: reduce") :]
        assert "animation: none" in block

    def test_focus_is_visible_in_the_editor(self):
        """Keyboard navigation must show where focus is."""
        assert "focus-visible" in self.css()

    def test_the_pinned_marker_has_a_text_cue(self):
        """Not colour alone: the state is spelled out."""
        css = self.css()
        assert "pinned" in css
        assert 'content: " pinned"' in css

    def test_every_custom_block_has_a_text_label(self):
        """Their meaning must not depend on their colour."""
        source = (STATIC / "editorjs/custom-blocks.js").read_text(encoding="utf-8")
        for label in ("[QUESTION", "[DECISION]", "[TODO "):
            assert label in source, label

    def test_the_conflict_banner_states_the_conflict_in_words(self):
        source = template()
        assert "Agent also edited this block" in source

    def test_the_modal_has_a_labelled_title(self):
        source = template()
        assert 'aria-labelledby="convert-modal-title"' in source
        assert 'id="convert-modal-title"' in source

    def test_the_modal_declares_itself_a_dialog(self):
        source = template()
        marker = source[source.index('id="convert-modal"') :]
        marker = marker[: marker.index(">")]
        assert 'role="dialog"' in marker
        assert 'aria-modal="true"' in marker


class TestTheMarkIsAppliedToTheBlock:
    """An agent edit must be visible on the block, not only counted."""

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_touched_blocks_are_actually_marked(self):
        """Recording the ids is not showing them."""
        source = self.script()
        assert "function markAgentBlocks()" in source
        assert "markAgentBlocks();" in source
        assert 'classList.add("agent-touched")' in source

    def test_the_flash_is_separate_from_the_mark(self):
        """The mark must outlive the animation."""
        source = self.script()
        assert 'classList.add("agent-flash")' in source
        assert 'classList.remove("agent-flash")' in source

    def test_marking_matches_on_id_not_index(self):
        """Indices shift as blocks are added or removed."""
        source = self.script()
        body = source[source.index("function markAgentBlocks()") :]
        body = body[: body.index("function showConflict")]
        assert "agentTouched.has(id)" in body

    def test_the_mark_reads_ids_without_writing_to_the_block_dom(self):
        """The ids come from the editor, never from an attribute we wrote.

        Writing `data-block-id` onto the rendered block made Editor.js report the
        write back as a user edit, so onChange scheduled a save, whose re-stamp
        fired onChange again: the page PUT the document once per debounce interval
        forever with no user input. Read-only id lookup is what removes that loop.
        """
        source = self.script()
        assert "stampBlockIds" not in source
        assert "dataset.blockId = " not in source
        body = source[source.index("function markAgentBlocks()") :]
        body = body[: body.index("function showConflict")]
        assert "blocks[index]" in body

    def test_a_save_that_would_change_nothing_is_skipped(self):
        """The editor reports its own DOM writes as changes, so idle must be free.

        Without this guard the page saved in a loop and the version ran away.
        """
        source = self.script()
        save_body = source[source.index("async function save()") :]
        save_body = save_body[: save_body.index("function scheduleSave")]
        assert "diffAgainstSnapshot(blocks).length" in save_body

    def test_the_mark_carries_a_tooltip(self):
        """A border alone does not say what happened."""
        source = self.script()
        assert "Changed by the agent" in source


class TestTheBrowserSendsTheUserChanges:
    """The browser -> agent direction exists at all.

    This is the guard that was missing. Every other guard in this file passed
    while the page sent nothing: they asserted the shape of code that ran, and
    none asserted that the one network call the whole feature depends on is
    actually made. A shape guard cannot see an absent call, so the assertions
    here are deliberately about the call and its payload.
    """

    def script(self) -> str:
        """The block editor source."""
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_script_posts_to_the_changes_endpoint(self):
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("function updateQueueIndicator")]
        assert 'method: "POST"' in body
        assert "/api/changes" in body

    def test_the_ops_it_sends_carry_no_author(self):
        """Authorship is the server's to stamp, not the client's to claim.

        The browser used to send `author: "user"` per op, and the server filtered
        on it. That made the user-to-agent direction a convention: a crafted POST
        could forge the field either way. The field is gone, and the route itself
        is now the statement that these are the user's edits.
        """
        source = self.script()
        assert 'author: "user"' not in source
        assert "author:" not in source

    def test_the_payload_carries_the_version_the_op_was_computed_against(self):
        """The server checks it, and a wrong version is a 409."""
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("function updateQueueIndicator")]
        # The exact expression, not merely the name: `version` alone also appears
        # in the body, so a looser assertion passes when the wrong one is sent.
        assert "version: pendingVersion.get(targetSlug)," in body

    def test_the_parked_version_is_the_one_the_save_produced(self):
        """Recording the pre-save version raced the save and 409'd the notify."""
        source = self.script()
        body = source[source.index("async function flushChangeNotify") :]
        body = body[: body.index("async function sendPendingOps")]
        assert body.index("await save()") < body.index(
            "pendingVersion.set(slug, version)"
        )

    def test_the_diff_is_taken_against_a_snapshot_not_the_dirty_set(self):
        """The dirty set holds indexes; ops need ids, adds, removes and order."""
        source = self.script()
        assert "function diffAgainstSnapshot" in source
        assert "sentSnapshot" in source
        body = source[source.index("function diffAgainstSnapshot") :]
        body = body[: body.index("async function resyncSnapshot")]
        assert '"remove"' in body
        assert '"add"' in body
        assert '"move"' in body
        assert '"update"' in body

    def test_the_snapshot_advances_only_after_the_error_branches(self):
        """Advancing it on a failed POST would lose the change silently."""
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("function updateQueueIndicator")]
        # The error branch must RETURN, so the resync below it is unreachable
        # when the POST failed. Asserting only that a resync exists somewhere
        # passes even when it runs before the failure is handled.
        error_branch = body[body.index("if (result.error)") :]
        assert "return false;" in error_branch
        assert error_branch.index("return false;") < body.index("resyncSnapshot")

    def test_a_503_keeps_the_ops_for_the_next_poll(self):
        """No active session is transient; dropping the ops loses the edit."""
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("function updateQueueIndicator")]
        error_branch = body[body.index("if (result.error)") :]
        assert "HTTP 503" in error_branch
        assert "pendingOps.delete" not in error_branch[: error_branch.index("HTTP 409")]

    def test_the_send_control_is_hidden_when_nothing_is_queued(self):
        """An empty send would inject a message describing no change.

        See TestTheQueueControl for the selector this visibility acts on: the
        logic was always right, and the element it was applied to did not exist.
        """
        source = self.script()
        body = source[source.index("function updateQueueIndicator") :]
        body = body[: body.index("    /**\n     * Note which blocks the user touched.")]
        assert 'button.classList.toggle("hidden", empty);' in body
        assert "button.disabled = empty;" in body
        assert "const empty = distinct === 0;" in body

    def test_the_notify_runs_after_the_save(self):
        """Posting before the save raced it and every notify came back 409."""
        source = self.script()
        body = source[source.index("async function flushChangeNotify") :]
        body = body[: body.index("async function sendPendingOps")]
        assert body.index("await save()") < body.index("sendPendingOps(slug)")

    def test_the_client_adopts_the_version_the_agent_change_produced(self):
        """Otherwise every later save and notify is stale forever."""
        source = self.script()
        body = source[source.index("async function poll") :]
        body = body[: body.index("async function open")]
        assert "version = body.version" in body

    def test_an_agent_op_is_written_into_the_editor_not_just_marked(self):
        """Marking alone shows a border on content the agent never changed."""
        source = self.script()
        assert "async function applyOneOp" in source
        body = source[source.index("async function applyOneOp") :]
        body = body[: body.index("function showConflict")]
        assert "blocks.update" in body
        assert "blocks.insert" in body
        assert "blocks.delete" in body
        assert "blocks.move" in body

    def test_on_demand_mode_does_not_send_without_the_user(self):
        source = self.script()
        assert 'const AUTO_SEND = NOTIFICATION_MODE === "auto";' in source
        body = source[source.index("async function flushChangeNotify") :]
        body = body[: body.index("async function sendPendingOps")]
        assert "if (AUTO_SEND) {" in body
        assert "await sendPendingOps(slug);" in body

    def test_the_mode_and_debounce_come_from_the_injected_settings(self):
        """The settings were injected and never read; that is what made them dead."""
        source = self.script()
        assert "SETTINGS.change_debounce_ms" in source
        assert "SETTINGS.notification_mode" in source
        assert "CHANGE_DEBOUNCE_MS" in source


class TestAgentChangeVisualization:
    """The 9.2 marks, the toast, and the scope of the undo.

    Every assertion here pins the one thing that makes the feature honest: the
    meaning is carried by text, the count is blocks rather than ops, and an undo
    says what it will undo before it does it.
    """

    def script(self) -> str:
        """The block editor source."""
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_mark_is_carried_by_text_not_colour_alone(self):
        """A monochrome display, or no icon font, must still convey it."""
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        assert "[AGENT]" in css
        assert "[AGENT NEW]" in css
        source = self.script()
        assert "Changed by the agent" in source
        assert "Created by agent" in source

    def test_a_created_block_is_marked_differently_from_an_edited_one(self):
        source = self.script()
        assert "agentCreated" in source
        body = source[source.index("async function applyAgentChanges") :]
        body = body[: body.index("async function applyOneOp")]
        assert 'op.op === "add"' in body

    def test_the_tooltip_carries_a_timestamp(self):
        source = self.script()
        assert "function clockNow" in source
        assert "at ${time}" in source

    def test_the_status_line_counts_distinct_blocks_not_ops(self):
        """Five ops on one block is one block, and the marks show one block."""
        source = self.script()
        body = source[source.index("async function applyAgentChanges") :]
        body = body[: body.index("async function applyOneOp")]
        assert "new Set(untouched.map((op) => op.block_id)).size" in body

    def test_there_is_no_popup_notice(self):
        """The status line and the marks say it; a popup said it again."""
        assert "agent-toast" not in template()
        assert "showAgentToast" not in self.script()

    def test_a_status_message_survives_the_next_poll(self):
        """The poll runs every 2s and would wipe a notice before it is read."""
        source = self.script()
        assert "function holdStatus" in source
        body = source[source.index("async function poll") :]
        body = body[: body.index("async function open")]
        assert "Date.now() >= statusHoldUntil" in body

    def test_the_busy_notice_names_what_happens_to_the_users_edits(self):
        source = self.script()
        assert "next thinking" in source

    def test_the_flash_respects_reduced_motion(self):
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css
        # The animation is declared inside a no-preference block, so the mark
        # survives for someone who has asked for less motion.
        assert "@media (prefers-reduced-motion: no-preference)" in css


class TestTheMarksAreExplained:
    """The two marks need a stated meaning, not only a hover tooltip."""

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_legend_names_both_marks(self):
        body = template()
        assert 'id="agent-legend"' in body
        assert "[AGENT]" in body
        assert "[AGENT NEW]" in body

    def test_the_legend_says_what_each_mark_means_in_words(self):
        body = template()
        legend = body[body.index('id="agent-legend"') :]
        legend = legend[: legend.index("</span>\n")]
        assert "changed by the agent" in legend
        assert "created by the agent" in legend

    def test_the_legend_starts_hidden(self):
        """It must not describe a state the document is not in."""
        body = template()
        legend = body[body.index('id="agent-legend"') :]
        assert "hidden" in legend[: legend.index(">")]

    def test_the_mark_is_anchored_to_the_text_column_not_the_block_edge(self):
        """The block spans the full editor width while its content is a centred
        column, so a mark on the outer edge sits far from the text it describes,
        and -- at the block's vertical spacing -- looks attached to the block
        above it. It must be applied to the content wrapper."""
        source = self.script()
        body = source[source.index("async function markAgentBlocks") :]
        body = body[: body.index("function describeAgentChange")]
        assert 'element.querySelector(".ce-block__content")' in body
        # And the CSS must key off that wrapper, or the class lands on an
        # element the stylesheet does not target.
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        assert ".ce-block__content.agent-touched" in css
        # No rule may target the outer block edge for this mark.
        assert ".ce-block.agent-touched" not in css

    def test_the_editor_column_is_wider_than_the_vendor_default(self):
        """Editor.js caps its content at 650px and centres it, leaving most of a
        wide panel empty -- and widening that margin pushed the marks further
        from the text."""
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        body = css[css.index(".block-editor .codex-editor__redactor") :]
        body = body[: body.index("}")]
        assert "max-width" in body
        # Centred, or the saving shows up as empty space on one side only.
        assert "margin: 0 auto" in body
        # A measure, not the vendor's cap: 650 is what is being overridden.
        assert "650px" not in body

    def test_the_legend_is_shown_and_hidden_with_the_marks(self):
        source = self.script()
        body = source[source.index("function updateAgentLegend") :]
        body = body[: body.index("/**")] if "/**" in body else body
        assert "agentTouched.size === 0" in body


class TestTheHistoryBrowser:
    """The toolbar's History control opens a read-only revision browser.

    It replaces the per-agent-undo control: the revision log holds EVERY
    revision, so browsing it and restoring is both more general and more
    honest than a button that silently meant "the agent's last change".
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_toolbar_offers_history(self):
        body = template()
        assert 'data-action="history"' in body

    def test_it_is_disabled_for_a_markdown_note(self):
        """A markdown note keeps no revision log, so history is not on offer."""
        body = template()
        button = body[body.index('data-action="history"') :]
        button = button[: button.index(">")]
        assert "disabled" in button
        assert 'format !== "editorjs"' in self.script()

    def test_it_lists_revisions_newest_first(self):
        """The server returns them newest first; the list must not re-sort."""
        body = self.script()
        body = body[body.index("function renderHistoryList") :]
        body = body[: body.index("function clearHistoryDetail")]
        assert "for (const entry of historyEntries)" in body

    def test_selecting_a_revision_shows_its_content(self):
        body = self.script()
        body = body[body.index("async function selectHistoryRevision") :]
        body = body[: body.index("function renderBlocksAsText")]
        assert "response.blocks" in body

    def test_an_unbuildable_revision_is_stated_not_hidden(self):
        """A partial replay produces a state that looks real but is not."""
        body = self.script()
        body = body[body.index("async function selectHistoryRevision") :]
        body = body[: body.index("function renderBlocksAsText")]
        assert "response.complete === false" in body
        assert "history-incomplete" in body

    def test_restore_is_refused_for_an_unbuildable_revision(self):
        body = self.script()
        body = body[body.index("async function selectHistoryRevision") :]
        body = body[: body.index("function renderBlocksAsText")]
        assert "restore.disabled = response.complete === false" in body

    def test_restore_states_its_scope_before_acting(self):
        """It replaces the WHOLE document; the dialog must say so first."""
        source = self.script()
        body = source[source.index("async function restoreSelectedRevision") :]
        body = body[: body.index("function closeHistory")]
        assert "window.confirm" in body
        assert "replaces the whole document" in body
        confirm_at = body.index("window.confirm")
        assert confirm_at < body.index("if (!confirmed)")
        assert body.index("if (!confirmed)") < body.index("/restore")

    def test_a_refused_restore_issues_no_request(self):
        source = self.script()
        body = source[source.index("async function restoreSelectedRevision") :]
        body = body[: body.index("function closeHistory")]
        guard_at = body.index("if (!confirmed)")
        assert guard_at < body.index("/restore")

    def test_restore_posts_to_the_restore_endpoint(self):
        """Not /revert: that one means "undo this change", one revision earlier."""
        source = self.script()
        body = source[source.index("async function restoreSelectedRevision") :]
        body = body[: body.index("function closeHistory")]
        assert "/restore" in body
        assert "/revert" not in body

    def test_switching_notes_closes_the_browser(self):
        """A modal left up would describe the previous document's history."""
        body = self.script()
        body = body[body.index("async function destroyEditor") :]
        assert "closeHistory()" in body


class TestTheConflictBannerResolves:
    """SPEC 9.3's three actions, and the rule that only one of them loses work."""

    def script(self) -> str:
        """The block editor source."""
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_banner_offers_all_three_actions(self):
        body = template()
        for action in ("mine", "theirs", "compare"):
            assert f'data-conflict="{action}"' in body

    def test_the_banner_is_inert_until_a_button_is_pressed(self):
        """Showing it must not discard anything: that is the safe default."""
        source = self.script()
        body = source[source.index("function showConflict") :]
        body = body[: body.index("async function keepMine")]
        assert "classList.remove" in body or "classList.add" in body
        # No destructive call in the display path.
        assert "open(" not in body
        assert "pendingOps.delete" not in body

    def test_keep_mine_acknowledges_so_the_ops_do_not_return(self):
        """Dropping them locally is not enough; they live on the server."""
        source = self.script()
        body = source[source.index("async function keepMine") :]
        body = body[: body.index("async function keepTheirs")]
        assert "/api/changes/ack" in body
        assert "up_to_version" in body

    def test_keep_theirs_confirms_before_discarding(self):
        """It is the only action that loses local work, so it must ask first."""
        source = self.script()
        body = source[source.index("async function keepTheirs") :]
        body = body[: body.index("async function compareConflict")]
        assert "window.confirm" in body
        assert body.index("window.confirm") < body.index("await open(slug)")

    def test_refusing_keep_theirs_leaves_the_banner_up(self):
        source = self.script()
        body = source[source.index("async function keepTheirs") :]
        body = body[: body.index("async function compareConflict")]
        refused = body[body.index("if (!confirmed)") :]
        assert "return;" in refused
        # The banner is only hidden AFTER the confirmation, so a refusal keeps it.
        assert body.index("hideConflict()") > body.index("if (!confirmed)")

    def test_keep_theirs_does_nothing_when_there_is_no_conflict(self):
        """A stray click must not promise to discard '0 blocks'."""
        source = self.script()
        body = source[source.index("async function keepTheirs") :]
        body = body[: body.index("async function compareConflict")]
        assert "!pendingConflicts.length" in body

    def test_compare_shows_both_versions_and_resolves_neither(self):
        source = self.script()
        body = source[source.index("async function compareConflict") :]
        body = body[: body.index("async function describeBlockForCompare")]
        assert "compare-mine" in body
        assert "compare-theirs" in body
        # It must not hide the banner or apply either side.
        assert "hideConflict" not in body
        assert "keepMine" not in body and "keepTheirs" not in body

    def test_compare_renders_as_text_not_markup(self):
        """Block content is document data and may contain markup."""
        source = self.script()
        body = source[source.index("async function compareConflict") :]
        body = body[: body.index("async function describeBlockForCompare")]
        assert "mine.textContent = " in body
        assert "theirs.textContent = " in body
        # The assignment, not the word: the comment above it names innerHTML to
        # explain why it is not used.
        assert ".innerHTML = " not in body

    def test_the_local_side_is_read_through_save_not_a_data_property(self):
        """The block wrapper has no `data` getter, so block.data is undefined."""
        source = self.script()
        body = source[source.index("async function describeBlockForCompare") :]
        body = body[: body.index("function describeOpForCompare")]
        assert ".save()" in body
        assert "block.data" not in body

    def test_a_conflicted_block_gets_its_own_border_class(self):
        source = self.script()
        assert 'classList.toggle("conflicted"' in source
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        assert ".block-editor .ce-block.conflicted" in css

    def test_the_conflicted_border_is_orange_not_the_agent_purple(self):
        """The two states must be distinguishable at a glance."""
        css = (STATIC / "notes.css").read_text(encoding="utf-8")
        conflicted = css[css.index(".block-editor .ce-block.conflicted") :]
        conflicted = conflicted[: conflicted.index("}")]
        assert "warning" in conflicted
        assert "a855f7" not in conflicted

    def test_the_banner_states_the_conflict_in_words(self):
        body = template()
        assert "Agent also edited this block" in body


class TestTheQueueControl:
    """On-demand mode is unusable without a control that can actually be found.

    The control was looked up as `#send-changes`, an id the toolbar does not use:
    every button there is identified by `data-action`. The lookup matched nothing,
    the function returned early, and the queue indicator never appeared -- so
    queued edits could not be sent by hand at all. Source guards did not catch it
    because they asserted the visibility LOGIC, which was correct; what was wrong
    was the selector it was applied to.
    """

    def script(self) -> str:
        """The block editor source."""
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_queue_control_is_found_by_its_data_action(self):
        source = self.script()
        body = source[source.index("function updateQueueIndicator") :]
        body = body[: body.index("    /**\n     * Apply queued agent changes")]
        assert "document.querySelector('[data-action=\"send-changes\"]')" in body
        assert 'document.getElementById("send-changes")' not in body

    def test_the_every_selector_the_script_uses_exists_in_the_template(self):
        """A selector naming an element that is not there fails silently.

        This is the general form of the defect above: the template and the script
        are edited in different files, and a mismatch shows up as a missing
        control rather than an error.
        """
        source = self.script()
        body = template()
        import re

        ids = set(re.findall(r'getElementById\("([^"]+)"\)', source))
        actions = set(re.findall(r'querySelector\(.\[data-action="([^"]+)"\]', source))
        for element_id in ids:
            if element_id in {"notes-settings"}:
                continue
            assert f'id="{element_id}"' in body, f"#{element_id} is not in the template"
        for action in actions:
            assert f'data-action="{action}"' in body, f"data-action={action} missing"

    def test_the_clear_control_is_gone(self):
        """It discarded the queue while the sent snapshot still lagged.

        The next edit then recomputed the identical ops, so the control did
        nothing except lie about it. The queue is derived from the snapshot, so
        the honest fix was to remove it rather than to fake it.
        """
        source = self.script()
        assert "clear-changes" not in source
        assert "clear-changes" not in template()

    def test_the_toolbar_handles_send_before_the_slug_guard(self):
        """A queue control that no-ops when no note is open is dead code."""
        source = self.script()
        body = source[source.index("function initToolbar") :]
        body = body[: body.index("function askToConvert")]
        assert 'action === "send-changes"' in body


class TestTheLegacyMarkdownPage:
    """The markdown page must read the payload the API actually sends.

    It used to read `note.title` and `note.content` from a payload shaped
    `{meta, blocks, format}` and PUT `{title, content}` to an endpoint that
    requires `version` plus `blocks`. Both halves failed: the page rendered an
    empty title and body, and every save was rejected -- with the only trace in
    the browser console.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def select_body(self) -> str:
        source = self.script()
        body = source[source.index("async function selectNote") :]
        return body[: body.index("async function saveNote")]

    def test_it_reads_the_title_from_meta(self):
        """`meta.title` is where the API puts it; `note.title` is undefined."""
        body = self.select_body()
        assert "meta.title" in body
        assert "note.title" not in body

    def test_it_reads_a_markdown_body_from_the_synthetic_block(self):
        """A legacy .md arrives as one paragraph block holding the body."""
        body = self.select_body()
        assert "note.blocks" in body
        assert "data.text" in body
        # The old top-level content field does not exist in the payload.
        assert "note.content" not in body

    def test_it_creates_the_editor_with_the_extracted_body(self):
        """Passing the raw payload field would leave the editor blank."""
        body = self.select_body()
        editor_at = body.index("new EasyMDE(")
        assert "initialValue: bodyText" in body[editor_at:]

    def test_a_markdown_note_is_read_only(self):
        """Nothing can write a .md note, so the editor must not pretend otherwise."""
        body = self.select_body()
        assert "setOption('readOnly', true)" in body

    def test_the_save_debounce_is_never_armed_for_markdown(self):
        """An armed debounce queues a save the server can only reject."""
        body = self.select_body()
        change_at = body.index("codemirror.on('change'")
        handler = body[change_at : change_at + 400]
        assert "if (isMarkdownNote)" in handler
        assert handler.index("if (isMarkdownNote)") < handler.index("isDirty = true")

    def test_saving_a_markdown_note_is_refused_before_the_request(self):
        """Issue no request that can only fail; say why instead."""
        source = self.script()
        body = source[source.index("async function saveNote") :]
        body = body[: body.index("async function createNewNote")]
        guard = body.index("if (isMarkdownNote)")
        assert guard < body.index("await fetch(")

    def test_the_read_only_notice_exists_and_names_conversion(self):
        """The notice is the only place the edit path is explained."""
        markup = template()
        assert 'id="convert-hint"' in markup
        assert "Convert to blocks to edit" in markup

    def test_the_notice_is_only_shown_for_markdown(self):
        source = self.script()
        body = self.select_body()
        assert "showConvertHint(isMarkdownNote)" in body
        # And it is defined to toggle rather than to show unconditionally.
        helper = source[source.index("function showConvertHint") :]
        helper = helper[: helper.index("function showNoteError")]
        assert "classList.toggle('hidden', !show)" in helper

    def test_the_notice_button_starts_the_conversion(self):
        """A notice naming the edit path with a dead button is decoration."""
        source = self.script()
        assert "btn-convert-hint" in source
        body = source[source.index("btnConvertHint.addEventListener") :]
        assert "convertFromRow(currentSlug" in body[:200]

    def test_errors_reach_the_page_and_not_only_the_console(self):
        """A failed load or save that only logs reads as a page that did nothing."""
        markup = template()
        assert 'id="note-error"' in markup
        source = self.script()
        save = source[source.index("async function saveNote") :]
        save = save[: save.index("async function createNewNote")]
        assert "showNoteError(" in save
        # The read path reports too.
        assert "showNoteError(" in self.select_body()

    def test_the_error_message_carries_the_server_detail(self):
        """HTTP 409 with no explanation is not actionable."""
        source = self.script()
        save = source[source.index("async function saveNote") :]
        save = save[: save.index("async function createNewNote")]
        assert "err.error" in save


class TestTheConvertOfAnOpenNote:
    """Converting the open note must hand the editor over to the new document.

    The row's Convert control re-rendered the sidebar only, leaving the markdown
    editor holding the pre-conversion text with its debounced save still armed
    against the same slug. Once the markdown page could save, that buffer would
    write the old body back over the conversion result.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def test_the_open_note_is_re_announced_after_a_conversion(self):
        source = self.script()
        body = source[source.index("async function convertFromRow") :]
        body = body[: body.index("async function reattachOpenNote")]
        assert "reattachOpenNote(slug)" in body

    def test_re_announcing_checks_it_is_still_the_open_note(self):
        """Converting another row must not disturb the note being edited."""
        source = self.script()
        helper = source[source.index("async function reattachOpenNote") :]
        helper = helper[: helper.index("/** Tell the block editor")]
        assert "slug !== currentSlug" in helper

    def test_re_announcing_cancels_the_armed_markdown_save(self):
        """The stale buffer is exactly what must not survive the conversion."""
        source = self.script()
        helper = source[source.index("async function reattachOpenNote") :]
        helper = helper[: helper.index("/** Tell the block editor")]
        assert "clearTimeout(saveTimer)" in helper
        assert "announceNoteOpened(slug)" in helper

    def test_a_row_keypress_on_the_convert_button_does_not_select_the_row(self):
        """One key must not both select the note and start a conversion."""
        source = self.script()
        body = source[source.index("item.addEventListener('keydown'") :]
        body = body[: body.index("notesList.appendChild(item)")]
        assert "closest('[data-convert-slug]')" in body
        assert body.index("closest('[data-convert-slug]')") < body.index("selectNote(")


class TestTheDirtyGuardDoesNotLatch:
    """An agent edit must not permanently classify its own block as conflicted.

    Writing an agent op into the editor makes Editor.js report those blocks as
    changed, which added them to the dirty set. The applied content then diffed
    empty against the refreshed snapshot, so save() returned early and the entries
    were never cleared. After the FIRST agent edit to a block, every later agent
    edit to it was routed to the conflict banner instead of being applied.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def apply_body(self) -> str:
        source = self.script()
        body = source[source.index("async function applyAgentChanges") :]
        return body[: body.index("async function applyOneOp")]

    def test_applied_agent_blocks_are_disarmed(self):
        body = self.apply_body()
        assert "dirty.delete(op.block_id)" in body

    def test_the_disarm_happens_after_the_snapshot_is_refreshed(self):
        """Resyncing afterwards would compare against a stale base."""
        body = self.apply_body()
        assert body.index("await resyncSnapshot()") < body.index("dirty.delete(")

    def test_only_applied_ops_are_disarmed(self):
        """A blocked op is untouched content, so its guard must survive."""
        body = self.apply_body()
        clear_loop = body[
            body.index("for (const op of untouched) {\n            dirty.delete") :
        ]
        assert "blocked" not in clear_loop[:200]

    def test_it_reports_which_ops_it_applied(self):
        """The ack needs the applied set; marking alone is not enough."""
        body = self.apply_body()
        assert "return untouched;" in body
        assert "return [];" in body

    def test_a_blocked_op_still_raises_the_conflict_banner(self):
        body = self.apply_body()
        assert "pendingConflicts = blocked;" in body
        assert "showConflict();" in body


class TestKeepMinePersistsTheKeptContent:
    """Keep mine must write the user's version before it acknowledges.

    The old path acked, then resynced the snapshot with the kept blocks still
    unsaved. Because the kept content was now recorded as sent, the diff was empty
    and save() skipped the PUT: the user's kept text was never written anywhere,
    while the ack told the server the agent's ops had been handled.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def keep_mine_body(self) -> str:
        source = self.script()
        body = source[source.index("async function keepMine") :]
        return body[: body.index("async function keepTheirs")]

    def test_the_kept_blocks_are_put_to_the_server(self):
        body = self.keep_mine_body()
        assert 'method: "PUT"' in body
        assert "JSON.stringify({ version, blocks })" in body

    def test_the_save_precedes_the_ack(self):
        """Acking first is exactly the path that lost the content."""
        body = self.keep_mine_body()
        assert body.index('method: "PUT"') < body.index("/api/changes/ack")

    def test_the_kept_blocks_are_read_from_the_editor(self):
        """Resyncing instead of saving would record unsent content as sent."""
        body = self.keep_mine_body()
        assert "await currentBlocks()" in body

    def test_a_failed_save_re_raises_the_banner(self):
        """The conflict is unresolved when the kept content did not land."""
        body = self.keep_mine_body()
        retry_failed = body[body.index("if (retry.error)") :]
        assert "pendingConflicts = conflictOps" in retry_failed
        assert "showConflict()" in retry_failed

    def test_a_non_conflict_failure_also_keeps_the_banner(self):
        body = self.keep_mine_body()
        else_branch = body[body.index("} else {\n                pendingConflicts") :]
        assert "showConflict()" in else_branch[:200]

    def test_the_conflict_is_captured_before_the_banner_clears(self):
        body = self.keep_mine_body()
        assert body.index("const conflictOps = pendingConflicts") < body.index(
            "pendingConflicts = []"
        )

    def test_a_stray_click_with_no_conflict_does_nothing(self):
        body = self.keep_mine_body()
        assert "!conflictOps.length" in body


class TestTheAckOnlyCoversWhatWasApplied:
    """Conflicted ops must survive the poll that could not apply them.

    poll() acknowledged the response's version unconditionally, right after
    applyAgentChanges returned -- including the blocked subset that went to the
    conflict banner. The comment claiming those ops come back on the next poll was
    false: they were already discarded server-side.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def poll_body(self) -> str:
        source = self.script()
        body = source[source.index("async function poll") :]
        return body[: body.index("function ackableVersion")]

    def test_the_poll_does_not_ack_the_response_version_blindly(self):
        body = self.poll_body()
        assert "up_to_version: body.version" not in body

    def test_the_ack_is_derived_from_the_applied_set(self):
        body = self.poll_body()
        assert "ackableVersion(body.changes, applied)" in body

    def test_the_ack_is_skipped_when_there_is_nothing_safe_to_ack(self):
        """A computed max of zero must not send ``up_to_version: 0``."""
        body = self.poll_body()
        assert "if (ackUpTo > 0)" in body

    def test_an_unapplied_op_caps_the_ack_below_its_version(self):
        source = self.script()
        body = source[source.index("function ackableVersion") :]
        body = body[: body.index("/** Open a block document")]
        assert "appliedIds" in body
        assert "return lowest - 1;" in body

    def test_an_entirely_applied_batch_acks_its_versions(self):
        source = self.script()
        body = source[source.index("function ackableVersion") :]
        body = body[: body.index("/** Open a block document")]
        assert "lowest === Infinity" in body

    def test_the_comment_describes_the_implemented_behaviour(self):
        """The claim that blocked ops re-deliver is only true if they stay queued."""
        source = self.script()
        comment = source[source.index("let pendingConflicts = [];") - 400 :]
        assert "NOT acknowledged" in comment


class TestTheConflictedReloadRespectsDirtyBlocks:
    """The wholesale reload is the one path that could overwrite typing.

    On ``body.conflicted`` the poll called open(slug), which re-fetches and rebuilds
    the editor with no regard for unsaved local edits.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def conflicted_branch(self) -> str:
        source = self.script()
        body = source[source.index("if (body.conflicted)") :]
        return body[: body.index("if (body.changes && body.changes.length)")]

    def test_it_asks_before_discarding_unsaved_work(self):
        branch = self.conflicted_branch()
        assert "window.confirm" in branch
        assert "dirty.size" in branch
        assert "pendingConflicts.length" in branch

    def test_the_confirmation_precedes_the_reload(self):
        branch = self.conflicted_branch()
        assert branch.index("window.confirm") < branch.index("await open(targetSlug)")

    def test_declining_does_not_reload(self):
        branch = self.conflicted_branch()
        refusal = branch[branch.index("if (!ok)") :]
        assert "return;" in refusal
        assert refusal.index("return;") < branch.index("await open(targetSlug)")

    def test_a_clean_document_reloads_without_asking(self):
        """The question is only worth asking when there is something to lose."""
        branch = self.conflicted_branch()
        assert branch.index("if (dirty.size") < branch.index("window.confirm")


# ---------------------------------------------------------------------------
# Stage 7: editor lifecycle
# ---------------------------------------------------------------------------


class TestTheOpenEpoch:
    """Every async continuation must abandon work for a document no longer open.

    `slug` and `version` are module-level and were re-read after every await, and
    nothing cancelled the poll timer on a switch. A poll started on note A could
    therefore apply A's ops into B's editor, ack under B's slug, and adopt A's
    version.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def poll_body(self) -> str:
        source = self.script()
        body = source[source.index("async function poll") :]
        return body[: body.index("function ackableVersion")]

    def test_the_epoch_exists(self):
        assert "let openEpoch = 0;" in self.script()

    def test_open_bumps_before_its_first_await(self):
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        assert body.index("++openEpoch") < body.index("await fetchJson")

    def test_open_abandons_when_superseded(self):
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        assert "epoch !== openEpoch" in body

    def test_the_poll_captures_both_the_epoch_and_the_slug(self):
        body = self.poll_body()
        assert "const epoch = openEpoch;" in body
        assert "const targetSlug = slug;" in body

    def test_the_poll_rechecks_after_its_fetch(self):
        body = self.poll_body()
        fetch_at = body.index("const body = await fetchJson")
        after = body[fetch_at:]
        assert "epoch !== openEpoch || slug !== targetSlug" in after[:400]

    def test_no_ack_is_sent_for_a_superseded_poll(self):
        """The ack is the irreversible half: it discards server-side state."""
        body = self.poll_body()
        ack_at = body.index("/api/changes/ack")
        guard = body.rindex("epoch !== openEpoch", 0, ack_at)
        assert guard < ack_at

    def test_no_version_is_adopted_for_a_superseded_poll(self):
        body = self.poll_body()
        adopt_at = body.index("version = body.version;")
        guard = body.rindex("epoch !== openEpoch", 0, adopt_at)
        assert guard < adopt_at

    def test_a_poll_started_on_another_note_uses_the_captured_slug(self):
        """`slug` in the request could otherwise be a different document."""
        body = self.poll_body()
        assert "encodeURIComponent(targetSlug)" in body
        assert "encodeURIComponent(slug)" not in body

    def test_the_auto_send_rechecks_the_epoch(self):
        body = self.poll_body()
        send_at = body.index("await sendPendingOps(targetSlug)")
        assert "epoch !== openEpoch" in body[send_at : send_at + 300]


class TestEditorInitFailureFallsBack:
    """A throwing tool constructor must not leave a broken editor.

    `new EditorJS(...)` and `await editor.isReady` were outside any try/catch, so
    a failure left a half-built instance assigned with the markdown node already
    hidden: an empty box and no explanation.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_construction_is_guarded(self):
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        assert "try {" in body
        assert "new window.EditorJS" in body[body.index("try {") :]

    def test_a_failure_clears_the_editor_and_shows_the_fallback(self):
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        catch = body[body.index("} catch (e) {") :]
        assert "editor = null;" in catch
        assert 'showEditorFor("markdown")' in catch

    def test_a_failure_says_what_happened(self):
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        catch = body[body.index("} catch (e) {") :]
        assert "setStatus(" in catch

    def test_a_superseded_editor_is_destroyed(self):
        """Two editors on one holder would stack."""
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("function toEditorBlock")]
        after_ready = body[body.index("await editor.isReady") :]
        assert "editor.destroy()" in after_ready


class TestSwitchingNotesClearsConflictState:
    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_destroy_clears_the_tracked_conflicts(self):
        source = self.script()
        body = source[source.index("async function destroyEditor") :]
        body = body[: body.index("async function currentBlocks")]
        assert "pendingConflicts = [];" in body

    def test_destroy_hides_the_banner(self):
        source = self.script()
        body = source[source.index("async function destroyEditor") :]
        body = body[: body.index("async function currentBlocks")]
        assert "hideConflict();" in body

    def test_open_clears_conflicts_before_building(self):
        """Scoped to the SUCCESS path: the failure branch clears them too, so a
        whole-function search would pass on the branch that returns early."""
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("new window.EditorJS")]
        success = body[body.index("slug = nextSlug;") :]
        assert "pendingConflicts = [];" in success
        assert "hideConflict();" in success


class TestOpenFailureResetsLocalState:
    """A failed open left the editor pointing at the PREVIOUS document.

    The sidebar had already marked the new note active, so every toolbar action
    then targeted the wrong note while the user looked at the new one.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def failure_branch(self) -> str:
        source = self.script()
        body = source[source.index("async function open(nextSlug)") :]
        body = body[: body.index("slug = nextSlug;")]
        return body[body.index("if (document_.error)") :]

    def test_it_clears_the_slug(self):
        """Scoped to the failure branch, which returns before the success path
        assigns `slug = nextSlug`."""
        branch = self.failure_branch()
        assert "slug = null;" in branch
        assert "slug = nextSlug" not in branch

    def test_it_clears_the_version(self):
        branch = self.failure_branch()
        assert "version = 0;" in branch
        assert "version = document_.meta.version" not in branch

    def test_it_says_so(self):
        assert "setStatus(" in self.failure_branch()


class TestThePollStopsOnADeadSlug:
    def test_a_404_holds_a_status_and_stops_mutating(self):
        source = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        body = source[source.index("async function poll") :]
        body = body[: body.index("function ackableVersion")]
        error_branch = body[body.index("if (body.error)") :]
        assert "404" in error_branch
        assert "holdStatus(" in error_branch
        assert "return;" in error_branch
        # It must return before the changes are applied.
        assert error_branch.index("return;") < body.index("applyAgentChanges")


class TestConcurrentSendsAreCoalesced:
    """Two overlapping sends posted the same diff twice.

    The toolbar click and the poll's auto-send could both read the same pending
    ops. On a 503 both retried, so the op reached the agent's context twice.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_an_in_flight_set_exists(self):
        assert "const sendInFlight = new Set();" in self.script()

    def test_a_second_send_for_the_same_slug_is_refused(self):
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("async function postPendingOps")]
        assert "sendInFlight.has(targetSlug)" in body

    def test_the_flag_is_cleared_on_every_exit_path(self):
        """A leaked flag would block all later sends for that note."""
        source = self.script()
        body = source[source.index("async function sendPendingOps") :]
        body = body[: body.index("async function postPendingOps")]
        assert "finally {" in body
        assert "sendInFlight.delete(targetSlug)" in body

    def test_the_poll_skips_a_send_already_in_flight(self):
        source = self.script()
        body = source[source.index("async function poll") :]
        body = body[: body.index("function ackableVersion")]
        assert "!sendInFlight.has(targetSlug)" in body


class TestHistoryFlushesThePendingSave:
    """Opening the history must not hide an unsaved edit.

    The browser shows what the SERVER recorded, and an edit still sitting in the
    save debounce has not been recorded yet -- so the user's last sentence would
    be missing from the newest revision and read as data loss.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def history_body(self) -> str:
        source = self.script()
        body = source[source.index("async function openHistory") :]
        return body[: body.index("function renderHistoryList")]

    def test_the_armed_timer_is_flushed(self):
        body = self.history_body()
        assert "if (saveTimer)" in body
        assert "clearTimeout(saveTimer)" in body
        assert "await save()" in body

    def test_the_flush_precedes_any_request(self):
        body = self.history_body()
        assert body.index("await save()") < body.index("fetchJson(")

    def restore_body(self) -> str:
        source = self.script()
        body = source[source.index("async function restoreSelectedRevision") :]
        return body[: body.index("function closeHistory")]

    def test_a_409_refreshes_the_version_and_invites_a_retry(self):
        """A restore is a write, so it can lose the version race like any other."""
        body = self.restore_body()
        assert "409" in body
        assert "refreshVersion()" in body
        assert "retry" in body.lower()


class TestPinFollowThrough:
    """Pressing Pin in the block toolbar appeared to do nothing."""

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def pin_body(self) -> str:
        source = self.script()
        body = source[source.index("async function togglePin") :]
        return body[: body.index("async function removeDocument")]

    def test_a_successful_pin_announces_the_change(self):
        body = self.pin_body()
        assert 'new CustomEvent("wichy:scratchpad-changed")' in body

    def test_the_sidebar_listens(self):
        source = (STATIC / "notes.js").read_text(encoding="utf-8")
        assert '"wichy:scratchpad-changed"' in source
        assert "refreshScratchpadState" in source

    def test_a_read_failure_is_retried_before_deciding(self):
        """A failed read used to be read as "not pinned", flipping the pin."""
        body = self.pin_body()
        assert body.count("fetchJson(") >= 2
        assert "Could not read the current pin state" in body

    def test_a_failed_post_says_so(self):
        body = self.pin_body()
        assert "Could not update the pin" in body


class TestRenamesAreFollowed:
    """A rename stranded the open editor on a slug that no longer existed.

    notes.js updated `currentSlug` but never re-announced the note, so the block
    editor kept polling and saving under the dead slug: 404s at best, and a
    document that silently stopped persisting.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def test_a_rename_re_announces_the_document(self):
        source = self.script()
        body = source[source.index("async function saveNote") :]
        body = body[: body.index("async function createNewNote")]
        assert "announceNoteOpened(data.slug)" in body


class TestTheQuietPeriodIsStatedInPlainWords:
    """The comment must name the value, not cite a document nobody can read.

    A commit replaced the old citation with nothing, leaving the 1000 ms quiet
    period documented only in the Python settings -- so a reader of the script
    had no idea how long a burst is held for.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_the_debounce_comment_names_the_value(self):
        source = self.script()
        body = source[source.index("Restart the change-notify debounce") :]
        body = body[: body.index("function scheduleChangeNotify")]
        assert "1000 ms" in body

    def test_it_says_the_value_is_configurable(self):
        source = self.script()
        body = source[source.index("Restart the change-notify debounce") :]
        body = body[: body.index("function scheduleChangeNotify")]
        assert "configurable" in body

    def test_it_names_no_document_or_internal_identifier(self):
        """SPEC.md is never committed, so a citation to it dangles."""
        source = self.script()
        body = source[source.index("Restart the change-notify debounce") :]
        body = body[: body.index("function scheduleChangeNotify")]
        assert "INV-" not in body
        assert "SPEC.md" not in body

    def test_the_stated_value_matches_the_setting_default(self):
        """A comment naming the wrong number is worse than no comment."""
        from wichy.config import settings

        source = self.script()
        body = source[source.index("Restart the change-notify debounce") :]
        body = body[: body.index("function scheduleChangeNotify")]
        assert f"{settings.notes_change_debounce_ms} ms" in body


class TestTheTodoCheckboxAnnouncesItsChange:
    """A toggled checkbox is a real edit and must reach the server.

    The checkbox edits `data` without touching the contenteditable, so the
    editor's mutation observer never sees it. The tool tried to announce it
    through `api.blocks.blockDidMutated`, which does not exist on the API surface
    a tool receives (it is an internal BlockManager method), so the guard was
    always false and the call never ran: the change event never fired, the save
    debounce never started, and the toggle was silently unsaved.
    """

    def custom_blocks(self) -> str:
        return (STATIC / "editorjs" / "custom-blocks.js").read_text(encoding="utf-8")

    def test_it_uses_the_block_wrapper_the_editor_hands_it(self):
        source = self.custom_blocks()
        assert "this.block = block" in source

    def test_it_calls_dispatch_change(self):
        source = self.custom_blocks()
        assert "this.block?.dispatchChange" in source
        assert "this.block.dispatchChange()" in source

    def test_the_nonexistent_api_method_is_not_called(self):
        """The comment above the call names it to explain why; the CODE must not.

        Asserted on the call sites, not the whole file: a mention inside a
        comment is the explanation, and banning the word outright would make the
        reason unwritable.
        """
        source = self.custom_blocks()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            assert "blockDidMutated" not in stripped, stripped

    def test_dispatch_change_is_what_the_vendored_tools_use(self):
        """Checked against the bundle, so the call is not another guess."""
        quote = (STATIC / "editorjs" / "quote.umd.js").read_text(encoding="utf-8")
        assert "dispatchChange" in quote

    def test_the_todo_constructor_accepts_the_block_wrapper(self):
        source = self.custom_blocks()
        # Scoped to the todo block's constructor, not any constructor.
        todo_at = source.index("cdx-todo")
        constructor = source.rindex("constructor(", 0, todo_at)
        header = source[constructor:todo_at]
        assert "block" in header


class TestEveryAsyncActionCarriesTheEpoch:
    """Fixing poll() alone left every OTHER async action unguarded.

    Each one captures `slug` and `version` from module state and awaits at least
    once, so a note switch part-way through made it act on the wrong document:
    applying the old note's ops into the new editor, PUTting the old blocks under
    the new slug, reverting or converting a note the user had just opened.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def body_of(self, name: str, until: str) -> str:
        source = self.script()
        body = source[source.index(f"async function {name}") :]
        return body[: body.index(until)]

    def test_apply_agent_changes_checks_before_writing(self):
        """The check must come BEFORE the first op write, not only after.

        Each op write is awaited and an `update` fires onChange, which arms the
        save and notify debounces -- so checking afterwards is too late.
        """
        body = self.body_of("applyAgentChanges", "/** Apply one agent op")
        assert "epoch !== openEpoch || slug !== targetSlug" in body
        guard = body.index("epoch !== openEpoch || slug !== targetSlug")
        assert guard < body.index("await applyOneOp")
        assert guard < body.index("await resyncSnapshot")

    def test_apply_agent_changes_rechecks_between_ops(self):
        body = self.body_of("applyAgentChanges", "/** Apply one agent op")
        loop = body[body.index("for (const op of untouched) {") :]
        assert "epoch !== openEpoch || slug !== targetSlug" in loop[:300]
        assert "break;" in loop[:400]

    def test_refresh_version_does_not_assign_a_foreign_version(self):
        body = self.body_of("refreshVersion", "async function save()")
        assert "const epoch = openEpoch;" in body
        guard = body.index("epoch !== openEpoch")
        assert guard < body.index("version = document_.meta.version")

    def test_keep_mine_puts_to_the_captured_slug(self):
        body = self.body_of(
            "keepMine", "/**\n     * Resolve the conflict in favour of the agent"
        )
        assert "const targetSlug = slug;" in body
        assert "api/notes/${targetSlug}" in body
        assert "api/notes/${slug}" not in body

    def test_restore_acts_on_the_captured_slug(self):
        body = self.body_of("restoreSelectedRevision", "function closeHistory")
        assert "const targetSlug = slug;" in body
        assert "/restore" in body
        assert "api/notes/${targetSlug}/revisions/${revisionId}/restore" in body

    def test_restore_rechecks_after_the_confirmation_dialog(self):
        """The dialog is modal but not instant, and the note can switch under it."""
        body = self.body_of("restoreSelectedRevision", "function closeHistory")
        after_confirm = body[body.index("if (!confirmed)") :]
        assert "epoch !== openEpoch" in after_confirm[:400]

    def test_selecting_a_revision_guards_against_a_switch(self):
        """The fetch is awaited, so the note can change while it is in flight."""
        body = self.body_of("selectHistoryRevision", "function renderBlocksAsText")
        assert "const targetSlug = slug;" in body
        assert "epoch !== openEpoch || slug !== targetSlug" in body

    def test_convert_acts_on_the_captured_slug(self):
        """It is destructive and irreversible from the UI."""
        body = self.body_of("convert", "async function togglePin")
        assert "const targetSlug = slug;" in body
        assert "conversion-preview`" in body
        assert "api/notes/${targetSlug}/conversion-preview" in body
        assert "api/notes/${targetSlug}/convert" in body
        assert "api/notes/${slug}/" not in body


class TestTheBlockEditorHidesTheMarkdownEditor:
    """Opening a block document must hide the WHOLE markdown editor.

    EasyMDE replaces the textarea with an `.EasyMDEContainer` and moves the
    CodeMirror wrapper inside it, so toggling `hidden` on the textarea alone
    left the real editor standing: the page showed both editors at once,
    stacked in the same region.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def show_body(self) -> str:
        source = self.script()
        body = source[source.index("function showEditorFor") :]
        return body[: body.index("function setStatus")]

    def test_the_container_is_hidden_with_the_textarea(self):
        body = self.show_body()
        assert "EasyMDEContainer" in body
        assert 'classList.toggle("hidden"' in body

    def test_the_toggle_goes_to_the_container_not_a_fresh_query(self):
        """A querySelector could race a container that is rebuilt per note."""
        body = self.show_body()
        assert "markdownNode.parentElement" in body

    def test_the_textarea_is_still_toggled(self):
        """Before EasyMDE exists the textarea is the visible thing."""
        body = self.show_body()
        textarea_at = body.rindex("markdownNode.classList.toggle")
        container_at = body.index('contains("EasyMDEContainer")')
        assert container_at < textarea_at

    def test_the_container_guard_tolerates_a_missing_easyMDE(self):
        """A degraded page (no EasyMDE built) must not throw here."""
        body = self.show_body()
        assert "if (isContainer)" in body

    def test_rebuilding_easyMDE_restates_the_format(self):
        """The constructor runs AFTER the visibility was decided.

        selectNote announces the note (which hides the textarea) and then builds
        a fresh EasyMDE container; without restating `hidden`, the new container
        shows beneath the block editor.
        """
        source = (STATIC / "notes.js").read_text(encoding="utf-8")
        constructor_at = source.index("new EasyMDE(")
        window_end = source.index("showConvertHint(isMarkdownNote);", constructor_at)
        after = source[constructor_at:window_end]
        assert "classList.toggle('hidden', !isMarkdownNote)" in after


class TestTheTitleSaveCarriesTheVersion:
    """The markdown page's save path was still markdown-era.

    It PUT `{title, content}` to an endpoint that requires `version` and reads
    the title from `meta`: every title edit failed with "A version is
    required", and had it not, the markdown string in `content` would have
    replaced the block list with one synthetic paragraph.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def save_body(self) -> str:
        source = self.script()
        body = source[source.index("async function saveNote") :]
        return body[: body.index("async function createNewNote")]

    def test_the_payload_carries_the_version(self):
        assert "{ version: knownVersion }" in self.save_body()

    def test_the_payload_carries_no_blocks_key(self):
        """Omitted `blocks` means unchanged; a present one replaces the list."""
        body = self.save_body()
        assert "payload.meta" in body
        assert "JSON.stringify(payload)" in body
        assert "content: ''" not in body
        assert "{ title, content }" not in body

    def test_the_title_rides_in_meta(self):
        body = self.save_body()
        assert "payload.meta = { title }" in body

    def test_the_version_is_tracked_from_every_source(self):
        source = self.script()
        assert "let knownVersion" in source
        assert "knownVersion = meta.version || 0;" in source
        assert "knownVersion = data.version;" in source

    def test_a_same_title_edit_is_still_a_put(self):
        """A no-op save is sent anyway: it must clear the dirty latch."""
        body = self.save_body()
        assert "if (wanted) {" in body
        assert "payload.meta = { title };" in body
        # The PUT happens regardless of `wanted`.
        assert body.index("const payload = { version: knownVersion };") < body.index(
            "method: 'PUT'"
        )

    def test_overlapping_saves_are_serialised(self):
        """A debounce and a blur can both fire: two PUTs with one version 409."""
        source = self.script()
        assert "let saveChain = Promise.resolve();" in source
        body = self.save_body()
        assert "saveChain.then(doTitleSave)" in body
        assert "saveChain = run.catch(() => {});" in body

    def test_the_new_version_reaches_the_block_editor(self):
        """A title PUT bumps the version under the block editor's feet."""
        source = self.script()
        assert "wichy:note-version" in source
        assert "announceVersion();" in source

    def test_the_open_note_title_is_read_from_meta(self):
        """`note.title` does not exist on the wire; `meta.title` does."""
        source = self.script()
        select_at = source.index("async function selectNote")
        save_at = source.index("async function saveNote")
        select_body = source[select_at:save_at]
        assert "noteTitle.textContent = meta.title" in select_body
        assert "note.title" not in select_body

    def test_the_block_editor_adopts_an_announced_version(self):
        blocks = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        init_at = blocks.index("function init() {")
        body = blocks[init_at:]
        assert 'addEventListener("wichy:note-version"' in body
        assert "version = event.detail.version;" in body

    def test_the_announced_version_is_ignored_with_no_document_open(self):
        blocks = (STATIC / "notes_blocks.js").read_text(encoding="utf-8")
        init_at = blocks.index('addEventListener("wichy:note-version"')
        body = blocks[init_at : init_at + 600]
        assert "if (!slug) {" in body
        assert body.index("if (!slug) {") < body.index(
            "version = event.detail.version;"
        )


class TestTheTitleSaveTracksServerMoves:
    """A version held while the server moves is a 409 waiting to happen.

    The page used to hold no version at all; after fixing the payload, a
    version held but never refreshed would 409 the first save after any
    external write -- the agent's, or another tab's.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def test_the_poll_refreshes_the_version_from_the_list(self):
        source = self.script()
        poll_at = source.index("pollTimer = setInterval")
        poll_body = source[poll_at : poll_at + 6000]
        assert "knownVersion = openNote.version;" in poll_body
        assert "announceVersion();" in poll_body

    def test_the_poll_skips_a_dirty_note(self):
        """Adopting a list version over unsaved edits would clobber them."""
        source = self.script()
        adopt_at = source.index("knownVersion = openNote.version;")
        guard = source.rindex("if (", 0, adopt_at)
        assert "!isDirty" in source[guard:adopt_at]

    def test_a_markdown_note_refuses_before_the_request_still(self):
        body = self.save_note_body()
        guard_at = body.index("if (isMarkdownNote)")
        assert guard_at < body.index("saveChain")

    def save_note_body(self) -> str:
        source = self.script()
        body = source[source.index("async function saveNote") :]
        return body[: body.index("async function createNewNote")]


class TestTheListPollNeverRebuildsTheOpenEditor:
    """The sidebar poll must not re-select the note the user is writing in.

    The poll compared `updated` stamps and re-selected the open note when it
    moved. But this page's own saves move that stamp: a few seconds after every
    pause in typing, selectNote(sameSlug) ran, and open() destroys and
    recreates Editor.js -- the caret died mid-writing on a note the user never
    left. External changes to the open document arrive through the block
    editor's own pending-changes poll, which writes ops into the standing
    editor instead of replacing it.
    """

    def script(self) -> str:
        return (STATIC / "notes.js").read_text(encoding="utf-8")

    def poll_body(self) -> str:
        source = self.script()
        start = source.index("pollTimer = setInterval")
        return source[start : start + 5200]

    def test_the_poll_does_not_re_select_the_open_note(self):
        body = self.poll_body()
        assert "await selectNote(currentSlug)" not in body
        assert "selectNote(" not in body.split("renderNotesList")[0]

    def test_the_comment_states_why_the_reselect_is_gone(self):
        body = self.poll_body()
        assert "re-select" in body or "re-selected" in body

    def test_the_poll_still_refreshes_the_open_version(self):
        """Dropping the re-select must not drop the version refresh: the list
        response carries the open note's current version."""
        body = self.poll_body()
        assert "knownVersion = openNote.version;" in body

    def test_the_poll_still_renders_the_list(self):
        body = self.poll_body()
        assert "renderNotesList(noteSearch.value.trim())" in body


class TestTheQueueSurvivesAReload:
    """Parked ops died with the page.

    pendingOps is in-memory, and the toolbar says the edits are "queued" --
    after leaving and re-entering the notes page the queue was empty and the
    Send control was gone, with no trace of the edits it had promised to
    deliver.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def test_a_storage_key_exists(self):
        assert "wichy-notes-pending-ops" in self.script()

    def test_parking_persists_the_queue(self):
        body = self.flush_body()
        first_set = body.index("pendingOps.set(slug, ops);")
        persist_at = body.index("persistQueue(slug);", first_set)
        assert persist_at > first_set

    def test_the_version_is_persisted_after_the_save(self):
        """The stored queue must carry the version the save produced."""
        body = self.flush_body()
        version_at = body.index("pendingVersion.set(slug, version);")
        persist_at = body.index("persistQueue(slug);", version_at)
        assert persist_at > version_at

    def test_open_rehydrates_the_queue(self):
        source = self.script()
        open_at = source.index("async function open(")
        open_body = source[open_at:]
        snapshot_at = open_body.rindex("sentSnapshot = new Map(")
        segment = open_body[snapshot_at : snapshot_at + 700]
        assert "restoreQueue(nextSlug);" in segment

    def test_a_successful_send_drops_the_stored_queue(self):
        body = self.post_body()
        delete_at = body.index(
            "pendingOps.delete(targetSlug);", body.rindex("return false")
        )
        drop_at = body.index("dropStoredQueue(targetSlug);", delete_at)
        assert drop_at > delete_at

    def test_a_409_drop_also_clears_storage(self):
        body = self.post_body()
        conflict_at = body.index('result.error === "HTTP 409"')
        segment = body[conflict_at : body.index("HTTP 503", conflict_at)]
        assert "dropStoredQueue(targetSlug);" in segment

    def test_a_failed_send_does_not_clear_storage(self):
        """The 503 branch parks the ops: dropping the stored copy there is
        exactly the loss this persistence exists to prevent."""
        body = self.post_body()
        conflict_at = body.index('result.error === "HTTP 503"')
        segment = body[conflict_at : body.index("return false;", conflict_at)]
        assert "dropStoredQueue" not in segment

    def test_deleting_the_note_drops_its_queue(self):
        """A new note that later takes the same slug must not inherit the dead
        document's unsent ops."""
        source = self.script()
        start = source.index("async function removeDocument")
        body = source[start : start + 800]
        assert "dropStoredQueue(targetSlug);" in body

    def test_storage_access_is_guarded(self):
        """A private-mode or quota failure must not break the save path."""
        for helper in ("persistQueue", "restoreQueue", "dropStoredQueue"):
            start = self.script().index(f"function {helper}(")
            body = self.script()[start : start + 900]
            assert "try {" in body
            assert "catch" in body

    def flush_body(self) -> str:
        source = self.script()
        start = source.index("async function flushChangeNotify")
        return source[start : source.index("async function sendPendingOps")]

    def post_body(self) -> str:
        source = self.script()
        start = source.index("async function postPendingOps")
        return source[start : source.index("function updateQueueIndicator")]


class TestTheBrowserSendsThePreviousContent:
    """A diff needs the before-state, and only the browser still has it.

    The server's copy of a block is already the edited text, so an update sent
    without its previous content can only be reported as "something changed" --
    which is what made the notification useless.
    """

    def script(self) -> str:
        return (STATIC / "notes_blocks.js").read_text(encoding="utf-8")

    def diff_body(self) -> str:
        source = self.script()
        body = source[source.index("function diffAgainstSnapshot") :]
        return body[: body.index("/** Record the current blocks")]

    def test_an_update_carries_the_previous_content(self):
        body = self.diff_body()
        assert 'op: "update"' in body
        assert "before: { type: previous.type, data: previous.data }" in body

    def test_a_removal_carries_the_deleted_content(self):
        body = self.diff_body()
        assert 'op: "remove"' in body
        assert "before: { type: entry.type, data: entry.data }" in body

    def test_the_before_state_comes_from_the_snapshot_not_the_editor(self):
        """`previous` is the snapshot entry, which is what the server last took.

        Reading the before-state from anywhere else would describe a different
        baseline than the one the diff was computed against.
        """
        body = self.diff_body()
        assert "const previous = beforeById.get(block.id);" in body
