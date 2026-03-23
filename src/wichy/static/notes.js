// Notes Tool Frontend

let currentSlug = null;
let scratchpadSlug = null;
let allNotes = [];  // [{slug, title, created, updated}]
let mde = null;
let saveTimer = null;
let isDirty = false;

// DOM elements
const btnNewNote = document.getElementById('btn-new-note');
const noteSearch = document.getElementById('note-search');
const notesList = document.getElementById('notes-list');
const emptyState = document.getElementById('empty-state');
const noNoteSelected = document.getElementById('no-note-selected');
const noteEditor = document.getElementById('note-editor');
const noteTitle = document.getElementById('note-title');
const noteTitleUnsaved = document.getElementById('note-title-unsaved');
const noteTitleSaving = document.getElementById('note-title-saving');
const editorHeader = document.getElementById('editor-header');
const noteContent = document.getElementById('note-content');
const editorToolbar = document.getElementById('editor-toolbar');
const btnPin = document.getElementById('btn-pin');
const btnDelete = document.getElementById('btn-delete');

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
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

    // Delete button
    btnDelete.addEventListener('click', deleteCurrentNote);

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
        const resp = await fetch('/tools/notes/api/scratchpad-status', { credentials: 'same-origin' });
        if (resp.ok) {
            const data = await resp.json();
            scratchpadSlug = data.slug;
        }
    } catch (e) {
        console.error('Failed to fetch scratchpad status:', e);
    }
    await loadNotes();
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
            const statusResp = await fetch('/tools/notes/api/scratchpad-status', { credentials: 'same-origin' });
            if (statusResp.ok) {
                const statusData = await statusResp.json();
                scratchpadSlug = statusData.slug;
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

        item.addEventListener('click', () => selectNote(note.slug));
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                selectNote(note.slug);
            }
        });
        notesList.appendChild(item);
    });
}

async function selectNote(slug) {
    // Cancel any pending debounce
    clearTimeout(saveTimer);
    saveTimer = null;

    currentSlug = slug;

    noNoteSelected.classList.add('hidden');
    noteEditor.classList.remove('hidden');

    try {
        const resp = await fetch(`/tools/notes/api/notes/${slug}`, { credentials: 'same-origin' });
        if (!resp.ok) {
            console.error('Failed to load note');
            return;
        }
        const note = await resp.json();

        // Populate title immediately
        noteTitle.textContent = note.title || '';
        setDirtyState(false);

        // Update active state in list
        renderNotesList(noteSearch.value.trim());

        // Update pin button
        updatePinButton();

        // Destroy existing EasyMDE if any
        if (mde) {
            mde.toTextArea();
            mde = null;
        }

        // Reset the textarea
        noteContent.value = note.content || '';

        // Create new EasyMDE instance
        mde = new EasyMDE({
            element: noteContent,
            toolbar: false,
            initialValue: note.content || '',
            status: false,
        });

        // Move EasyMDE toolbar into our toolbar container
        // Note: EasyMDE renders its .editor-toolbar inside the .EasyMDE wrapper.
        // We must NOT move #editor-toolbar into itself; instead we just show it
        // since it IS the EasyMDE toolbar element.
        editorToolbar.innerHTML = '';
        const mdeContainer = mde.element.parentElement;
        const mdeToolbar = mdeContainer.querySelector('.editor-toolbar');
        if (mdeToolbar && mdeToolbar !== editorToolbar) {
            editorToolbar.innerHTML = '';
            editorToolbar.appendChild(mdeToolbar);
            mdeToolbar.style.display = '';
        }

        // Track changes
        mde.codemirror.on('change', () => {
            isDirty = true;
            setDirtyState(true);
            restartSaveTimer();
        });

        // Clear dirty flag after load
        isDirty = false;
        setDirtyState(false);

    } catch (e) {
        console.error('Failed to load note:', e);
    }
}

async function saveNote() {
    if (!currentSlug) return;

    clearTimeout(saveTimer);
    saveTimer = null;

    const title = noteTitle.textContent.trim() || 'Untitled';
    const content = mde ? mde.value() : '';

    // Mark as saving
    editorHeader.dataset.saving = 'true';

    try {
        const resp = await fetch(`/tools/notes/api/notes/${currentSlug}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ title, content }),
        });

        if (!resp.ok) {
            console.error('Failed to save note');
            editorHeader.dataset.saving = 'false';
            return;
        }

        const data = await resp.json();
        isDirty = false;
        setDirtyState(false);
        editorHeader.dataset.saving = 'false';

        // Reload note list (title may have changed)
        await loadNotes();

        // Update current slug if it changed
        if (data.slug && data.slug !== currentSlug) {
            currentSlug = data.slug;
        }

    } catch (e) {
        console.error('Failed to save note:', e);
        editorHeader.dataset.saving = 'false';
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
            body: JSON.stringify({ title: 'Untitled', content: '' }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert('Error creating note: ' + (err.error || 'Unknown'));
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
            alert('Error deleting note: ' + (err.error || 'Unknown'));
            return;
        }

        // Clean up editor
        if (mde) {
            mde.toTextArea();
            mde = null;
        }

        noteEditor.classList.add('hidden');
        noNoteSelected.classList.remove('hidden');
        currentSlug = null;
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
        const resp = await fetch('/tools/notes/api/notes/set-scratchpad', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ slug: currentSlug }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert('Error setting scratchpad: ' + (err.error || 'Unknown'));
            return;
        }

        const data = await resp.json();
        scratchpadSlug = data.slug;
        renderNotesList(noteSearch.value.trim());
        updatePinButton();

    } catch (e) {
        console.error('Failed to set scratchpad:', e);
    }
}

async function clearScratchpad() {
    try {
        const resp = await fetch('/tools/notes/api/notes/set-scratchpad', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ slug: null }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert('Error clearing scratchpad: ' + (err.error || 'Unknown'));
            return;
        }

        const data = await resp.json();
        scratchpadSlug = data.slug;
        renderNotesList(noteSearch.value.trim());
        updatePinButton();

    } catch (e) {
        console.error('Failed to clear scratchpad:', e);
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
