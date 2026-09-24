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

    /**
     * Bumped every time a document is opened.
     *
     * Every async worker captures it on entry and re-checks it after each await.
     * Without it, a note switch mid-flight let the OLD note's continuation run
     * against the NEW note's state: `slug` and `version` are module-level and
     * were re-read after the awaits, so a poll started on note A applied A's ops
     * into B's editor, acked under B's slug, and adopted A's version. Nothing
     * cancelled the timer, and nothing compared the slug it started with to the
     * one it now had.
     */
    let openEpoch = 0;
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
    /**
     * Slugs with a send currently in flight.
     *
     * A per-slug flag rather than one global boolean: sending note A must not
     * block note B's send, and the ops are keyed by slug anyway.
     */
    const sendInFlight = new Set();
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

    /**
     * Show which editor matches `format`, and hide the other.
     *
     * EasyMDE replaces the textarea with its own `.EasyMDEContainer` and moves
     * the CodeMirror wrapper INSIDE it, so hiding the textarea alone leaves the
     * actual editor standing: the page showed both editors at once, stacked in
     * the same region. The container is the textarea's parent once EasyMDE has
     * built its DOM, and `hidden` is toggled on it whenever the textarea's
     * state is toggled. When EasyMDE has not been constructed yet the textarea
     * is still the visible thing, so it is toggled as before.
     */
    function showEditorFor(format) {
        const isBlocks = format === "editorjs";
        blocksNode.classList.toggle("hidden", !isBlocks);
        const container = markdownNode.parentElement;
        const isContainer = container && container.classList.contains("EasyMDEContainer");
        if (isContainer) {
            container.classList.toggle("hidden", isBlocks);
        }
        markdownNode.classList.toggle("hidden", isBlocks);
    }

    /**
     * The parked queue survives a reload.
     *
     * `pendingOps` is in-memory, and the toolbar tells the user the edits are
     * "queued" -- a promise that dies with the page unless it is persisted.
     * The queue is written to localStorage on every mutation and rehydrated
     * when a document is opened, keyed by slug so several notes can hold
     * unsent ops at once. The entry is cleared only when the server has
     * actually taken the ops (or the document was deleted): a failed or
     * refused send must leave the queue exactly as it was.
     *
     * Storage can be unavailable (private modes, quota), and a thrown error
     * there would kill the save path, so every access is guarded: persistence
     * is a repair for the reload case, not a load-bearing part of sending.
     */
    const QUEUE_STORAGE_KEY = "wichy-notes-pending-ops";

    function persistQueue(targetSlug) {
        try {
            const raw = {};
            for (const [key, value] of pendingOps) {
                raw[key] = { ops: value, version: pendingVersion.get(key) || 0 };
            }
            if (!raw[targetSlug]) {
                delete raw[targetSlug];
            }
            window.localStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(raw));
        } catch (e) {
            // Unavailable storage degrades to the old in-memory behaviour.
        }
    }

    function restoreQueue(targetSlug) {
        try {
            const raw = JSON.parse(window.localStorage.getItem(QUEUE_STORAGE_KEY) || "{}");
            const entry = raw[targetSlug];
            if (!entry || !Array.isArray(entry.ops) || !entry.ops.length) {
                return;
            }
            if (!pendingOps.has(targetSlug)) {
                pendingOps.set(targetSlug, entry.ops);
                pendingVersion.set(targetSlug, entry.version);
            }
        } catch (e) {
            // Corrupt or unavailable storage: the queue simply starts empty.
        }
    }

    function dropStoredQueue(targetSlug) {
        try {
            const raw = JSON.parse(window.localStorage.getItem(QUEUE_STORAGE_KEY) || "{}");
            if (raw[targetSlug] !== undefined) {
                delete raw[targetSlug];
                window.localStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(raw));
            }
        } catch (e) {
            // Nothing to drop, or nowhere to write the drop.
        }
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
        // The conflicted marks live in the DOM, which the next render replaces,
        // but the tracked ops and the banner do not: left behind, a new document
        // would show a banner describing conflicts in the old one.
        pendingConflicts = [];
        hideConflict();
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
                    // The content as it was. The server cannot recover this from
                    // the document, which already holds the AFTER state, and a
                    // deletion reported with no text tells the agent only that
                    // something it cannot see is gone.
                    before: { type: entry.type, data: entry.data },
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
                    // The content before this edit, so the server can show the
                    // agent a real before/after diff instead of naming the block
                    // it changed. Only the browser has it: the snapshot is what
                    // the last accepted send recorded, and the server's copy is
                    // already the new text.
                    before: { type: previous.type, data: previous.data },
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
        const epoch = openEpoch;
        const targetSlug = slug;
        const document_ = await fetchJson(`${PREFIX}/api/notes/${targetSlug}`);
        if (epoch !== openEpoch || slug !== targetSlug) {
            // The user switched notes during the fetch. Assigning this version
            // (and resyncing the snapshot from the new editor) would give the new
            // note the OLD note's version, and every later save would 409.
            return;
        }
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
     * A burst of keystrokes becomes one notification, not one per key: edits are
     * held for a 1000 ms quiet period (configurable via the notes settings, which
     * the page reads from its injected JSON) before they are sent to the agent.
     *
     * Separate from the save debounce and shorter by default: saving is the
     * editor's own durability, notifying is what the agent sees.
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
        persistQueue(slug);
        // Persist first, so the version sent with the ops is the one the server
        // holds once it has the content those ops describe.
        await save();
        pendingVersion.set(slug, version);
        persistQueue(slug);
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
        if (sendInFlight.has(targetSlug)) {
            // The toolbar click and the poll's auto-send can both reach here with
            // the same pending set, which posted the same diff twice: two
            // identical notifications in the agent's context for one edit. The
            // first send deletes the queue when it succeeds, so the second saw
            // nothing to send and was harmless -- but on a 503 both retried, and
            // the op was delivered twice.
            return false;
        }
        if (targetSlug !== slug) {
            // The user switched documents; the ops stay parked for that slug and
            // are sent when it is reopened.
            return false;
        }
        sendInFlight.add(targetSlug);
        try {
            return await postPendingOps(targetSlug, ops);
        } finally {
            sendInFlight.delete(targetSlug);
        }
    }

    /** The send itself, so the in-flight guard wraps every exit path. */
    async function postPendingOps(targetSlug, ops) {
        const result = await fetchJson(`${PREFIX}/api/changes`, {
            method: "POST",
            body: JSON.stringify({
                slug: targetSlug,
                version: pendingVersion.get(targetSlug),
                // No `author` field: this route is the user-to-agent channel by
                // contract, so the server stamps authorship itself. Sending it
                // made the server's filter a convention a crafted request could
                // break, in either direction.
                ops: ops,
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
                dropStoredQueue(targetSlug);
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
        // Only now is the promise "queued" discharged: the server has the ops,
        // so the stored copy can go. Clearing on a failed send here would lose
        // edits on the very reload this persistence exists for.
        dropStoredQueue(targetSlug);
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
        const count = document.getElementById("queued-count");
        if (!button) {
            return;
        }
        const ops = slug ? pendingOps.get(slug) || [] : [];
        const distinct = new Set(ops.map((entry) => entry.block_id)).size;
        const empty = distinct === 0;
        button.classList.toggle("hidden", empty);
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
    async function applyAgentChanges(changes, epoch, targetSlug) {
        if (!editor || !changes.length) {
            return [];
        }
        // Verified BEFORE the first write, not only after the batch. Each op
        // write is awaited, and an `update` fires the editor's onChange -- which
        // arms the save and notify debounces. If the note were switched mid-batch
        // the remaining ops would land in the NEW note's editor and those
        // debounces would persist the old note's content under the new note's
        // slug. Checking only afterwards is too late: the write already happened.
        if (epoch !== openEpoch || slug !== targetSlug) {
            return [];
        }

        const untouched = changes.filter(
            (op) => op.block_id && !dirty.has(op.block_id)
        );
        const blocked = changes.filter(
            (op) => op.block_id && dirty.has(op.block_id)
        );

        for (const op of untouched) {
            if (epoch !== openEpoch || slug !== targetSlug) {
                // The note changed under this batch. Stop: the remaining ops
                // belong to a document that is no longer open.
                break;
            }
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
            // The status line is the notice now. It already said this, which is
            // why a separate popup was redundant as well as intrusive.
            setStatus(`Agent updated ${distinct} block(s).`);
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
            // The conflict mark stays on the block, because its border is the
            // thing that reads as "this whole block is contested".
            element.classList.toggle("conflicted", !!(id && conflictedIds.has(id)));
            // The agent mark goes on the CONTENT wrapper instead, so the bar and
            // its label sit at the text column rather than out in the margin.
            // `.ce-block__content` is the element Editor.js wraps each block's
            // body in; without it (a block type that renders none) the mark falls
            // back to the block itself rather than being dropped.
            const target = element.querySelector(".ce-block__content") || element;
            if (id && agentTouched.has(id)) {
                target.classList.add("agent-touched");
                target.classList.toggle("agent-created", agentCreated.has(id));
                // The flash is a separate class so the mark survives its end.
                target.classList.add("agent-flash");
                target.title = describeAgentChange(id);
                window.setTimeout(
                    () => target.classList.remove("agent-flash"), 2000
                );
            } else {
                target.classList.remove(
                    "agent-touched", "agent-created", "agent-flash"
                );
                target.removeAttribute("title");
            }
        });
        updateAgentLegend();
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
        // Captured on entry: this function awaits several times, and re-reading
        // `slug` after an await could PUT the previous note's blocks under the
        // slug of the one the user just switched to.
        const epoch = openEpoch;
        const targetSlug = slug;

        const blocks = await currentBlocks();
        if (epoch !== openEpoch || slug !== targetSlug) {
            // The kept content belongs to a note that is no longer open, and the
            // conflict is not ours to resolve any more.
            pendingConflicts = conflictOps;
            return;
        }
        const result = await fetchJson(`${PREFIX}/api/notes/${targetSlug}`, {
            method: "PUT",
            body: JSON.stringify({ version, blocks }),
        });

        if (result.error) {
            // Same retry-once shape as save(): adopt the newer version and send
            // the kept content again on top of it.
            if (String(result.error).includes("409")) {
                await refreshVersion();
                if (epoch !== openEpoch || slug !== targetSlug) {
                    pendingConflicts = conflictOps;
                    return;
                }
                const retry = await fetchJson(`${PREFIX}/api/notes/${targetSlug}`, {
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
     * Show or hide the marks' legend.
     *
     * Hidden when nothing is marked, so it does not describe a state the document
     * is not in. Shown, it stays until the marks go -- which is why there is no
     * dismiss: the legend is not a notice to wave away, it is the key to marks
     * that are still on screen.
     */
    function updateAgentLegend() {
        const legend = document.getElementById("agent-legend");
        if (!legend) {
            return;
        }
        legend.classList.toggle("hidden", agentTouched.size === 0);
        updateUndoAgentButton();
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
        // Captured on entry. A revert is a write to a document named by slug, and
        // this function awaits (a save, a history fetch, a confirmation dialog),
        // so a note switch part-way through would revert the note the user just
        // opened rather than the one they were looking at.
        const epoch = openEpoch;
        const targetSlug = slug;
        // A save is armed by the debounce and this function rebuilds the editor
        // on success, which clears that timer: edits typed in the last couple of
        // seconds were silently dropped by an undo. Persist them first, so the
        // revert lands on top of what the user actually wrote.
        if (saveTimer) {
            clearTimeout(saveTimer);
            saveTimer = null;
            await save();
        }
        if (epoch !== openEpoch || slug !== targetSlug) {
            return;
        }
        const listed = await fetchJson(
            `${PREFIX}/api/notes/${targetSlug}/revisions?author=agent&limit=1`
        );
        if (listed.error) {
            setStatus("Could not read the revision history.");
            return;
        }
        if (epoch !== openEpoch || slug !== targetSlug) {
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
        // The dialog is modal but not instant, and the user can switch notes by
        // other means while it is up.
        if (epoch !== openEpoch || slug !== targetSlug) {
            return;
        }
        const result = await fetchJson(
            `${PREFIX}/api/notes/${targetSlug}/revisions/${latest.id}/revert`,
            { method: "POST", body: JSON.stringify({ version }) }
        );
        if (result.error) {
            if (String(result.error).includes("409")) {
                // The document moved on since this editor last knew its version.
                // Refresh and tell the user to try again, as save() does --
                // before, this path only said "could not undo", with no way
                // forward and no hint that retrying would work.
                await refreshVersion();
                setStatus("The note changed. Try the undo again.");
                return;
            }
            setStatus("Could not undo that edit.");
            return;
        }
        // Reloaded rather than patched in place: a revert can restore, remove and
        // reorder blocks at once, so re-reading is the only way to be sure the
        // page matches the document.
        await open(targetSlug);
        setStatus("Undid the agent's last edit.");
    }

    /** One poll: retry unsent ops, hand back what the agent changed, then ack it. */
    async function poll() {
        if (!slug) {
            return;
        }
        // Captured once, and re-checked after every await. Every value this
        // function reads (slug, version, the editor) is module-level and can be
        // replaced by an open() while one of these awaits is outstanding.
        const epoch = openEpoch;
        const targetSlug = slug;
        // Retry anything still parked, in both modes: in auto mode this is the
        // retry after a 503, and in on-demand mode it is a no-op because the ops
        // are waiting for the user, not for the network. The queue is only sent
        // on demand there, so sendPendingOps is not called for it.
        if (
            AUTO_SEND &&
            (pendingOps.get(targetSlug) || []).length &&
            !sendInFlight.has(targetSlug)
        ) {
            await sendPendingOps(targetSlug);
            if (epoch !== openEpoch || slug !== targetSlug) {
                return;
            }
        }

        const body = await fetchJson(
            `${PREFIX}/api/changes/pending?slug=${encodeURIComponent(targetSlug)}`
        );
        if (epoch !== openEpoch || slug !== targetSlug) {
            return;
        }
        if (body.error) {
            if (String(body.error).includes("404")) {
                // The document was renamed or deleted from under this editor.
                // Continuing to poll a slug that no longer exists would 404 on
                // every cycle forever, and every toolbar action would target a
                // dead name. Stop mutating state and say what happened.
                holdStatus("This note no longer exists here. Reopen it from the list.");
                return;
            }
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
            await open(targetSlug);
            return;
        }

        if (body.changes && body.changes.length) {
            const applied = await applyAgentChanges(body.changes, epoch, targetSlug);
            if (epoch !== openEpoch || slug !== targetSlug) {
                // The user switched notes while these ops were being written into
                // the editor. Acknowledge NOTHING and adopt NOTHING: the ops were
                // applied to a document that is no longer open.
                return;
            }
            const ackUpTo = ackableVersion(body.changes, applied);
            if (ackUpTo > 0) {
                await fetchJson(`${PREFIX}/api/changes/ack`, {
                    method: "POST",
                    body: JSON.stringify({ slug: targetSlug, up_to_version: ackUpTo }),
                });
            }
            if (epoch !== openEpoch || slug !== targetSlug) {
                return;
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
            } else if ((pendingOps.get(targetSlug) || []).length && !AUTO_SEND) {
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
        // Bumped FIRST, before any await, so a fetch already in flight for the
        // previous document can tell it has been superseded.
        const epoch = ++openEpoch;
        const document_ = await fetchJson(`${PREFIX}/api/notes/${nextSlug}`);
        if (epoch !== openEpoch) {
            // A newer open replaced this one while its fetch was in flight.
            return;
        }
        if (document_.error) {
            // The sidebar has already marked the new note active, so local state
            // still pointing at the PREVIOUS document would make every toolbar
            // action target the wrong note. Clear it and say so.
            slug = null;
            version = 0;
            editor = null;
            pendingConflicts = [];
            hideConflict();
            setStatus("Could not open the document.");
            return;
        }
        slug = nextSlug;
        version = document_.meta.version;
        // Cleared before the rebuild: a banner left up from the previous document
        // would name conflicts in a note the user is no longer looking at.
        pendingConflicts = [];
        hideConflict();
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
        try {
            editor = new window.EditorJS({
                holder: blocksNode,
                tools: toolsConfig(),
                data: { blocks: document_.blocks.map(toEditorBlock) },
                onChange: onChange,
            });
            await editor.isReady;
        } catch (e) {
            // A throwing tool constructor would otherwise leave a half-built
            // editor assigned, with the markdown node already hidden: the user
            // sees an empty box and no explanation. Fall back to the markdown
            // view and say what happened.
            editor = null;
            showEditorFor("markdown");
            setStatus("The block editor failed to start. Showing the raw note instead.");
            return;
        }
        if (epoch !== openEpoch) {
            // A newer open started while this editor was initialising. Drop this
            // one rather than leaving two editors attached to one holder.
            try {
                editor.destroy();
            } catch (e) {
                // A partially initialised editor has nothing to detach.
            }
            editor = null;
            return;
        }
        // What the server now knows about, taken from the document just loaded
        // rather than from the editor: a save round-trip would rewrite the
        // document before the user changed anything.
        sentSnapshot = new Map(
            document_.blocks
                .filter((block) => block.id)
                .map((block) => [block.id, comparable(block)])
        );
        // The queue parked on a previous visit to this document comes back:
        // the toolbar promised "queued", not "forgotten when you navigate".
        // A queue already held in memory (an earlier open of this slug in this
        // page's lifetime) is left untouched.
        restoreQueue(nextSlug);
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

    /**
     * Enable the undo control only when there is an agent edit to undo.
     *
     * The control is one toolbar button rather than a per-block action, because a
     * revert restores the WHOLE document to its state before that revision -- it
     * can undo more than the one block the user saw marked. Enabling it only when
     * a mark exists keeps it from offering an action with nothing behind it.
     */
    function updateUndoAgentButton() {
        const button = document.querySelector(
            '#toolbar button[data-action="undo-agent"]'
        );
        if (button) {
            button.disabled = agentTouched.size === 0;
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
            } else if (action === "undo-agent") {
                await undoLastAgentEdit();
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
        // Captured on entry: converting is destructive and irreversible from the
        // UI, and this function awaits a preview, a confirmation dialog and the
        // conversion itself -- so a note switch part-way through would convert a
        // DIFFERENT note from the one the user was shown a preview of.
        const epoch = openEpoch;
        const targetSlug = slug;
        // The preview comes first so the user can back out before anything is
        // written: conversion cannot be undone from the UI.
        const preview = await fetchJson(
            `${PREFIX}/api/notes/${targetSlug}/conversion-preview`
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
        if (epoch !== openEpoch || slug !== targetSlug) {
            return;
        }
        const result = await fetchJson(`${PREFIX}/api/notes/${targetSlug}/convert`, {
            method: "POST",
        });
        if (result.error) {
            setStatus("Could not convert this note.");
            return;
        }
        await open(targetSlug);
        setStatus(
            `Converted ${result.converted_blocks} blocks. Original markdown kept as backup.`
        );
    }

    async function togglePin() {
        let state = await fetchJson(`${PREFIX}/api/notes/scratchpad`);
        if (state.error) {
            // A failed read used to be read as "not pinned", so the POST below
            // would FLIP the pin instead of setting it: the user asked to pin and
            // got an unpin. Retry once, then refuse rather than guess.
            state = await fetchJson(`${PREFIX}/api/notes/scratchpad`);
        }
        if (state.error) {
            setStatus("Could not read the current pin state. Try again.");
            return;
        }
        const isPinned = state.primary === slug;
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}/pin`, {
            method: "POST",
            body: JSON.stringify({ pinned: !isPinned }),
        });
        if (result.error) {
            setStatus("Could not update the pin.");
            return;
        }
        // The sidebar's pinned marker is notes.js's to own, and it only ever
        // refreshes on its own poll: without this the pin POST appeared to do
        // nothing at all.
        window.dispatchEvent(new CustomEvent("wichy:scratchpad-changed"));
    }

    async function removeDocument() {
        if (!window.confirm("Delete this note? This cannot be undone.")) {
            return;
        }
        const targetSlug = slug;
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
            method: "DELETE",
        });
        if (!result.error) {
            // A deleted document can never be reopened to send its queue, and
            // a NEW note that later takes the same slug must not inherit the
            // dead document's unsent ops.
            dropStoredQueue(targetSlug);
            await destroyEditor();
            slug = null;
        }
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
        initConflictBanner();
        startPolling();
        // The notes list decides which document is open; it publishes the slug
        // so this script does not have to parse the DOM for it.
        document.addEventListener("wichy:note-opened", (event) => {
            if (event.detail && event.detail.slug) {
                open(event.detail.slug);
            }
        });
        // A title save in notes.js is a PUT: it moves the server version under
        // this editor. Adopting it keeps the next block save from 409ing on a
        // number that went stale through no action taken here.
        document.addEventListener("wichy:note-version", (event) => {
            if (!event.detail || typeof event.detail.version !== "number") {
                return;
            }
            if (!slug) {
                return;
            }
            version = event.detail.version;
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
