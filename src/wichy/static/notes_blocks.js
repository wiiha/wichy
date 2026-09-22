/**
 * Block editor lifecycle for the notes page.
 *
 * Loaded only when every Editor.js asset is present (`window.editorjsReady`).
 * If any is missing the page keeps using the markdown editor, so an incomplete
 * vendor drop degrades rather than breaking the page.
 *
 * This script owns the block editor and nothing else. It does not touch
 * EasyMDE's elements: the two editors have separate DOM nodes and the page
 * shows whichever matches the open document's format.
 *
 * Data flow:
 *   open document -> GET /api/notes/<slug>  (format decides which editor shows)
 *   edit          -> debounce -> PUT /api/notes/<slug> with the whole block list
 *   edit          -> debounce -> POST /api/changes with the DIFF, so the agent
 *                    is told what changed rather than the whole document
 *   poll          -> GET /api/changes/pending?slug= ... -> apply to clean blocks
 *   done          -> POST /api/changes/ack
 */
(function () {
    "use strict";

    if (!window.editorjsReady) {
        // A visible note beats a broken page: the markdown editor is still there.
        return;
    }

    const settingsNode = document.getElementById("notes-settings");
    const SETTINGS = settingsNode ? JSON.parse(settingsNode.textContent) : {};
    const SAVE_DEBOUNCE_MS = SETTINGS.save_debounce_ms || 2000;
    const POLL_INTERVAL_MS = SETTINGS.poll_interval_ms || 2000;
    const CHANGE_DEBOUNCE_MS = SETTINGS.change_debounce_ms || 1000;
    const PREFIX = "/tools/notes";

    /**
     * "auto" sends each diff as it settles; anything else queues it locally until
     * the user presses Send.
     */
    const NOTIFICATION_MODE = SETTINGS.notification_mode || "auto";
    const AUTO_SEND = NOTIFICATION_MODE === "auto";

    /** The editor instance, or null when no block document is open. */
    let editor = null;
    /** The open document's slug, or null. */
    let slug = null;
    /** The version the editor last loaded or saved, sent with every write. */
    let version = 0;
    /** Blocks the user has edited but not yet saved. */
    let dirty = new Set();
    /** Blocks the agent has changed, so they are marked rather than silently overwritten. */
    let agentTouched = new Set();
    /**
     * The blocks as the agent last saw them, keyed by id, and the version that
     * snapshot belongs to.
     *
     * The diff is taken against this, not against `dirty`. `dirty` cannot
     * describe an added, removed or moved block -- it only knows which indexes
     * reported a change -- and an op list needs ids and positions. The snapshot
     * advances only once the server has accepted the ops, so a rejected send
     * leaves the change pending instead of losing it.
     */
    let sentSnapshot = new Map();
    /**
     * Ops computed but not yet sent, per slug.
     *
     * In auto mode a failed POST parks them here and the next poll retries, which
     * is what keeps "no active session" from silently dropping an edit. In
     * on-demand mode they wait here on purpose until Send.
     */
    let pendingOps = new Map();
    /** The version to send with pendingOps for a given slug. */
    let pendingVersion = new Map();
    let changeTimer = null;
    let saveTimer = null;
    let pollTimer = null;
    /**
     * Agent ops for blocks the user is editing, so they were not applied.
     *
     * Held rather than dropped: the banner's [Keep agent's] applies these, and
     * while the banner is up they are NOT acknowledged -- the poll acks only the
     * ops it actually applied, so these stay queued on the server and the next
     * poll returns them again.
     */
    let pendingConflicts = [];
    /** Guards the one-shot retry after a 409, so a losing write cannot loop. */
    let conflictRetried = false;
    /** Timestamp until which a held status message must not be overwritten. */
    let statusHoldUntil = 0;
    /** Blocks the agent CREATED, as opposed to ones it modified. */
    let agentCreated = new Set();
    /** When each marked block was changed, as HH:MM local time. */
    let agentChangeTimes = new Map();

    const blocksNode = document.getElementById("block-editor");
    const markdownNode = document.getElementById("note-content");
    const toolbarNode = document.getElementById("toolbar");
    const statusNode = document.getElementById("editor-status");

    /**
     * Fetch JSON, returning null on a non-OK response rather than throwing.
     * Callers show a message; an unhandled rejection would leave the UI silent.
     */
    async function fetchJson(url, options) {
        try {
            const response = await fetch(url, {
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                ...options,
            });
            if (!response.ok) {
                return { error: `HTTP ${response.status}` };
            }
            return await response.json();
        } catch (e) {
            return { error: String(e) };
        }
    }

    /** Show which editor matches `format`, and hide the other. */
    function showEditorFor(format) {
        const isBlocks = format === "editorjs";
        blocksNode.classList.toggle("hidden", !isBlocks);
        markdownNode.classList.toggle("hidden", isBlocks);
    }

    function setStatus(text) {
        if (statusNode) {
            statusNode.textContent = text || "";
        }
    }

    /**
     * Show a status message that survives the next poll.
     *
     * `setStatus` is called on every poll, and the poll runs every couple of
     * seconds, so a plain message is wiped almost immediately -- "Edits queued
     * for next turn" would be unreadable. The hold is a deadline rather than a
     * flag so a burst of messages cannot leave the status stuck forever.
     */
    function holdStatus(text, ms = 6000) {
        setStatus(text);
        statusHoldUntil = Date.now() + ms;
    }

    /** The Editor.js tools map. Custom classes come from the vendored bundle. */
    function toolsConfig() {
        const custom = window.WichyCustomBlocks || {};
        return {
            header: window.Header,
            list: window.List,
            code: window.CodeTool,
            quote: window.Quote,
            checklist: window.Checklist,
            delimiter: window.Delimiter,
            question: custom.Question,
            decision: custom.Decision,
            todo: custom.Todo,
        };
    }

    /**
     * Destroy the editor.
     *
     * Editor.js attaches listeners to the holder, so re-initialising without
     * destroying first would stack a second editor on the same node.
     */
    async function destroyEditor() {
        if (saveTimer) {
            clearTimeout(saveTimer);
            saveTimer = null;
        }
        if (changeTimer) {
            clearTimeout(changeTimer);
            changeTimer = null;
        }
        if (editor) {
            try {
                await editor.isReady;
                editor.destroy();
            } catch (e) {
                // An editor that never became ready has nothing to detach.
            }
            editor = null;
        }
        dirty = new Set();
        agentTouched = new Set();
        agentCreated = new Set();
        agentChangeTimes = new Map();
        sentSnapshot = new Map();
        updateQueueIndicator();
    }

    /** Read the editor's current blocks, excluding anything not yet rendered. */
    async function currentBlocks() {
        const saved = await editor.save();
        return saved.blocks || [];
    }

    /** The comparable content of one block: id, type and data, without meta. */
    function comparable(block) {
        return { id: block.id, type: block.type, data: block.data };
    }

    /**
     * Describe the change from the sent snapshot to the current blocks.
     *
     * Mirrors the server's own diff (removals first, then a left-to-right pass
     * placing each block, then updates) so the ops the agent is told about match
     * the ops its own revision log records for the same edit. Removals carry no
     * index because a removal does not depend on position; every other index is
     * measured against the FINAL list, which is only well defined once the
     * removals have been dropped.
     *
     * Returns [] when nothing changed, so a no-op flush sends nothing.
     */
    function diffAgainstSnapshot(blocks) {
        const before = Array.from(sentSnapshot.values());
        const beforeById = new Map(before.map((entry) => [entry.id, entry]));
        const afterIds = new Set(blocks.map((block) => block.id));
        const ops = [];

        for (const entry of before) {
            if (!afterIds.has(entry.id)) {
                ops.push({
                    op: "remove",
                    block_id: entry.id,
                    block_type: entry.type,
                });
            }
        }

        let working = before
            .filter((entry) => afterIds.has(entry.id))
            .map((entry) => entry.id);

        blocks.forEach((block, index) => {
            const previous = beforeById.get(block.id);
            if (!previous) {
                ops.push({
                    op: "add",
                    block_id: block.id,
                    block_type: block.type,
                    data: block.data,
                    index: index,
                });
                working.splice(index, 0, block.id);
                return;
            }
            if (index < working.length && working[index] !== block.id) {
                ops.push({ op: "move", block_id: block.id, index: index });
                working = working.filter((id) => id !== block.id);
                working.splice(index, 0, block.id);
            }
            if (
                JSON.stringify(previous.data) !== JSON.stringify(block.data) ||
                previous.type !== block.type
            ) {
                ops.push({
                    op: "update",
                    block_id: block.id,
                    block_type: block.type,
                    data: block.data,
                    index: index,
                });
            }
        });

        return ops;
    }

    /** Record the current blocks as what the server now knows about. */
    async function resyncSnapshot() {
        if (!editor) {
            sentSnapshot = new Map();
            return;
        }
        const blocks = await currentBlocks();
        sentSnapshot = new Map(
            blocks.filter((block) => block.id).map((block) => [block.id, comparable(block)])
        );
    }

    /**
     * Re-read the document's version after a write we did not make.
     *
     * The snapshot is resynced too, and that is the point of doing it here
     * rather than only reading the version: after a conflict the local content is
     * the truth we want to keep, so recording it as sent stops the next diff from
     * describing the user's own existing text as a fresh change.
     */
    async function refreshVersion() {
        if (!slug) {
            return;
        }
        const document_ = await fetchJson(`${PREFIX}/api/notes/${slug}`);
        if (!document_.error && document_.meta) {
            version = document_.meta.version;
        }
        await resyncSnapshot();
    }

    /**
     * Save the whole block list.
     *
     * A whole-list PUT is what the server expects: the editor owns order and
     * content, and the server merges its own per-block metadata back in by id.
     *
     * A save that would change nothing is skipped. The editor reports changes
     * made by the editor rather than by the user -- including the ones this
     * script makes when it applies an agent's ops -- so without this check a
     * delivery of agent changes triggered a PUT of content that was already on
     * the server, which came back 409 because the version had moved. Comparing
     * against the last known server state is self-correcting: whatever the
     * cause of a spurious change event, an unchanged document is never written.
     */
    async function save() {
        if (!editor || !slug) {
            return;
        }
        const blocks = await currentBlocks();
        if (!diffAgainstSnapshot(blocks).length) {
            return;
        }
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
            method: "PUT",
            body: JSON.stringify({ version, blocks }),
        });

        if (result.error) {
            if (String(result.error).includes("409") && !conflictRetried) {
                // Someone else wrote first. Adopt their version and re-send the
                // local content on top of it, so the user's edit is not simply
                // refused. Retried once only: if it conflicts again the note is
                // being written continuously, and looping would hammer it while
                // the user's editor and the other writer fight.
                conflictRetried = true;
                try {
                    await refreshVersion();
                    const retry = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
                        method: "PUT",
                        body: JSON.stringify({ version, blocks }),
                    });
                    if (!retry.error) {
                        version = retry.version;
                        dirty = new Set();
                        await resyncSnapshot();
                        setStatus("");
                        return;
                    }
                } finally {
                    conflictRetried = false;
                }
            }
            setStatus("Could not save. Reload the page to see the current version.");
            return;
        }
        version = result.version;
        dirty = new Set();
        await resyncSnapshot();
        setStatus("");
    }

    /** Restart the save debounce. Called on every editor change. */
    function scheduleSave() {
        if (saveTimer) {
            clearTimeout(saveTimer);
        }
        saveTimer = setTimeout(() => {
            saveTimer = null;
            save();
        }, SAVE_DEBOUNCE_MS);
    }

    /**
     * Restart the change-notify debounce.
     *
     * Separate from the save debounce and shorter by default: saving is the
     * editor's own durability, notifying is what the agent sees. The quiet period
     * exists so a burst of keystrokes is one message, not one message per key;
     * the interval is configurable and read from the page's settings.
     */
    function scheduleChangeNotify() {
        if (changeTimer) {
            clearTimeout(changeTimer);
        }
        changeTimer = setTimeout(() => {
            changeTimer = null;
            flushChangeNotify();
        }, CHANGE_DEBOUNCE_MS);
    }

    /**
     * Compute the diff and either send it or park it, depending on the mode.
     *
     * The save is awaited BEFORE the ops are posted, and the version they carry
     * is the one the save produced. Posting them with the pre-save version raced
     * the save: the server checks the version against the document, and whichever
     * of the two requests arrived second was told its version was stale. The two
     * are not independent -- the notify describes the very change the save
     * persists -- so they run in order, save first.
     *
     * The ops are computed before the save, because the save resyncs the snapshot
     * that the diff is taken against.
     */
    async function flushChangeNotify() {
        if (!editor || !slug) {
            return;
        }
        const ops = diffAgainstSnapshot(await currentBlocks());
        if (!ops.length) {
            updateQueueIndicator();
            return;
        }
        pendingOps.set(slug, ops);
        // Persist first, so the version sent with the ops is the one the server
        // holds once it has the content those ops describe.
        await save();
        pendingVersion.set(slug, version);
        if (AUTO_SEND) {
            await sendPendingOps(slug);
        }
        updateQueueIndicator();
    }

    /**
     * POST the parked ops, and advance the snapshot only if the server took them.
     *
     * The version sent is the one the ops were computed against, not the current
     * one: the server checks it against the document, and a mismatch means
     * something else wrote first, so the diff is no longer against the right base
     * and must not be recorded as sent.
     */
    async function sendPendingOps(targetSlug) {
        const ops = pendingOps.get(targetSlug);
        if (!ops || !ops.length) {
            return false;
        }
        if (targetSlug !== slug) {
            // The user switched documents; the ops stay parked for that slug and
            // are sent when it is reopened.
            return false;
        }
        const result = await fetchJson(`${PREFIX}/api/changes`, {
            method: "POST",
            body: JSON.stringify({
                slug: targetSlug,
                version: pendingVersion.get(targetSlug),
                ops: ops.map((entry) => ({ ...entry, author: "user" })),
            }),
        });
        if (result.error) {
            if (result.error === "HTTP 409") {
                // The document moved on since these ops were computed, so they
                // describe a base the server no longer has. Retrying the same
                // version would 409 forever, so the ops are dropped and the
                // version is refreshed; the next edit diffs cleanly against the
                // current state. The local content is still in the editor, so
                // nothing the user typed is lost -- only this notification is.
                pendingOps.delete(targetSlug);
                pendingVersion.delete(targetSlug);
                await refreshVersion();
                holdStatus("The note changed elsewhere. Your next edit will be sent.");
                updateQueueIndicator();
                return false;
            }
            // 503 (no session) and anything else transient: keep the ops and let
            // the next poll retry. Dropping them here is how an edit goes missing.
            if (result.error === "HTTP 503") {
                holdStatus("Edits queued for next turn.");
            }
            updateQueueIndicator();
            return false;
        }
        pendingOps.delete(targetSlug);
        pendingVersion.delete(targetSlug);
        await resyncSnapshot();
        updateQueueIndicator();
        return true;
    }

    /**
     * Show or hide the "Send to Agent" control and its count.
     *
     * Hidden when there is nothing unsent, so an empty queue is never offered:
     * sending an empty op list would inject a change message describing nothing.
     *
     * Found by `data-action`, not by id: the toolbar identifies its buttons by
     * `data-action`, and looking this one up as `#send-changes` matched nothing,
     * so the function returned early and the queue control never appeared at all
     * -- the on-demand mode had no way to send, and the queued edits were only
     * discoverable from a status line.
     */
    function updateQueueIndicator() {
        const button = document.querySelector('[data-action="send-changes"]');
        const clear = document.querySelector('[data-action="clear-changes"]');
        const count = document.getElementById("queued-count");
        if (!button) {
            return;
        }
        const ops = slug ? pendingOps.get(slug) || [] : [];
        const distinct = new Set(ops.map((entry) => entry.block_id)).size;
        const empty = distinct === 0;
        button.classList.toggle("hidden", empty);
        if (clear) {
            // The escape hatch is offered only when there is something to discard.
            clear.classList.toggle("hidden", empty);
        }
        if (count) {
            count.textContent = String(distinct);
        }
        button.disabled = empty;
    }

    /**
     * Note which blocks the user touched.
     *
     * The dirty set is what stops an agent edit overwriting a block the user is
     * still typing in, so a change event that adds nothing to it disarms that
     * guard silently. See changedBlockIds for the event shape.
     */
    async function onChange(api, event) {
        for (const blockId of changedBlockIds(event)) {
            dirty.add(blockId);
        }
        scheduleSave();
        scheduleChangeNotify();
    }

    /**
     * The block ids a change event refers to.
     *
     * Editor.js passes `event.detail.target.id` -- a STRING block id -- and
     * batches several events into an array when more than one block changed in
     * the same tick. The previous code read `event.block.id` and required it to
     * be a number, which is not the shape this editor emits, so it matched
     * nothing and `dirty` stayed empty: the guard that stops an agent edit
     * overwriting a block the user is still typing in never armed at all.
     *
     * The id is used directly rather than resolved through an index. Ids are
     * stable across edits and indices are not, and the event already carries the
     * id, so resolving it would only add a way to get it wrong.
     */
    function changedBlockIds(event) {
        const events = Array.isArray(event) ? event : [event];
        const ids = [];
        for (const one of events) {
            const id = one && one.detail && one.detail.target ? one.detail.target.id : null;
            if (typeof id === "string" && id) {
                ids.push(id);
            }
        }
        return ids;
    }

    /**
     * Apply queued agent changes to blocks the user has not touched.
     *
     * Applying means WRITING the change into the editor, not just marking it.
     * Marking alone left the browser showing the old text while the server held
     * the new: the user saw a purple border on content that did not match what
     * the agent had actually written. Each op also refreshes the sent snapshot,
     * so the agent's own change is not then diffed back as if the user had made
     * it -- that would bounce the agent's edit straight back to it as a user edit.
     */
    async function applyAgentChanges(changes) {
        if (!editor || !changes.length) {
            return [];
        }

        const untouched = changes.filter(
            (op) => op.block_id && !dirty.has(op.block_id)
        );
        const blocked = changes.filter(
            (op) => op.block_id && dirty.has(op.block_id)
        );

        for (const op of untouched) {
            await applyOneOp(op);
            agentTouched.add(op.block_id);
            if (op.op === "add") {
                // Tracked separately so the block gets the "created by" mark and
                // tooltip rather than the "changed by" one.
                agentCreated.add(op.block_id);
            }
            agentChangeTimes.set(op.block_id, clockNow());
        }
        // The re-render moved the DOM, and both the mark and the snapshot are
        // keyed by what is rendered now.
        await resyncSnapshot();
        // Writing the agent's ops into the editor made it report those blocks as
        // changed, which armed the dirty guard for content that is now identical
        // on both sides. The applied content diffs empty against the refreshed
        // snapshot, so save() returns early and would otherwise never clear
        // them: after the first agent edit to a block, every later agent edit to
        // it would be classified as a conflict, forever.
        //
        // Resyncing first is what makes this safe. Anything the user typed during
        // the apply window is still unsaved and still dirty, so it stays
        // protected; only the blocks whose content the agent just wrote -- and
        // which are now recorded in the snapshot -- are disarmed.
        for (const op of untouched) {
            dirty.delete(op.block_id);
        }
        await markAgentBlocks();
        if (untouched.length) {
            const distinct = new Set(untouched.map((op) => op.block_id)).size;
            setStatus(`Agent updated ${distinct} block(s).`);
            showAgentToast(distinct);
        }
        if (blocked.length) {
            // Those blocks are being edited here, so applying the agent's
            // version would discard the user's work. The banner states the
            // conflict and leaves the choice to them.
            pendingConflicts = blocked;
            // Marked orange, and marked BEFORE the banner is shown, so the
            // border and the banner describe the same set of blocks.
            await markAgentBlocks();
            showConflict();
        }
        // The ops actually written into the editor. The caller acknowledges this
        // set and no more: the blocked ones are still queued on the server, which
        // is what makes the banner's promise (they come back on the next poll)
        // true.
        return untouched;
    }

    /** Apply one agent op to the editor. Idempotent per op. */
    async function applyOneOp(op) {
        const id = op.block_id;
        try {
            if (op.op === "remove") {
                if (editor.blocks.getBlockIndex(id) !== undefined) {
                    editor.blocks.delete(editor.blocks.getBlockIndex(id));
                }
            } else if (op.op === "add") {
                const index =
                    typeof op.index === "number" ? op.index : undefined;
                editor.blocks.insert(
                    op.block_type,
                    op.data || {},
                    {},
                    index,
                    false,
                    false,
                    // The agent's own id is reused, so a later op for the same
                    // block still resolves and the mark still matches.
                    id
                );
            } else if (op.op === "update") {
                if (editor.blocks.getBlockIndex(id) !== undefined) {
                    await editor.blocks.update(id, op.data || {});
                }
            } else if (op.op === "move") {
                const from = editor.blocks.getBlockIndex(id);
                if (from !== undefined && typeof op.index === "number") {
                    editor.blocks.move(op.index, from);
                }
            }
        } catch (e) {
            // One op that the editor refuses must not abort the rest of the
            // batch, or a single stale id would leave the document half-updated.
            setStatus("Some agent changes could not be applied. Reload the document.");
        }
    }

    /**
     * Mark the blocks the agent changed, and briefly flash them.
     *
     * The id for each rendered block comes from the editor's own block list, not
     * from an attribute written onto the DOM. That is deliberate: Editor.js
     * watches the redactor for attribute mutations and reports any it does not
     * recognise as a user edit, so writing `data-block-id` here fired onChange,
     * which scheduled a save, which re-stamped -- a PUT loop that ran forever
     * with no user input, bumping the version and appending a "No changes"
     * revision on every pass. Reading the ids instead of writing them removes
     * the feedback loop at its source.
     *
     * The index-to-id pairing is sound for the same reason the old stamp was:
     * within one render pass the editor's block list is in the same order as the
     * rendered `.ce-block` elements.
     */
    async function markAgentBlocks() {
        if (!blocksNode) {
            return;
        }
        const blocks = editor ? await currentBlocks() : [];
        const conflictedIds = new Set(pendingConflicts.map((op) => op.block_id));
        blocksNode.querySelectorAll(".ce-block").forEach((element, index) => {
            const block = blocks[index];
            const id = block && block.id ? block.id : null;
            element.classList.toggle("conflicted", !!(id && conflictedIds.has(id)));
            if (id && agentTouched.has(id)) {
                element.classList.add("agent-touched");
                element.classList.toggle("agent-created", agentCreated.has(id));
                // The flash is a separate class so the mark survives its end.
                element.classList.add("agent-flash");
                element.title = describeAgentChange(id);
                window.setTimeout(() => element.classList.remove("agent-flash"), 2000);
            } else {
                element.classList.remove("agent-touched", "agent-created", "agent-flash");
                element.removeAttribute("title");
            }
        });
    }

    /**
     * The tooltip for a marked block.
     *
     * Text, not colour or an icon alone, so the meaning survives a monochrome
     * display: the CSS adds the robot marker, and this says the same thing in
     * words. The time is when the browser applied the change, which is the only
     * time it has -- the op carries no server timestamp, and inventing one would
     * claim a precision the pipeline does not have.
     */
    function describeAgentChange(id) {
        const time = agentChangeTimes.get(id);
        if (agentCreated.has(id)) {
            return time ? `Created by agent at ${time}` : "Created by agent";
        }
        return time ? `Changed by the agent at ${time}` : "Changed by the agent";
    }

    /** Local HH:MM for the moment the browser applied an agent change. */
    function clockNow() {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        return `${hh}:${mm}`;
    }

    /**
     * Show the conflict banner.
     *
     * The banner is informational until a button is pressed: the local version
     * stays in place and the queued agent ops stay unapplied, so merely showing
     * it discards nothing. That is the safe default -- a user who ignores the
     * banner keeps their own work.
     */
    function showConflict() {
        const banner = document.getElementById("conflict-banner");
        if (!banner) {
            return;
        }
        const text = document.getElementById("conflict-text");
        const count = new Set(pendingConflicts.map((op) => op.block_id)).size;
        if (text) {
            text.textContent =
                count === 1
                    ? "Agent also edited this block. Your version is kept."
                    : `Agent also edited ${count} blocks. Your version is kept.`;
        }
        banner.classList.remove("hidden");
    }

    function hideConflict() {
        const banner = document.getElementById("conflict-banner");
        if (banner) {
            banner.classList.add("hidden");
        }
    }

    /**
     * Resolve the conflict in favour of the local version.
     *
     * "Keep mine" means the user's version of those blocks is what the document
     * ends up holding, and the agent's queued ops for them are dropped.
     *
     * The kept content is SAVED FIRST. Recording it as sent without writing it
     * was a silent data-loss path: resyncing the snapshot made the kept blocks
     * diff empty, so save() skipped the PUT entirely and the user's kept text was
     * never persisted anywhere -- while the ack below told the server the agent's
     * ops had been handled. The ack now waits until the PUT has actually landed.
     */
    async function keepMine() {
        const conflictOps = pendingConflicts; // captured before the banner clears
        pendingConflicts = [];
        hideConflict();
        if (!slug || !conflictOps.length) {
            return;
        }

        const blocks = await currentBlocks();
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
            method: "PUT",
            body: JSON.stringify({ version, blocks }),
        });

        if (result.error) {
            // Same retry-once shape as save(): adopt the newer version and send
            // the kept content again on top of it.
            if (String(result.error).includes("409")) {
                await refreshVersion();
                const retry = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
                    method: "PUT",
                    body: JSON.stringify({ version, blocks }),
                });
                if (retry.error) {
                    // The kept content is still unsaved, so the conflict is NOT
                    // resolved: put the banner back rather than reporting success.
                    pendingConflicts = conflictOps;
                    showConflict();
                    setStatus("Could not save your version. The conflict is still open.");
                    return;
                }
                version = retry.version;
            } else {
                pendingConflicts = conflictOps;
                showConflict();
                setStatus("Could not save your version. The conflict is still open.");
                return;
            }
        } else {
            version = result.version;
        }

        dirty = new Set();
        await resyncSnapshot();
        // Only now, with the kept content on the server, are the agent's ops for
        // those blocks discarded.
        await fetchJson(`${PREFIX}/api/changes/ack`, {
            method: "POST",
            body: JSON.stringify({ slug, up_to_version: version }),
        });
        setStatus("Kept your version of the blocks the agent also edited.");
    }

    /**
     * Resolve the conflict in favour of the agent's version.
     *
     * This is the only action that discards local work, so the loss is stated
     * before it happens. The whole document is re-read rather than patched: the
     * agent's ops may have added, removed or reordered blocks as well as editing
     * this one, and applying them piecemeal is what the dirty guard exists to
     * avoid.
     */
    async function keepTheirs() {
        if (!slug || !pendingConflicts.length) {
            // Nothing to resolve. Without this guard a stray click would put up a
            // confirmation promising to discard "0 blocks" and then reload the
            // document for no reason.
            return;
        }
        const count = new Set(pendingConflicts.map((op) => op.block_id)).size;
        const confirmed = window.confirm(
            `Discard your version of ${count} block${count === 1 ? "" : "s"} ` +
                "and use the agent's?\n\nYour edits to " +
                (count === 1 ? "that block" : "those blocks") +
                " will be lost. This cannot be undone from here."
        );
        if (!confirmed) {
            // Refusing issues no request and leaves the banner up, so the user can
            // still choose one of the other actions.
            return;
        }
        pendingConflicts = [];
        hideConflict();
        await open(slug);
        setStatus("Replaced your version with the agent's.");
    }

    /** Show the local and agent versions of the conflicting blocks, read-only. */
    async function compareConflict() {
        if (!pendingConflicts.length) {
            return;
        }
        const mine = document.getElementById("compare-mine");
        const theirs = document.getElementById("compare-theirs");
        const modal = document.getElementById("compare-modal");
        if (!mine || !theirs || !modal) {
            return;
        }

        const mineLines = [];
        const theirLines = [];
        for (const op of pendingConflicts) {
            const id = op.block_id;
            mineLines.push(`${id}\n${await describeBlockForCompare(id)}`);
            theirLines.push(`${id}\n${describeOpForCompare(op)}`);
        }
        // textContent, not innerHTML: block data is document content and may
        // contain markup, which must be shown rather than rendered.
        mine.textContent = mineLines.join("\n\n");
        theirs.textContent = theirLines.join("\n\n");
        modal.classList.remove("hidden");
        document.getElementById("compare-modal-close")?.focus();
    }

    /**
     * The local content of a block, as plain text for the compare view.
     *
     * The block is read through `save()`, not through a `data` property: the
     * editor's block wrapper exposes getters for id, name, holder and so on but
     * NOT for its data, so reading `block.data` yields undefined and the local
     * column renders empty -- which reads as "you have no version", the opposite
     * of the truth. `save()` is async, so this returns a promise.
     */
    async function describeBlockForCompare(id) {
        if (!editor) {
            return "(not in the local document)";
        }
        const index = editor.blocks.getBlockIndex(id);
        if (index === undefined) {
            return "(not in the local document)";
        }
        const saved = await editor.blocks.getBlockByIndex(index).save();
        const data = (saved && saved.data) || {};
        const content = data.text || data.caption || "";
        return content ? String(content) : "(no text content)";
    }

    /** The agent's content for an op, as plain text for the compare view. */
    function describeOpForCompare(op) {
        if (op.op === "remove") {
            return "(the agent removed this block)";
        }
        const data = op.data || {};
        const content = data.text || data.caption || "";
        return content ? String(content) : `(${op.op} op, no text content)`;
    }

    function hideCompare() {
        const modal = document.getElementById("compare-modal");
        if (modal) {
            modal.classList.add("hidden");
        }
    }

    /** The conflict banner's three actions. */
    function initConflictBanner() {
        const banner = document.getElementById("conflict-banner");
        if (!banner) {
            return;
        }
        banner.addEventListener("click", async (event) => {
            const button = event.target.closest("button[data-conflict]");
            if (!button) {
                return;
            }
            const action = button.dataset.conflict;
            if (action === "mine") {
                await keepMine();
            } else if (action === "theirs") {
                await keepTheirs();
            } else if (action === "compare") {
                await compareConflict();
            }
        });

        const close = document.getElementById("compare-modal-close");
        if (close) {
            close.addEventListener("click", hideCompare);
        }
        const modal = document.getElementById("compare-modal");
        if (modal) {
            modal.addEventListener("click", (event) => {
                if (event.target === modal) {
                    hideCompare();
                }
            });
        }
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                hideCompare();
            }
        });
    }

    /**
     * Show the "Agent updated N blocks" toast.
     *
     * `count` is distinct blocks, not ops: it matches what the marks show and
     * what the conflict cap counts, so the number the user reads is the number of
     * blocks they can see changed.
     */
    function showAgentToast(count) {
        const toast = document.getElementById("agent-toast");
        const text = document.getElementById("agent-toast-text");
        if (!toast) {
            return;
        }
        if (text) {
            text.textContent = `Agent updated ${count} block${count === 1 ? "" : "s"}`;
        }
        toast.classList.remove("hidden");
    }

    function hideAgentToast() {
        const toast = document.getElementById("agent-toast");
        if (toast) {
            toast.classList.add("hidden");
        }
    }

    /** Scroll the first marked block into view and flash it again. */
    function viewAgentChange() {
        if (!blocksNode) {
            return;
        }
        const marked = blocksNode.querySelector(".ce-block.agent-touched");
        if (!marked) {
            setStatus("No agent changes are marked in this document.");
            return;
        }
        marked.scrollIntoView({ behavior: "smooth", block: "center" });
        marked.classList.remove("agent-flash");
        // Reflow between removing and re-adding, or the animation does not restart.
        void marked.offsetWidth;
        marked.classList.add("agent-flash");
    }

    /**
     * Revert the most recent agent revision.
     *
     * The whole-document scope is surfaced before anything happens: a revert
     * restores the document as it was before that revision, which can undo more
     * than the single block the user saw flash. Saying so is the difference
     * between an undo and a surprise.
     */
    async function undoLastAgentEdit() {
        if (!slug) {
            return;
        }
        const listed = await fetchJson(
            `${PREFIX}/api/notes/${slug}/revisions?author=agent&limit=1`
        );
        if (listed.error) {
            setStatus("Could not read the revision history.");
            return;
        }
        const latest = (listed.revisions || [])[0];
        if (!latest) {
            setStatus("There is no agent edit to undo.");
            return;
        }
        const summary = latest.summary || "the agent's last change";
        const when = latest.timestamp || "";
        const confirmed = window.confirm(
            `Undo the agent's last edit?\n\n${summary}\n${when}\n\n` +
                "This restores the whole document to how it was before that " +
                "change. Any edits made here since then are kept only if you " +
                "saved them."
        );
        if (!confirmed) {
            return;
        }
        const result = await fetchJson(
            `${PREFIX}/api/notes/${slug}/revisions/${latest.id}/revert`,
            { method: "POST", body: JSON.stringify({ version }) }
        );
        if (result.error) {
            setStatus("Could not undo that edit.");
            return;
        }
        // Reloaded rather than patched in place: a revert can restore, remove and
        // reorder blocks at once, so re-reading is the only way to be sure the
        // page matches the document.
        await open(slug);
        hideAgentToast();
        setStatus("Undid the agent's last edit.");
    }

    /** One poll: retry unsent ops, hand back what the agent changed, then ack it. */
    async function poll() {
        if (!slug) {
            return;
        }
        // Retry anything still parked, in both modes: in auto mode this is the
        // retry after a 503, and in on-demand mode it is a no-op because the ops
        // are waiting for the user, not for the network. The queue is only sent
        // on demand there, so sendPendingOps is not called for it.
        if (AUTO_SEND && (pendingOps.get(slug) || []).length) {
            await sendPendingOps(slug);
        }

        const body = await fetchJson(
            `${PREFIX}/api/changes/pending?slug=${encodeURIComponent(slug)}`
        );
        if (body.error) {
            return;
        }

        if (body.conflicted) {
            // Too many blocks changed for piecemeal application to be safe, so
            // the whole document is re-fetched instead. That rebuild replaces
            // every block, so it must not run over the top of local edits the
            // user has not saved: this is the one place the "never overwrite a
            // block the user is typing in" rule could still be broken.
            if (dirty.size || pendingConflicts.length) {
                const ok = window.confirm(
                    "The agent changed many blocks in this note. Reloading will " +
                        "discard the edits you have not saved, and any conflict you " +
                        "have not resolved. Reload?"
                );
                if (!ok) {
                    holdStatus("Reload skipped. Save or resolve your edits first.");
                    return;
                }
            }
            setStatus("Many changes arrived. Reloading the document.");
            await open(slug);
            return;
        }

        if (body.changes && body.changes.length) {
            const applied = await applyAgentChanges(body.changes);
            const ackUpTo = ackableVersion(body.changes, applied);
            if (ackUpTo > 0) {
                await fetchJson(`${PREFIX}/api/changes/ack`, {
                    method: "POST",
                    body: JSON.stringify({ slug, up_to_version: ackUpTo }),
                });
            }
            // Adopt the version those ops produced. The agent's write moved the
            // document on, and acknowledging is exactly "I am now based on this
            // version". Without this the client keeps sending the version it
            // loaded, and every later save and notify is rejected as stale --
            // the user's next edit could never be persisted at all.
            version = body.version;
        }
        updateQueueIndicator();
        if (Date.now() >= statusHoldUntil) {
            if (body.agent_busy) {
                // Names what happens to the user's edits, not just that the agent
                // is busy: the queue is not discarded, it lands in the next
                // thinking step, and the user should know that before typing more.
                setStatus(
                    "Agent is working. Your edits will appear in its next thinking " +
                        "step -- this may redirect its attention."
                );
            } else if ((pendingOps.get(slug) || []).length && !AUTO_SEND) {
                setStatus("Edits queued for next turn.");
            } else {
                setStatus("");
            }
        }
    }

    /**
     * The highest version this poll may acknowledge.
     *
     * Acknowledging V tells the server the browser has applied everything
     * produced at or before V, and that is only true for ops actually written
     * into the editor. Acking the response's version unconditionally was the
     * defect: the conflicted ops were discarded server-side while the banner
     * still offered to apply them, so the promise that they come back on the
     * next poll was false and [Keep agent's] resolved a conflict whose
     * server-side source no longer existed.
     *
     * A queued op's version is the version of the WRITE it came from, so several
     * ops in one batch can share a version -- including a blocked one alongside
     * an applied one. Returning the version just BELOW the oldest blocked op is
     * therefore the strongest statement that is actually true; anything at or
     * below it was applied, and the blocked ops stay queued. Acking a lower
     * number never loses an applied op: it only leaves ops to be re-delivered,
     * and acking the remainder on a later poll is enough, because everything at
     * or below the ack is already on screen.
     */
    function ackableVersion(changes, applied) {
        const appliedIds = new Set(applied.map((op) => op.block_id));
        let lowest = Infinity;
        for (const op of changes) {
            if (appliedIds.has(op.block_id)) {
                continue;
            }
            const v = typeof op.version === "number" ? op.version : 0;
            if (v < lowest) {
                lowest = v;
            }
        }
        if (lowest === Infinity) {
            // Everything in the batch was applied, so the batch's versions are
            // safe to acknowledge.
            return Math.max(
                ...changes.map((op) => (typeof op.version === "number" ? op.version : 0))
            );
        }
        return lowest - 1;
    }

    /** Open a block document, replacing whatever is currently loaded. */
    async function open(nextSlug) {
        const document_ = await fetchJson(`${PREFIX}/api/notes/${nextSlug}`);
        if (document_.error) {
            setStatus("Could not open the document.");
            return;
        }
        slug = nextSlug;
        version = document_.meta.version;
        showEditorFor(document_.format);
        // Updated for EVERY format, before any early return. A markdown document
        // is exactly the one "Convert to blocks" is for, so setting this only on
        // the block path left the control disabled for the only document it
        // applies to.
        updateConvertButton(document_.format);

        if (document_.format !== "editorjs") {
            await destroyEditor();
            return;
        }

        await destroyEditor();
        editor = new window.EditorJS({
            holder: blocksNode,
            tools: toolsConfig(),
            data: { blocks: document_.blocks.map(toEditorBlock) },
            onChange: onChange,
        });
        await editor.isReady;
        // What the server now knows about, taken from the document just loaded
        // rather than from the editor: a save round-trip would rewrite the
        // document before the user changed anything.
        sentSnapshot = new Map(
            document_.blocks
                .filter((block) => block.id)
                .map((block) => [block.id, comparable(block)])
        );
        // Any ops left parked from a previous visit to this document are still
        // unsent, so the control reappears with its count.
        updateQueueIndicator();
    }

    /**
     * Convert a stored block to the editor's shape.
     *
     * `meta` is deliberately dropped: the editor has nowhere to keep it, and the
     * server re-attaches its own copy by block id on save.
     */
    function toEditorBlock(block) {
        return { id: block.id, type: block.type, data: block.data };
    }

    /**
     * Enable "Convert to blocks" only for a markdown note.
     *
     * Disabled rather than hidden: a control that appears and disappears moves
     * the others, and with no note open there is no format to test, so a
     * visibility rule would make the disabled state unreachable.
     */
    function updateConvertButton(format) {
        const button = document.querySelector('#toolbar button[data-action="convert"]');
        if (button) {
            button.disabled = format !== "markdown";
        }
    }

    /** The toolbar, wired to the document-level actions. */
    function initToolbar() {
        if (!toolbarNode) {
            return;
        }
        toolbarNode.addEventListener("click", async (event) => {
            const button = event.target.closest("button[data-action]");
            if (!button || button.disabled) {
                return;
            }
            const action = button.dataset.action;
            if (action === "send-changes") {
                if (slug) {
                    await sendPendingOps(slug);
                }
                return;
            }
            if (action === "clear-changes") {
                if (slug) {
                    pendingOps.delete(slug);
                    pendingVersion.delete(slug);
                    updateQueueIndicator();
                    holdStatus("Queued changes cleared.");
                }
                return;
            }
            if (!slug) {
                return;
            }
            if (action === "export") {
                window.location.href = `${PREFIX}/api/notes/${slug}/export?download=1`;
            } else if (action === "convert") {
                await convert();
            } else if (action === "pin") {
                await togglePin();
            } else if (action === "delete") {
                await removeDocument();
            }
        });
    }

    /**
     * Ask before converting, naming what will be lost.
     *
     * Resolves true only when the user explicitly confirms. Cancelling, or
     * dismissing by clicking the backdrop or pressing Escape, resolves false and
     * the caller then issues NO request -- the preview is read-only, so backing
     * out leaves nothing behind.
     */
    function askToConvert(features) {
        return new Promise((resolve) => {
            const modal = document.getElementById("convert-modal");
            const list = document.getElementById("convert-modal-features");
            const confirmButton = document.getElementById("convert-modal-confirm");
            const cancelButton = document.getElementById("convert-modal-cancel");
            if (!modal || !list) {
                // Without the dialog, refusing is the safe answer: converting
                // silently would discard the features with no warning at all.
                resolve(false);
                return;
            }

            list.replaceChildren();
            for (const feature of features) {
                const item = document.createElement("li");
                // textContent, not innerHTML: the feature names come from the
                // document, and a note could contain markup in a table cell.
                item.textContent = feature;
                list.appendChild(item);
            }

            const finish = (answer) => {
                modal.classList.add("hidden");
                modal.removeEventListener("click", onBackdrop);
                document.removeEventListener("keydown", onKey);
                resolve(answer);
            };
            const onBackdrop = (event) => {
                if (event.target === modal) {
                    finish(false);
                }
            };
            const onKey = (event) => {
                if (event.key === "Escape") {
                    finish(false);
                }
            };

            confirmButton.onclick = () => finish(true);
            cancelButton.onclick = () => finish(false);
            modal.addEventListener("click", onBackdrop);
            document.addEventListener("keydown", onKey);
            modal.classList.remove("hidden");
            // Focus the safe choice, so a stray Enter does not convert.
            cancelButton.focus();
        });
    }

    async function convert() {
        // The preview comes first so the user can back out before anything is
        // written: conversion cannot be undone from the UI.
        const preview = await fetchJson(
            `${PREFIX}/api/notes/${slug}/conversion-preview`
        );
        if (preview.error) {
            setStatus("Could not preview the conversion.");
            return;
        }
        if (preview.lossy_features && preview.lossy_features.length) {
            const proceed = await askToConvert(preview.lossy_features);
            if (!proceed) {
                // Cancelling must issue no request at all.
                return;
            }
        }
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}/convert`, {
            method: "POST",
        });
        if (result.error) {
            setStatus("Could not convert this note.");
            return;
        }
        await open(slug);
        setStatus(
            `Converted ${result.converted_blocks} blocks. Original markdown kept as backup.`
        );
    }

    async function togglePin() {
        const state = await fetchJson(`${PREFIX}/api/notes/scratchpad`);
        const isPinned = !state.error && state.primary === slug;
        await fetchJson(`${PREFIX}/api/notes/${slug}/pin`, {
            method: "POST",
            body: JSON.stringify({ pinned: !isPinned }),
        });
    }

    async function removeDocument() {
        if (!window.confirm("Delete this note? This cannot be undone.")) {
            return;
        }
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
            method: "DELETE",
        });
        if (!result.error) {
            await destroyEditor();
            slug = null;
        }
    }

    /** The agent toast's three actions. */
    function initAgentToast() {
        const toast = document.getElementById("agent-toast");
        if (!toast) {
            return;
        }
        toast.addEventListener("click", async (event) => {
            const button = event.target.closest("button[data-toast]");
            if (!button) {
                return;
            }
            if (button.dataset.toast === "view") {
                viewAgentChange();
            } else if (button.dataset.toast === "undo") {
                await undoLastAgentEdit();
            } else if (button.dataset.toast === "dismiss") {
                // Dismissal hides the notice only: the marks stay, so the user can
                // still see what changed after waving the toast away.
                hideAgentToast();
            }
        });
    }

    /** Start polling for agent changes. */
    function startPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
        }
        pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    }

    function init() {
        initToolbar();
        initAgentToast();
        initConflictBanner();
        startPolling();
        // The notes list decides which document is open; it publishes the slug
        // so this script does not have to parse the DOM for it.
        document.addEventListener("wichy:note-opened", (event) => {
            if (event.detail && event.detail.slug) {
                open(event.detail.slug);
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
