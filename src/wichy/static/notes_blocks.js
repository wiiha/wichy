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
    const PREFIX = "/tools/notes";

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
    let saveTimer = null;
    let pollTimer = null;

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
    }

    /** Read the editor's current blocks, excluding anything not yet rendered. */
    async function currentBlocks() {
        const saved = await editor.save();
        return saved.blocks || [];
    }

    /**
     * Save the whole block list.
     *
     * A whole-list PUT is what the server expects: the editor owns order and
     * content, and the server merges its own per-block metadata back in by id.
     */
    async function save() {
        if (!editor || !slug) {
            return;
        }
        const blocks = await currentBlocks();
        const result = await fetchJson(`${PREFIX}/api/notes/${slug}`, {
            method: "PUT",
            body: JSON.stringify({ version, blocks }),
        });

        if (result.error) {
            setStatus("Could not save. Reload the page to see the current version.");
            return;
        }
        version = result.version;
        dirty = new Set();
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

    /** The id at a given editor index, or null when the index is out of range. */
    async function idAt(index) {
        if (!editor) {
            return null;
        }
        const blocks = await currentBlocks();
        const block = blocks[index];
        return block && block.id ? block.id : null;
    }

    /**
     * Note which blocks the user touched.
     *
     * Editor.js reports a changed block by INDEX; queued agent ops carry block
     * IDs. The two are different namespaces, so the index is resolved to an id
     * here. Comparing them directly would never match, and the dirty guard --
     * the thing that stops an agent edit overwriting a block the user is still
     * working in -- would silently do nothing.
     */
    async function onChange(api, event) {
        const index = typeof event?.block?.id === "number" ? event.block.id : null;
        if (index !== null) {
            const blockId = await idAt(index);
            if (blockId) {
                dirty.add(blockId);
            }
        }
        scheduleSave();
    }

    /** Apply queued agent changes to blocks the user has not touched. */
    function applyAgentChanges(changes) {
        if (!editor || !changes.length) {
            return;
        }

        const untouched = changes.filter(
            (op) => op.block_id && !dirty.has(op.block_id)
        );
        const blocked = changes.filter(
            (op) => op.block_id && dirty.has(op.block_id)
        );

        for (const op of untouched) {
            agentTouched.add(op.block_id);
        }
        if (untouched.length) {
            setStatus(`Agent updated ${untouched.length} block(s).`);
        }
        if (blocked.length) {
            // Those blocks are being edited here, so applying the agent's
            // version would discard the user's work. The banner states the
            // conflict and leaves the choice to them.
            showConflict();
        }
    }

    function showConflict() {
        const banner = document.getElementById("conflict-banner");
        if (banner) {
            banner.classList.remove("hidden");
        }
    }

    /** One poll: hand back what the agent changed, then acknowledge it. */
    async function poll() {
        if (!slug) {
            return;
        }
        const body = await fetchJson(
            `${PREFIX}/api/changes/pending?slug=${encodeURIComponent(slug)}`
        );
        if (body.error) {
            return;
        }

        if (body.conflicted) {
            // Too many blocks changed for piecemeal application to be safe, so
            // the whole document is re-fetched instead.
            setStatus("Many changes arrived. Reloading the document.");
            await open(slug);
            return;
        }

        if (body.changes && body.changes.length) {
            applyAgentChanges(body.changes);
            await fetchJson(`${PREFIX}/api/changes/ack`, {
                method: "POST",
                body: JSON.stringify({ slug, up_to_version: body.version }),
            });
        }
        setStatus(body.agent_busy ? "Agent is working..." : "");
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

    /** Start polling for agent changes. */
    function startPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
        }
        pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    }

    function init() {
        initToolbar();
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
