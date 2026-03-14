// Context Editor Frontend

let currentMtime = null;
let syncInterval = null;
const POLL_INTERVAL = 3000; // 3 seconds

// DOM elements
const elMsgCount = document.getElementById('msg-count');
const elLogCount = document.getElementById('log-count');
const elFilename = document.getElementById('filename');
const elLastSync = document.getElementById('last-sync');
const elSyncIndicator = document.getElementById('sync-indicator');
const elMessagesContainer = document.getElementById('messages-container');
const elAddForm = document.getElementById('add-form');
const elEditForm = document.getElementById('edit-form');
const elConflictModal = document.getElementById('conflict-modal');

// Buttons
const btnRefresh = document.getElementById('btn-refresh');
const btnAdd = document.getElementById('btn-add');
const btnSaveAdd = document.getElementById('btn-save-add');
const btnCancelAdd = document.getElementById('btn-cancel-add');
const btnSaveEdit = document.getElementById('btn-save-edit');
const btnCancelEdit = document.getElementById('btn-cancel-edit');
const btnDrop = document.getElementById('btn-drop');
const btnRefreshConflict = document.getElementById('btn-refresh-conflict');

// Inputs
const dropCountInput = document.getElementById('drop-count');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    fetchStatus();
    fetchMessages();
    startAutoSync();

    btnRefresh.addEventListener('click', () => {
        fetchMessages();
        fetchStatus();
    });

    btnAdd.addEventListener('click', () => {
        elAddForm.classList.remove('hidden');
        elEditForm.classList.add('hidden');
        document.getElementById('new-content').focus();
    });

    btnCancelAdd.addEventListener('click', () => {
        elAddForm.classList.add('hidden');
    });

    btnSaveAdd.addEventListener('click', async () => {
        const role = document.getElementById('new-role').value;
        const content = document.getElementById('new-content').value.trim();
        if (!content) {
            alert('Content is required');
            return;
        }
        try {
            const resp = await fetch('/tools/context/api/messages', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, content }),
            });
            if (resp.ok) {
                elAddForm.classList.add('hidden');
                document.getElementById('new-content').value = '';
                fetchMessages();
                fetchStatus();
            } else {
                const err = await resp.json();
                alert('Error: ' + (err.error || 'Unknown'));
            }
        } catch (e) {
            alert('Network error: ' + e);
        }
    });

    btnCancelEdit.addEventListener('click', () => {
        elEditForm.classList.add('hidden');
    });

    btnSaveEdit.addEventListener('click', async () => {
        const index = parseInt(document.getElementById('edit-index').value);
        const role = document.getElementById('edit-role').value;
        const content = document.getElementById('edit-content').value.trim();
        if (!content) {
            alert('Content is required');
            return;
        }
        try {
            const resp = await fetch(`/tools/context/api/messages/${index}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, content }),
            });
            if (resp.ok) {
                elEditForm.classList.add('hidden');
                fetchMessages();
                fetchStatus();
            } else if (resp.status === 409) {
                showConflictModal();
                fetchMessages();
            } else {
                const err = await resp.json();
                alert('Error: ' + (err.error || 'Unknown'));
            }
        } catch (e) {
            alert('Network error: ' + e);
        }
    });

    btnDrop.addEventListener('click', async () => {
        const n = parseInt(dropCountInput.value) || 1;
        if (!confirm(`Drop last ${n} message(s)? This cannot be undone.`)) {
            return;
        }
        try {
            const resp = await fetch('/tools/context/api/drop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ n }),
            });
            if (resp.ok) {
                fetchMessages();
                fetchStatus();
            } else {
                const err = await resp.json();
                alert('Error: ' + (err.error || 'Unknown'));
            }
        } catch (e) {
            alert('Network error: ' + e);
        }
    });

    btnRefreshConflict.addEventListener('click', () => {
        elConflictModal.classList.add('hidden');
        fetchMessages();
        fetchStatus();
    });
});

async function fetchStatus() {
    try {
        const resp = await fetch('/tools/context/api/status');
        if (resp.ok) {
            const data = await resp.json();
            elMsgCount.textContent = data.message_count;
            elLogCount.textContent = data.log_count;
            elFilename.textContent = data.filename || '-';
            currentMtime = data.mtime;
        } else {
            console.error('Failed to fetch status');
        }
    } catch (e) {
        console.error('Status fetch error:', e);
    }
}

async function fetchMessages() {
    setSyncState('syncing');
    try {
        const resp = await fetch('/tools/context/api/messages');
        if (resp.ok) {
            const messages = await resp.json();
            renderMessages(messages);
            setSyncState('idle');
        } else if (resp.status === 404) {
            elMessagesContainer.innerHTML = '<div class="empty-state"><p>No active context</p></div>';
            setSyncState('idle');
        } else {
            console.error('Failed to fetch messages');
            setSyncState('idle');
        }
    } catch (e) {
        console.error('Messages fetch error:', e);
        setSyncState('idle');
    }
}

function renderMessages(messages) {
    if (messages.length === 0) {
        elMessagesContainer.innerHTML = '<div class="empty-state"><p>No messages yet.</p></div>';
        return;
    }

    elMessagesContainer.innerHTML = messages.map((msg, idx) => {
        return `
        <div class="message-item" data-index="${idx}">
            <div class="message-header">
                <span class="message-role ${msg.role}">${escapeHtml(msg.role)}</span>
            </div>
            <div class="message-content">${escapeHtml(msg.content)}</div>
            <div class="message-actions">
                <button onclick="startEdit(${idx})">Edit</button>
            </div>
        </div>
        `;
    }).join('');
}

function startEdit(index) {
    // Fetch specific message? We have it in DOM, but let's get from current render
    const msgItem = document.querySelector(`.message-item[data-index="${index}"]`);
    if (!msgItem) return;

    const role = msgItem.querySelector('.message-role').textContent;
    const content = msgItem.querySelector('.message-content').textContent;

    document.getElementById('edit-index').value = index;
    document.getElementById('edit-role').value = role;
    document.getElementById('edit-content').value = content;

    elAddForm.classList.add('hidden');
    elEditForm.classList.remove('hidden');
    document.getElementById('edit-content').focus();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function setSyncState(state) {
    elSyncIndicator.className = 'indicator ' + state;
    switch (state) {
        case 'syncing':
            elSyncIndicator.textContent = '● Syncing...';
            break;
        case 'conflict':
            elSyncIndicator.textContent = '● Conflict!';
            break;
        default:
            elSyncIndicator.textContent = '● Idle';
    }
    if (state === 'idle' || state === 'conflict') {
        elLastSync.textContent = new Date().toLocaleTimeString();
    }
}

function showConflictModal() {
    elConflictModal.classList.remove('hidden');
    setSyncState('conflict');
}

function startAutoSync() {
    syncInterval = setInterval(async () => {
        await checkForChanges();
    }, POLL_INTERVAL);
}

async function checkForChanges() {
    try {
        const resp = await fetch('/tools/context/api/status');
        if (resp.ok) {
            const data = await resp.json();
            if (currentMtime !== null && data.mtime !== currentMtime) {
                // File changed externally! Reload messages
                console.log('Context file changed, reloading...');
                await fetchMessages();
            }
            // Update status count if changed
            if (parseInt(elMsgCount.textContent) !== data.message_count) {
                fetchStatus(); // Refresh counts
            }
        }
    } catch (e) {
        console.error('Polling error:', e);
    }
}
