// Context Editor Frontend

let currentMtime = null;
let syncInterval = null;
const POLL_INTERVAL = 3000; // 3 seconds

// In-memory store of full message objects (preserves all properties)
let messagesStore = [];

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
        
        // Get the original message from store and merge edited fields
        const originalMsg = messagesStore[index];
        const updatedMsg = { ...originalMsg, role, content };
        
        try {
            const resp = await fetch(`/tools/context/api/messages/${index}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedMsg),
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
            // Store full message objects in memory (preserves all properties)
            messagesStore = messages;
            renderMessages(messages);
            setSyncState('idle');
        } else if (resp.status === 404) {
            messagesStore = [];
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
        const role = msg.role || 'unknown';
        const content = msg.content || '';
        const toolCalls = msg.tool_calls || null;
        const toolCallId = msg.tool_call_id || null;
        const isTruncated = msg._truncated_from !== undefined;
        const contentLength = isTruncated ? msg._truncated_from.length : content.length;
        
        // Build the display HTML
        let html = `<div class="message-item" data-index="${idx}">`;
        html += '<div class="message-header">';
        
        // Role badge
        html += `<span class="message-role ${role}">${escapeHtml(role)}</span>`;
        
        // Content length indicator
        if (contentLength > 100) {
            html += `<span class="content-length">${contentLength} chars</span>`;
        }
        
        // Truncated indicator
        if (isTruncated) {
            html += `<span class="truncated-badge" title="Content truncated, original stored">TRUNCATED</span>`;
        }
        
        // Tool call ID badge (for tool responses)
        if (toolCallId) {
            html += `<span class="tool-call-id" title="Tool Call ID">🔗 ${escapeHtml(toolCallId.substring(0, 12))}...</span>`;
        }
        
        html += '</div>';
        
        // Content
        html += `<div class="message-content">${escapeHtml(content)}</div>`;
        
        // Tool calls section (view-only)
        if (toolCalls && Array.isArray(toolCalls) && toolCalls.length > 0) {
            html += '<div class="tool-calls-section">';
            html += '<div class="tool-calls-header">Tool Calls (view-only)</div>';
            toolCalls.forEach(tc => {
                const fnName = tc.function?.name || 'unknown';
                const fnArgs = tc.function?.arguments || '{}';
                const tcId = tc.id || '';
                html += `<div class="tool-call-item">`;
                html += `<div class="tool-call-name">${escapeHtml(fnName)}</div>`;
                html += `<div class="tool-call-id-small">ID: ${escapeHtml(tcId)}</div>`;
                try {
                    const parsedArgs = typeof fnArgs === 'string' ? JSON.parse(fnArgs) : fnArgs;
                    html += `<pre class="tool-call-args">${escapeHtml(JSON.stringify(parsedArgs, null, 2))}</pre>`;
                } catch (e) {
                    html += `<pre class="tool-call-args">${escapeHtml(fnArgs)}</pre>`;
                }
                html += '</div>';
            });
            html += '</div>';
        }
        
        // Action buttons
        html += '<div class="message-actions">';
        html += `<button onclick="startEdit(${idx})">Edit</button>`;
        
        // Truncate/Expand buttons
        if (isTruncated) {
            html += `<button class="btn-expand" onclick="expandMessage(${idx})">Expand</button>`;
        } else if (contentLength > 250) {
            html += `<button class="btn-truncate" onclick="truncateMessage(${idx})">Truncate</button>`;
        }
        
        html += '</div>';
        
        html += '</div>';
        return html;
    }).join('');
}

function startEdit(index) {
    // Use stored message object (preserves all properties)
    const msg = messagesStore[index];
    if (!msg) return;

    document.getElementById('edit-index').value = index;
    document.getElementById('edit-role').value = msg.role || 'user';
    document.getElementById('edit-content').value = msg.content || '';

    elAddForm.classList.add('hidden');
    elEditForm.classList.remove('hidden');
    document.getElementById('edit-content').focus();
}

async function truncateMessage(index) {
    const msg = messagesStore[index];
    if (!msg) return;
    
    if (!confirm('Truncate this message to 200 characters?\nThe original content will be preserved and can be restored.')) {
        return;
    }
    
    try {
        const resp = await fetch(`/tools/context/api/messages/${index}/truncate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_chars: 200 }),
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
}

async function expandMessage(index) {
    const msg = messagesStore[index];
    if (!msg) return;
    
    if (!confirm('Restore this message to its original content?')) {
        return;
    }
    
    try {
        const resp = await fetch(`/tools/context/api/messages/${index}/expand`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
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