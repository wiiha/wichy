"use strict";

// Wrapped in an IIFE: this file declares top-level `let`/`const`, and a second
// script on the same page redeclaring any of those names would throw at load.
// The block editor is exactly such a second script, so nothing here may leak
// into the shared global scope.
(function () {

    // Notes Tool Frontend

    let currentSlug = null;
    let scratchpadSlug = null;
    let allNotes = [];  // [{slug, title, created, updated}]
    let mde = null;
    let saveTimer = null;
    let isDirty = false;
    let pollTimer = null;
    /** True while the open note is a legacy markdown file, where saving is refused. */
    let isMarkdownNote = false;
    /**
     * The server version this page last read or wrote, sent with every save.
     *
     * The PUT route rejects a body without `version`, and it refuses a stale
     * one with a 409. Captured when a note is opened and adopted from each
     * save response, then announced to the block editor -- which holds the
     * version it fetched on open, and would otherwise send that against a
     * document this save has already moved.
     */
    let knownVersion = 0;
    /**
     * Serialises title saves.
     *
     * A debounced save and a blur save can both fire for one edit: two PUTs
     * carrying the same version 409 each other, and the user sees a conflict
     * error for their own typing. Chained, the second send reads the version
     * the first produced.
     */
    let saveChain = Promise.resolve();
    // DOM elements
    const btnNewNote = document.getElementById('btn-new-note');
    const noteSearch = document.getElementById('note-search');
    const notesList = document.getElementById('notes-list');
    const emptyState = document.getElementById('empty-state');
    const noNoteSelected = document.getElementById('no-note-selected');
    const noteEditor = document.getElementById('note-editor');
    const noteTitle = document.getElementById('note-title');
    const editorHeader = document.getElementById('editor-header');
    const noteContent = document.getElementById('note-content');
    const convertHint = document.getElementById('convert-hint');
    const noteError = document.getElementById('note-error');
    const btnPin = document.getElementById('btn-pin');
    const btnDelete = document.getElementById('btn-delete');

    // ---------------------------------------------------------------------------
    // Initialization
    // ---------------------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', () => {
        initSidebar();
        init();

        // New note button
        btnNewNote.addEventListener('click', createNewNote);

        // Search bar
        noteSearch.addEventListener('input', () => {
            renderNotesList(noteSearch.value.trim());
        });

        // Clicking anywhere in the sidebar while editor has unsaved changes → save first
        notesList.addEventListener('focusin', (e) => {
            if (e.target.closest('.note-item')) {
                if (isDirty && currentSlug !== null) {
                    saveNote();
                }
            }
        });

        // Clicking note items directly (fallback for focusin not firing)
        notesList.addEventListener('click', (e) => {
            const item = e.target.closest('.note-item');
            if (item) {
                if (isDirty && currentSlug !== null) {
                    saveNote().then(() => selectNote(item.dataset.slug));
                }
            }
        });

        // Title: click to edit, blur to save
        noteTitle.addEventListener('input', () => {
            onTitleInput();
        });

        noteTitle.addEventListener('blur', () => {
            if (isDirty) {
                onTitleBlur();
            }
        });

        // Pin button
        btnPin.addEventListener('click', () => {
            if (scratchpadSlug === currentSlug) {
                clearScratchpad();
            } else {
                setScratchpad();
            }
        });

        // Convert-from-hint button: the read-only notice for a markdown note
        // names the only edit path there is, so it must actually start it.
        const btnConvertHint = document.getElementById('btn-convert-hint');
        if (btnConvertHint) {
            btnConvertHint.addEventListener('click', () => {
                convertFromRow(currentSlug, btnConvertHint);
            });
        }

        // Delete button
        btnDelete.addEventListener('click', deleteCurrentNote);

        // The block editor owns its own Pin control, and the pinned marker is
        // this file's to render. Without a shared signal, pressing Pin there
        // appeared to do nothing until the next poll.
        window.addEventListener("wichy:scratchpad-changed", refreshScratchpadState);

        // Warn before leaving with unsaved changes
        window.addEventListener('beforeunload', (e) => {
            if (isDirty) {
                e.preventDefault();
                e.returnValue = '';
            }
        });
    });

    // ---------------------------------------------------------------------------
    // Core Functions
    // ---------------------------------------------------------------------------

    async function init() {
        try {
            const resp = await fetch('/tools/notes/api/notes/scratchpad', { credentials: 'same-origin' });
            if (resp.ok) {
                const data = await resp.json();
                scratchpadSlug = data.primary;
            }
        } catch (e) {
            console.error('Failed to fetch scratchpad status:', e);
        }
        await loadNotes();

        // Poll for new notes from the agent every 5 seconds
        pollTimer = setInterval(async () => {
            // Only reload if not dirty (don't interrupt user typing)
            if (isDirty) return;

            // Fetch fresh notes list
            try {
                const resp = await fetch('/tools/notes/api/notes', { credentials: 'same-origin' });
                if (!resp.ok) return;
                const data = await resp.json();
                const freshNotes = data.notes || [];

                // Compare with current state - detect adds, deletes, AND updates
                const freshSlugs = new Set(freshNotes.map(n => n.slug));
                const currentSlugs = new Set(allNotes.map(n => n.slug));

                let changed = freshSlugs.size !== currentSlugs.size;
                if (!changed) {
                    for (const slug of freshSlugs) {
                        if (!currentSlugs.has(slug)) { changed = true; break; }
                    }
                }

                // Also detect content/title/updated timestamp changes (same slug but modified)
                if (!changed) {
                    for (const fresh of freshNotes) {
                        const current = allNotes.find(n => n.slug === fresh.slug);
                        if (current) {
                            // Check if title or updated timestamp changed
                            if (current.title !== fresh.title || current.updated !== fresh.updated) {
                                changed = true;
                                break;
                            }
                        }
                    }
                }

                // Re-fetch scratchpad status every poll (not just when notes changed)
                let newScratchpadSlug = scratchpadSlug;
                try {
                    const statusResp = await fetch('/tools/notes/api/notes/scratchpad', { credentials: 'same-origin' });
                    if (statusResp.ok) {
                        const statusData = await statusResp.json();
                        newScratchpadSlug = statusData.primary;
                    }
                } catch (e) {}

                // Detect scratchpad pin change
                const scratchpadChanged = newScratchpadSlug !== scratchpadSlug;
                if (scratchpadChanged) {
                    changed = true;
                }

                if (changed) {
                    // Check if the currently viewed note was modified externally
                    // Compare fresh data against OLD allNotes before updating
                    if (currentSlug && !isDirty) {
                        const freshNote = freshNotes.find(n => n.slug === currentSlug);
                        const oldNote = allNotes.find(n => n.slug === currentSlug);
                        if (freshNote && oldNote && freshNote.updated !== oldNote.updated) {
                            await selectNote(currentSlug);
                        }
                    }

                    // Now update state
                    allNotes = freshNotes;
                    scratchpadSlug = newScratchpadSlug;
                    renderNotesList(noteSearch.value.trim());
                    // The list response carries the version of the open note.
                    // The re-select above already re-read it for a clean, open
                    // note; an agent write landed between polls would otherwise
                    // leave this page holding the superseded version, and its
                    // next save would 409 against a change it never made.
                    const openNote = allNotes.find(n => n.slug === currentSlug);
                    if (openNote && openNote.version && !isDirty) {
                        knownVersion = openNote.version;
                        announceVersion();
                    }
                }
            } catch (e) {}
        }, 5000);
    }

    async function loadNotes() {
        try {
            const resp = await fetch('/tools/notes/api/notes', { credentials: 'same-origin' });
            if (!resp.ok) {
                console.error('Failed to load notes');
                return;
            }
            const data = await resp.json();
            allNotes = data.notes || [];

            // Re-fetch scratchpad status to stay in sync
            try {
                const statusResp = await fetch('/tools/notes/api/notes/scratchpad', { credentials: 'same-origin' });
                if (statusResp.ok) {
                    const statusData = await statusResp.json();
                    scratchpadSlug = statusData.primary;
                }
            } catch (e) {
                console.error('Failed to refresh scratchpad status:', e);
            }

            renderNotesList(noteSearch.value.trim());
        } catch (e) {
            console.error('Failed to load notes:', e);
        }
    }

    function renderNotesList(filter = '') {
        notesList.innerHTML = '';

        const lowerFilter = filter.toLowerCase();
        const filtered = allNotes.filter(note =>
            note.title.toLowerCase().includes(lowerFilter)
        );

        // Show empty state only when there are truly zero notes (not filtered)
        if (allNotes.length === 0) {
            emptyState.classList.remove('hidden');
        } else {
            emptyState.classList.add('hidden');
        }

        if (filtered.length === 0) {
            return;
        }

        // Sort by updated desc
        const sorted = [...filtered].sort((a, b) => {
            const aTime = a.updated ? new Date(a.updated).getTime() : 0;
            const bTime = b.updated ? new Date(b.updated).getTime() : 0;
            return bTime - aTime;
        });

        sorted.forEach(note => {
            const isScratchpad = note.slug === scratchpadSlug;
            const isActive = note.slug === currentSlug;

            const item = document.createElement('div');
            item.className = 'note-item' + (isActive ? ' active' : '');
            item.dataset.slug = note.slug;
            item.tabIndex = 0;

            const starClass = isScratchpad ? ' note-item-star scratchpad' : ' note-item-star';
            const starChar = isScratchpad ? '★' : '☆';

            const dateStr = formatDate(note.updated);

            item.innerHTML = `
                <span class="${starClass}">${starChar}</span>
                <div class="note-item-body">
                    <span class="note-item-title">${escapeHtml(note.title || 'Untitled')}</span>
                    <span class="note-item-date">${dateStr}</span>
                </div>
            `;

            // A markdown note gets an inline convert control, so converting does
            // not require opening the note first. Built as a real button rather
            // than embedded in the innerHTML above, because the click handler
            // must be attached and the row's own handler must not fire.
            if (note.format === 'markdown') {
                const convertButton = document.createElement('button');
                convertButton.className = 'btn btn-secondary btn-sm note-item-convert';
                convertButton.type = 'button';
                convertButton.dataset.convertSlug = note.slug;
                convertButton.textContent = 'Convert';
                item.appendChild(convertButton);
            }

            item.addEventListener('click', (event) => {
                // The convert control acts on its own; selecting the note as
                // well would be a second, unasked-for action.
                if (event.target.closest('[data-convert-slug]')) {
                    return;
                }
                selectNote(note.slug);
            });
            item.addEventListener('keydown', (e) => {
                // A keypress on the row's own Convert control belongs to that
                // control: without this, Enter on it both selected the note and
                // started a conversion, two actions from one key.
                if (e.target.closest('[data-convert-slug]')) {
                    return;
                }
                if (e.key === 'Enter' || e.key === ' ') {
                    selectNote(note.slug);
                }
            });
            notesList.appendChild(item);
        });
    }

    /** Convert a markdown note from its sidebar row or from the read-only hint. */
    async function convertFromRow(slug, button) {
        if (!slug) {
            return;
        }
        if (button) {
            button.disabled = true;
        }
        try {
            const resp = await fetch(`/tools/notes/api/notes/${slug}/convert`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                showNoteError('Could not convert this note: ' + (err.error || resp.status));
                if (button) {
                    button.disabled = false;
                }
                return;
            }
            await loadNotes();
            await reattachOpenNote(slug);
        } catch (e) {
            console.error('Failed to convert note:', e);
            showNoteError('Could not convert this note.');
            if (button) {
                button.disabled = false;
            }
        }
    }

    /**
     * Rebuild the editor for a note whose file changed format under it.
     *
     * Converting the note that is currently open leaves the markdown editor
     * showing the pre-conversion text with its debounced save still armed -- a
     * save that would write the stale body back over the conversion result.
     * Announcing the note again makes the block editor take the document over,
     * exactly as the toolbar's own Convert action does.
     */
    async function reattachOpenNote(slug) {
        if (slug !== currentSlug) {
            return;
        }
        clearTimeout(saveTimer);
        saveTimer = null;
        isDirty = false;
        setDirtyState(false);
        isMarkdownNote = false;
        showConvertHint(false);
        announceNoteOpened(slug);
    }

    /** Tell the block editor which document is open. */
    function announceNoteOpened(slug) {
        document.dispatchEvent(
            new CustomEvent('wichy:note-opened', { detail: { slug: slug } })
        );
    }

    /**
     * Tell the block editor the document's version moved under it.
     *
     * A title save is a PUT: the server bumps the version, and the block
     * editor's next save carries the one it fetched on open -- the pre-rename
     * number, which the server now reads as stale. Without this, the FIRST
     * block edit after a title edit dies on a 409 the user did nothing to
     * cause.
     */
    function announceVersion() {
        document.dispatchEvent(
            new CustomEvent('wichy:note-version', { detail: { version: knownVersion } })
        );
    }

    /** Enable the toolbar controls that need an open document. */
    function setToolbarEnabled(enabled) {
        const toolbar = document.getElementById('toolbar');
        if (!toolbar) {
            return;
        }
        toolbar.querySelectorAll('button[data-action]').forEach((button) => {
            button.disabled = !enabled;
        });
    }

    async function selectNote(slug) {
        // Cancel any pending debounce
        clearTimeout(saveTimer);
        saveTimer = null;

        currentSlug = slug;
        announceNoteOpened(slug);
        setToolbarEnabled(true);

        noNoteSelected.classList.add('hidden');
        noteEditor.classList.remove('hidden');

        try {
            const resp = await fetch(`/tools/notes/api/notes/${slug}`, { credentials: 'same-origin' });
            if (!resp.ok) {
                showNoteError(`Could not load this note (HTTP ${resp.status}).`);
                return;
            }
            const note = await resp.json();

            // The wire form is {meta, blocks, format}: the title lives under
            // `meta`, and there is no top-level `content`. A legacy `.md` note
            // arrives as one synthetic paragraph block holding its body.
            const meta = note.meta || {};
            knownVersion = meta.version || 0;
            isMarkdownNote = note.format === 'markdown';
            const bodyText = isMarkdownNote
                ? ((note.blocks && note.blocks[0] && note.blocks[0].data && note.blocks[0].data.text) || '')
                : '';

            clearNoteError();

            // Populate title immediately
            noteTitle.textContent = meta.title || '';
            setDirtyState(false);

            // Update active state in list
            renderNotesList(noteSearch.value.trim());

            // Update pin button
            updatePinButton();

            // Destroy existing EasyMDE if any. toTextArea unwinds the whole
            // EasyMDE DOM: it reinserts the bare textarea where the container
            // sits and removes the container, so a leftover container after a
            // note switch is not a case to handle -- but any `hidden` class
            // this page left on the textarea is, because the constructor clears
            // the inline style, not the class.
            if (mde) {
                mde.toTextArea();
                mde = null;
                noteContent.classList.remove('hidden');
            }

            // Reset the textarea
            noteContent.value = bodyText;

            // Create new EasyMDE instance — no toolbar (user writes raw Markdown).
            // autoDownloadFontAwesome: false prevents the constructor from injecting any
            // external stylesheet regardless of what styles are already in the document.
            mde = new EasyMDE({
                element: noteContent,
                toolbar: false,
                spellChecker: false,
                autoDownloadFontAwesome: false,
                initialValue: bodyText,
                status: false,
            });

            // A markdown note is read-only here: writing one would create a
            // .json beside the .md, so the server refuses it. The notice points
            // at conversion, which is the supported edit path.
            showConvertHint(isMarkdownNote);
            if (isMarkdownNote) {
                mde.codemirror.setOption('readOnly', true);
            }

            // Track changes
            mde.codemirror.on('change', () => {
                if (isMarkdownNote) {
                    // Read-only by design; arming the debounce would queue a save
                    // the server is only going to reject.
                    return;
                }
                isDirty = true;
                setDirtyState(true);
                restartSaveTimer();
            });

            // Clear dirty flag after load
            isDirty = false;
            setDirtyState(false);

        } catch (e) {
            console.error('Failed to load note:', e);
            showNoteError('Could not load this note.');
        }
    }

    async function saveNote() {
        if (!currentSlug) return;

        clearTimeout(saveTimer);
        saveTimer = null;

        if (isMarkdownNote) {
            // The server has no markdown write path. Refuse visibly rather than
            // issuing a request that can only fail.
            showNoteError('This note is stored as markdown. Convert it to blocks to edit it.');
            isDirty = false;
            setDirtyState(false);
            return;
        }

        const title = noteTitle.textContent.trim() || 'Untitled';
        // A title save must not write blocks: the block editor owns them, and
        // PUTting a markdown string here would replace the whole block list
        // with one synthetic paragraph. Omitted `blocks` means "unchanged" on
        // the server, so a title-only save leaves every block exactly as it is.
        const wanted = titleChanged();
        const payload = { version: knownVersion };
        if (wanted) {
            payload.meta = { title };
        }

        // Serialise overlapping saves: the debounce and the blur can both fire
        // for one edit, and two PUTs carrying the same version 409 each other.
        const run = saveChain.then(doTitleSave);
        // The chain must never reject: a failed save is surfaced in the page,
        // and an unhandled rejection would kill every later save in the chain.
        saveChain = run.catch(() => {});
        await run;

        async function doTitleSave() {
            if (isMarkdownNote) {
                // Re-checked inside the chain: state can change while an earlier
                // save is still in flight.
                showNoteError('This note is stored as markdown. Convert it to blocks to edit it.');
                isDirty = false;
                setDirtyState(false);
                return;
            }
            // Mark as saving
            editorHeader.dataset.saving = 'true';

            try {
                const resp = await fetch(`/tools/notes/api/notes/${currentSlug}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify(payload),
                });

                if (!resp.ok) {
                    let detail = `HTTP ${resp.status}`;
                    try {
                        const err = await resp.json();
                        if (err && err.error) detail = err.error;
                    } catch (parseError) {
                        // A non-JSON error body must not mask the status code.
                    }
                    showNoteError(`Could not save this note: ${detail}`);
                    editorHeader.dataset.saving = 'false';
                    return;
                }

                const data = await resp.json();
                // Adopt the version this write produced, so the next save does
                // not send a stale one and 409 against its own predecessor.
                knownVersion = data.version;
                // A same-title save is still a PUT: the server bumps the
                // version, and the block editor's next save must carry the new
                // one.
                announceVersion();
                isDirty = false;
                setDirtyState(false);
                editorHeader.dataset.saving = 'false';
                clearNoteError();

                // Reload note list (title may have changed)
                await loadNotes();

                // Update current slug if it changed, and tell the block editor.
                // Without the announcement the editor kept polling, saving and
                // queueing under the DEAD slug: its poll 404ed silently and every
                // later save failed, while the user saw a note that simply stopped
                // persisting.
                if (data.slug && data.slug !== currentSlug) {
                    currentSlug = data.slug;
                    announceNoteOpened(data.slug);
                }
            } catch (e) {
                console.error('Failed to save note:', e);
                showNoteError('Could not save this note.');
                editorHeader.dataset.saving = 'false';
            }
        }
    }

    async function createNewNote() {
        // Save current note first if dirty
        if (isDirty && currentSlug !== null) {
            await saveNote();
        }

        try {
            const resp = await fetch('/tools/notes/api/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ title: 'Untitled' }),
            });

            if (!resp.ok) {
                const err = await resp.json();
                showNoteError('Error creating note: ' + (err.error || 'Unknown'));
                return;
            }

            const data = await resp.json();
            await loadNotes();
            await selectNote(data.slug);

        } catch (e) {
            console.error('Failed to create note:', e);
        }
    }

    async function deleteCurrentNote() {
        if (!currentSlug) return;

        const confirmed = confirm('Delete this note? This cannot be undone.');
        if (!confirmed) return;

        try {
            const resp = await fetch(`/tools/notes/api/notes/${currentSlug}`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });

            if (!resp.ok) {
                const err = await resp.json();
                showNoteError('Error deleting note: ' + (err.error || 'Unknown'));
                return;
            }

            // Clean up editor
            if (mde) {
                mde.toTextArea();
                mde = null;
            }

            noteEditor.classList.add('hidden');
            noNoteSelected.classList.remove('hidden');
            if (pollTimer !== null) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            currentSlug = null;
            setToolbarEnabled(false);
            isDirty = false;
            setDirtyState(false);
            clearTimeout(saveTimer);
            saveTimer = null;

            await loadNotes();

        } catch (e) {
            console.error('Failed to delete note:', e);
        }
    }

    async function setScratchpad() {
        if (!currentSlug) return;

        try {
            const resp = await fetch(`/tools/notes/api/notes/${currentSlug}/pin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ pinned: true }),
            });

            if (!resp.ok) {
                const err = await resp.json();
                showNoteError('Error pinning note: ' + (err.error || 'Unknown'));
                return;
            }

            const data = await resp.json();
            scratchpadSlug = data.primary;
            renderNotesList(noteSearch.value.trim());
            updatePinButton();

        } catch (e) {
            console.error('Failed to set scratchpad:', e);
        }
    }

    async function clearScratchpad() {
        // Unpinning needs the slug, and there is one only when the current note is
        // the pinned one. Without a slug there is nothing to clear.
        const target = currentSlug || scratchpadSlug;
        if (!target) return;

        try {
            const resp = await fetch(`/tools/notes/api/notes/${encodeURIComponent(target)}/pin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ pinned: false }),
            });

            if (!resp.ok) {
                const err = await resp.json();
                showNoteError('Error unpinning note: ' + (err.error || 'Unknown'));
                return;
            }

            const data = await resp.json();
            scratchpadSlug = data.primary;
            renderNotesList(noteSearch.value.trim());
            updatePinButton();

        } catch (e) {
            console.error('Failed to clear scratchpad:', e);
        }
    }

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /**
     * Wire the sidebar's delegated handlers once, before any row exists.
     *
     * Delegation rather than a handler per row: the list is re-rendered on every
     * refresh, so per-row handlers would be re-created (and leaked) each time.
     */
    function initSidebar() {
        if (!notesList) {
            return;
        }
        notesList.addEventListener('click', (event) => {
            const button = event.target.closest('[data-convert-slug]');
            if (button) {
                convertFromRow(button.dataset.convertSlug, button);
            }
        });
    }

    function restartSaveTimer() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
            if (isDirty) {
                saveNote();
            }
        }, 2000);
    }

    function onTitleInput() {
        isDirty = true;
        setDirtyState(true);
        restartSaveTimer();
    }

    function onTitleBlur() {
        if (isDirty) {
            saveNote();
        }
    }

    function setDirtyState(dirty) {
        if (dirty) {
            noteTitle.classList.add('unsaved');
        } else {
            noteTitle.classList.remove('unsaved');
        }
    }

    /**
     * Whether the open note's title differs from what the server has.
     *
     * The block editor owns content, so this page's save is title-only; a PUT
     * with no title change would still bump the version and fire a rename-less
     * revision, so the no-op save is skipped rather than sent. The comparison
     * is against the sidebar list -- the last state this page loaded -- rather
     * than the fetched document, because the fetched title is what the title
     * element already shows.
     */
    function titleChanged() {
        const current = allNotes.find(n => n.slug === currentSlug) || {};
        const title = noteTitle.textContent.trim() || 'Untitled';
        return title !== (current.title || '').trim();
    }

    /**
     * Show or hide the "this note is read-only" notice.
     *
     * The markdown page has no write path, so the notice names the one edit
     * route that does exist rather than leaving the user to guess why nothing
     * happens when they type.
     */
    function showConvertHint(show) {
        if (convertHint) {
            convertHint.classList.toggle('hidden', !show);
        }
    }

    /** Surface a failure in the page, not only in the console. */
    function showNoteError(message) {
        if (!noteError) {
            return;
        }
        noteError.textContent = message || '';
        noteError.classList.toggle('hidden', !message);
    }

    function clearNoteError() {
        showNoteError('');
    }

    /**
     * Re-read the scratchpad state and re-render what depends on it.
     *
     * Called when another control changes the pin. A failed read leaves the
     * previous value in place, which renders the old marker; that is stale
     * rather than wrong, and the poll corrects it within one interval, so this
     * does not retry on its own.
     */
    async function refreshScratchpadState() {
        try {
            const resp = await fetch('/tools/notes/api/notes/scratchpad', { credentials: 'same-origin' });
            if (!resp.ok) {
                return;
            }
            const data = await resp.json();
            scratchpadSlug = data.primary;
            renderNotesList(noteSearch.value.trim());
            updatePinButton();
        } catch (e) {
            console.error('Failed to refresh scratchpad state:', e);
        }
    }

    function updatePinButton() {
        const isPinned = scratchpadSlug === currentSlug;
        const pinIcon = btnPin.querySelector('.pin-icon');

        if (isPinned) {
            btnPin.classList.add('pinned');
            btnPin.title = 'Unpin from scratchpad';
            if (pinIcon) pinIcon.textContent = '★';
            btnPin.innerHTML = '<span class="pin-icon">★</span> Unpin';
        } else {
            btnPin.classList.remove('pinned');
            btnPin.title = 'Pin as scratchpad';
            btnPin.innerHTML = '<span class="pin-icon">☆</span> Pin as Scratchpad';
        }
    }

    function formatDate(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (e) {
            return '';
        }
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }
})();