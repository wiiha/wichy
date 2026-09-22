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
    """The page must honour the shared theme rather than forcing light."""

    def css(self) -> str:
        return (STATIC / "notes.css").read_text(encoding="utf-8")

    def test_the_page_no_longer_forces_light(self):
        """`color-scheme: light` alone would pin the page regardless of theme."""
        source = template()
        assert 'content="light"' not in source

    def test_it_does_not_suppress_the_theme_script(self):
        """The base template's FOUC-prevention script must reach this page."""
        source = template()
        assert "{% block theme_script %}{% endblock theme_script %}" not in source

    def test_a_dark_palette_exists(self):
        assert '[data-theme="dark"]' in self.css()

    def test_the_dark_palette_covers_the_tokens_the_page_uses(self):
        """A token left undefined would fall back to its light value.

        The palette is the `[data-theme="dark"]` block that DECLARES variables,
        not the first scoped rule that merely uses one.
        """
        import re

        match = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', self.css())
        assert match, "no dark palette block found"
        palette = match.group(1)
        for token in ("--surface", "--background", "--border", "--text"):
            assert token in palette, f"{token} has no dark value"

    def test_the_editor_chrome_is_themed(self):
        """Editor.js injects light-oriented chrome that it does not theme."""
        css = self.css()
        for selector in (".ce-toolbar__plus", ".ce-popover", ".ce-inline-toolbar"):
            assert f'[data-theme="dark"] {selector}' in css, selector

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

    def test_the_ops_it_sends_are_authored_by_the_user(self):
        """A missing author is not evidence of a user, and the server drops it."""
        source = self.script()
        assert 'author: "user"' in source

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

    def test_the_toast_counts_distinct_blocks_not_ops(self):
        """Five ops on one block is one block, and the marks show one block."""
        source = self.script()
        body = source[source.index("async function applyAgentChanges") :]
        body = body[: body.index("async function applyOneOp")]
        assert "new Set(untouched.map((op) => op.block_id)).size" in body

    def test_the_toast_pluralizes_correctly(self):
        source = self.script()
        body = source[source.index("function showAgentToast") :]
        body = body[: body.index("function hideAgentToast")]
        assert 'count === 1 ? "" : "s"' in body

    def test_dismissing_the_toast_keeps_the_marks(self):
        """Waving the notice away must not erase what it was pointing at."""
        source = self.script()
        body = source[source.index("function initAgentToast") :]
        body = body[: body.index("function startPolling")]
        dismiss = body[body.index('"dismiss"') :]
        assert "hideAgentToast()" in dismiss
        assert "agentTouched = new Set()" not in dismiss

    def test_undo_confirms_with_the_scope_and_the_summary_before_reverting(self):
        """A revert is whole-document; the dialog must say so first."""
        source = self.script()
        body = source[source.index("async function undoLastAgentEdit") :]
        body = body[: body.index("function initAgentToast")]
        assert "window.confirm" in body
        assert "restores the whole document" in body
        assert body.index("window.confirm") < body.index("/revert")
        assert "latest.summary" in body

    def test_undo_targets_the_latest_agent_revision(self):
        source = self.script()
        body = source[source.index("async function undoLastAgentEdit") :]
        body = body[: body.index("function initAgentToast")]
        assert "author=agent" in body
        assert "revisions/${latest.id}/revert" in body

    def test_a_refused_undo_issues_no_request(self):
        source = self.script()
        body = source[source.index("async function undoLastAgentEdit") :]
        body = body[: body.index("function initAgentToast")]
        confirm_at = body.index("window.confirm")
        guard_at = body.index("if (!confirmed)")
        revert_at = body.index("/revert")
        assert confirm_at < guard_at < revert_at

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


class TestTheToastIsAnnouncedPolitely:
    def test_the_toast_uses_aria_live_polite(self):
        body = template()
        assert 'id="agent-toast"' in body
        toast = body[body.index('id="agent-toast"') :]
        toast = toast[: toast.index("</div>")]
        assert 'aria-live="polite"' in toast
        assert 'role="status"' in toast

    def test_the_toast_offers_view_dismiss_and_undo(self):
        body = template()
        for action in ("view", "dismiss", "undo"):
            assert f'data-toast="{action}"' in body


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

    def test_clearing_is_offered_only_when_something_is_queued(self):
        source = self.script()
        body = source[source.index("function updateQueueIndicator") :]
        body = body[: body.index("    /**\n     * Apply queued agent changes")]
        assert '[data-action="clear-changes"]' in body
        assert 'clear.classList.toggle("hidden", empty)' in body

    def test_the_toolbar_handles_send_and_clear_before_the_slug_guard(self):
        """A queue control that no-ops when no note is open is dead code."""
        source = self.script()
        body = source[source.index("function initToolbar") :]
        body = body[: body.index("function askToConvert")]
        assert 'action === "send-changes"' in body
        assert 'action === "clear-changes"' in body
