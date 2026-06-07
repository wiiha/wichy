// Context Editor Frontend

let currentMtime = null;
let syncInterval = null;
const POLL_INTERVAL = 3000; // 3 seconds

// Threshold in pixels — if user is within this distance of the bottom, treat as "at bottom"
const SCROLL_NEAR_BOTTOM_THRESHOLD = 100;

function isScrolledNearBottom() {
    const el = elMessagesContainer;
    if (!el) return true;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= SCROLL_NEAR_BOTTOM_THRESHOLD;
}

function scrollToBottom() {
    const el = elMessagesContainer;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
}

// In-memory store of full message objects (preserves all properties)
let messagesStore = [];

// DOM elements
const elMsgCount = document.getElementById('msg-count');
const elLogCount = document.getElementById('log-count');
const elFilename = document.getElementById('filename');
const elLastSync = document.getElementById('last-sync');
const elSyncIndicator = document.getElementById('sync-indicator');
const elMessagesContainer = document.getElementById('messages-container');
const elPromptTokens = document.getElementById('prompt-tokens');
const elAutoCompactThreshold = document.getElementById('auto-compact-threshold');
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
const btnTick = document.getElementById('btn-tick');
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
        const contentText = document.getElementById('edit-content').value.trim();
        if (!contentText) {
            alert('Content is required');
            return;
        }
        
        // Parse content - handle multimodal (JSON array) vs text
        let content;
        const contentTextarea = document.getElementById('edit-content');
        const isMultimodal = contentTextarea.dataset.multimodal === 'true';
        
        if (isMultimodal) {
            // Try to parse as JSON
            try {
                const parsed = JSON.parse(contentText);
                if (Array.isArray(parsed)) {
                    content = parsed;
                } else {
                    alert('Multimodal content must be a JSON array');
                    return;
                }
            } catch (e) {
                alert('Invalid JSON for multimodal content: ' + e.message);
                return;
            }
        } else {
            // Regular text content
            content = contentText;
        }
        
        // Get the original message from store and merge edited fields
        const originalMsg = messagesStore[index];
        const updatedMsg = { ...originalMsg, role, content };
        
        // Clean up the multimodal hint
        const hintEl = document.getElementById('multimodal-hint');
        if (hintEl) hintEl.remove();
        
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
      btnTick.addEventListener('click', async () => {
          if (!confirm('Increment tick on all entries?')) {
              return;
          }
          try {
              const resp = await fetch('/tools/context/api/tick', {
                  method: 'POST',
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

    startTaskAgentsPolling();
    setupTaskAgentModalListeners();});

async function fetchStatus() {
    try {
        const resp = await fetch('/tools/context/api/status');
        if (resp.ok) {
            const data = await resp.json();
            elMsgCount.textContent = data.message_count;
            elLogCount.textContent = data.log_count;
            elFilename.textContent = data.filename || '-';
            currentMtime = data.mtime;
            elPromptTokens.textContent = (data.current_prompt_tokens !== null && data.current_prompt_tokens !== undefined) ? data.current_prompt_tokens : '-';
            elAutoCompactThreshold.textContent = (data.auto_compact_threshold !== null && data.auto_compact_threshold !== undefined) ? data.auto_compact_threshold : '-';
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
            const wasAtBottom = isScrolledNearBottom();
            messagesStore = messages;
            renderMessages(messages);
            if (wasAtBottom) {
                scrollToBottom();
            }
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
        
        // Handle multimodal content (array) vs text content (string)
        const isMultimodal = Array.isArray(content);
        let contentText = '';
        let imageCount = 0;
        
        if (isMultimodal) {
            // Extract text parts and count images
            const textParts = [];
            content.forEach(part => {
                if (part.type === 'text') {
                    textParts.push(part.text || '');
                } else if (part.type === 'image_url') {
                    imageCount++;
                }
            });
            contentText = textParts.join('\n');
        } else {
            contentText = content;
        }
        
        const contentLength = isTruncated ? msg._truncated_from.length : contentText.length;
        
        // Build the display HTML
        let html = `<div class="message-item" data-index="${idx}">`;
        html += '<div class="message-header">';
        
        // Role badge
        html += `<span class="message-role ${role}">${escapeHtml(role)}</span>`;
          
          // Tick counter
          if (msg._tick !== undefined) {
              html += `<span class="tick-badge" title="Tick">${msg._tick}</span>`;
          }        
        // Multimodal indicator
        if (isMultimodal && imageCount > 0) {
            html += `<span class="multimodal-badge" title="Contains ${imageCount} image(s)">🖼 ${imageCount} image${imageCount > 1 ? 's' : ''}</span>`;
        }
        
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
        html += `<div class="message-content">${escapeHtml(contentText)}</div>`;
        
        // Image previews for multimodal content
        if (isMultimodal && imageCount > 0) {
            html += '<div class="image-previews">';
            content.forEach(part => {
                if (part.type === 'image_url' && part.image_url?.url) {
                    const url = part.image_url.url;
                    if (url.startsWith('data:image')) {
                        html += `<img src="${escapeHtml(url)}" alt="Image" class="content-image-preview" />`;
                    }
                }
            });
            html += '</div>';
        }
        
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
        
        html += `<button class="btn-delete" onclick="deleteMessage(${idx})">Delete</button>`;
        
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
    
    // Handle multimodal content - convert to JSON string for editing
    const content = msg.content;
    if (Array.isArray(content)) {
        // Multimodal content - show as JSON for editing
        document.getElementById('edit-content').value = JSON.stringify(content, null, 2);
        // Mark as multimodal for saving
        const contentTextarea = document.getElementById('edit-content');
        contentTextarea.dataset.multimodal = 'true';
        // Show a hint above the textarea
        let hintEl = document.getElementById('multimodal-hint');
        if (!hintEl) {
            hintEl = document.createElement('div');
            hintEl.id = 'multimodal-hint';
            hintEl.className = 'multimodal-hint';
            hintEl.innerHTML = '⚠️ Multimodal content (images). Edit JSON carefully or remove images to convert to text.';
            contentTextarea.parentNode.insertBefore(hintEl, contentTextarea);
        }
    } else {
        // Regular text content
        document.getElementById('edit-content').value = content || '';
        document.getElementById('edit-content').dataset.multimodal = 'false';
        const hintEl = document.getElementById('multimodal-hint');
        if (hintEl) hintEl.remove();
    }

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

async function deleteMessage(index) {
    const msg = messagesStore[index];
    if (!msg) return;

    if (!confirm(`Delete this message?\nRole: ${msg.role}\nThis cannot be undone.`)) {
        return;
    }

    try {
        const resp = await fetch(`/tools/context/api/messages/${index}`, {
            method: 'DELETE',
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
                const wasAtBottom = isScrolledNearBottom();
                await fetchMessages();
                if (wasAtBottom) {
                    scrollToBottom();
                }
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


// ============================================================================
// Task Agents Panel
// ============================================================================

const TASK_AGENTS_POLL_INTERVAL = 3000;
let taskAgentsInterval = null;
let _taModalAgentId = null;
let modalContextInterval = null;
let _lastModalMsgCount = 0;

// Track which cards are expanded so re-renders preserve state
const _expandedAgentIds = new Set();

function startTaskAgentsPolling() {
    fetchTaskAgents();
    taskAgentsInterval = setInterval(fetchTaskAgents, TASK_AGENTS_POLL_INTERVAL);
}

async function fetchTaskAgents() {
    try {
        const resp = await fetch('/tools/context/api/task-agents');
        if (!resp.ok) throw new Error('Failed to fetch task agents');
        const data = await resp.json();
        renderTaskAgents(data.agents || []);
    } catch (err) {
        console.error('Task agents poll error:', err);
    }
}

function renderTaskAgents(agents) {
    const emptyEl = document.getElementById('task-agents-empty');
    const listEl = document.getElementById('task-agents-list');
    if (!agents.length) {
        emptyEl.classList.remove('hidden');
        listEl.classList.add('hidden');
        listEl.innerHTML = '';
        _expandedAgentIds.clear();
        return;
    }
    // Preserve expanded state before rebuild
    listEl.querySelectorAll('.ta-card').forEach(card => {
        const details = card.querySelector('.ta-card-details');
        if (details && !details.classList.contains('hidden')) {
            _expandedAgentIds.add(card.dataset.agentId);
        }
    });

    emptyEl.classList.add('hidden');
    listEl.classList.remove('hidden');
    listEl.innerHTML = agents.map(agent => `
        <div class="ta-card" data-agent-id="${escapeHtml(agent.id)}">
            <div class="ta-card-header">
                <span class="ta-name">${escapeHtml(agent.name)}</span>
                <span class="ta-status ta-status-${agent.status}">${escapeHtml(agent.status)}</span>
            </div>
            <div class="ta-card-details ${_expandedAgentIds.has(agent.id) ? '' : 'hidden'}">
                <p class="text-sm">${escapeHtml(agent.description || '')}</p>
                <p class="text-sm"><strong>Model:</strong> ${escapeHtml(agent.model)}</p>
                <p class="text-sm"><strong>Turns:</strong> ${agent.turns_used}${agent.turns_limit ? ' / ' + agent.turns_limit : ''}</p>
                <div class="ta-card-actions">
                    <button class="btn btn-secondary btn-sm ta-btn-view">View Context</button>
                    <button class="btn btn-danger btn-sm ta-btn-stop">Stop</button>
                </div>
            </div>
        </div>
    `).join('');

    // Attach click handlers
    listEl.querySelectorAll('.ta-card-header').forEach(hdr => {
        hdr.addEventListener('click', () => {
            const details = hdr.parentElement.querySelector('.ta-card-details');
            details.classList.toggle('hidden');
        });
    });
    listEl.querySelectorAll('.ta-btn-stop').forEach(btn => {
        btn.addEventListener('click', e => {
            const id = e.target.closest('.ta-card').dataset.agentId;
            requestStopAgent(id);
        });
    });
    listEl.querySelectorAll('.ta-btn-view').forEach(btn => {
        btn.addEventListener('click', e => {
            const id = e.target.closest('.ta-card').dataset.agentId;
            openAgentContextModal(id);
        });
    });
}

// ---------------------------------------------------------------------------
// Modal Context Viewer — auto-refreshes every 3s while open
// ---------------------------------------------------------------------------

function isModalScrolledNearBottom() {
    const el = document.getElementById('ta-context-body');
    if (!el) return true;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= SCROLL_NEAR_BOTTOM_THRESHOLD;
}

function startModalContextPolling(agentId) {
    _taModalAgentId = agentId;
    _lastModalMsgCount = 0;
    pollModalContext(); // initial load
    modalContextInterval = setInterval(pollModalContext, TASK_AGENTS_POLL_INTERVAL);
}


/**
 * Render a single message for the task agent context modal.
 * Mirrors the root context editor's rendering:
 * - tool_calls are shown with name + formatted arguments
 * - content preserves newlines via CSS `white-space: pre-wrap`
 * - tool_call_id badge shown for tool responses
 */
function renderModalMessage(m) {
    const role = escapeHtml(m.role || 'unknown');
    const content = m.content || '';
    const toolCalls = m.tool_calls || null;
    const toolCallId = m.tool_call_id || null;

    let html = `\n        <div class="ta-msg">\n            <div class="ta-msg-role">${role}</div>\n`;

    // Content text
    html += `            <div class="ta-msg-content">${escapeHtml(String(content))}</div>\n`;

    // Tool call ID badge (for tool responses)
    if (toolCallId) {
        html += `            <div class="ta-msg-toolcall-id">🔗 ${escapeHtml(toolCallId)}</div>\n`;
    }

    // Tool calls section (for assistant messages that initiated tool calls)
    if (toolCalls && Array.isArray(toolCalls) && toolCalls.length > 0) {
        html += '            <div class="ta-msg-toolcalls">\n';
        toolCalls.forEach(tc => {
            const fnName = tc.function?.name || 'unknown';
            const fnArgs = tc.function?.arguments || '{}';
            const tcId = tc.id || '';
            html += '                <div class="ta-msg-toolcall-item">\n';
            html += `                    <div class="ta-msg-toolcall-name">🔧 ${escapeHtml(fnName)}</div>\n`;
            html += `                    <div class="ta-msg-toolcall-id-small">ID: ${escapeHtml(tcId)}</div>\n`;
            try {
                const parsedArgs = typeof fnArgs === 'string' ? JSON.parse(fnArgs) : fnArgs;
                html += `                    <pre class="ta-msg-toolcall-args">${escapeHtml(JSON.stringify(parsedArgs, null, 2))}</pre>\n`;
            } catch (e) {
                html += `                    <pre class="ta-msg-toolcall-args">${escapeHtml(fnArgs)}</pre>\n`;
            }
            html += '                </div>\n';
        });
        html += '            </div>\n';
    }

    html += '        </div>\n';
    return html;
}
function stopModalContextPolling() {
    if (modalContextInterval) {
        clearInterval(modalContextInterval);
        modalContextInterval = null;
    }
    _taModalAgentId = null;
    _lastModalMsgCount = 0;
}

async function pollModalContext() {
    const modal = document.getElementById('ta-context-modal');
    if (modal.classList.contains('hidden') || !_taModalAgentId) return;

    const body = document.getElementById('ta-context-body');

    try {
        const resp = await fetch(`/tools/context/api/task-agents/${encodeURIComponent(_taModalAgentId)}/context`);
        if (!resp.ok) throw new Error('Failed to load context');
        const data = await resp.json();
        const msgs = data.messages || [];

        // Only re-render if message count changed
        if (msgs.length === _lastModalMsgCount) return;
        _lastModalMsgCount = msgs.length;

        if (!msgs.length) {
            body.innerHTML = '<p class="text-sm">No messages.</p>';
            return;
        }

        const wasAtBottom = isModalScrolledNearBottom();
          body.innerHTML = msgs.map(m => renderModalMessage(m)).join('');
        if (wasAtBottom) {
            body.scrollTop = body.scrollHeight;
        }
    } catch (err) {
        // Don't overwrite on error — might be transient
        console.error('Modal context poll error:', err);
    }
}

async function openAgentContextModal(agentId) {
    const modal = document.getElementById('ta-context-modal');
    const body = document.getElementById('ta-context-body');
    const title = document.getElementById('ta-modal-title');
    const steerFooter = document.getElementById('ta-steer-footer');
    const steerInput = document.getElementById('ta-steer-input');

    body.innerHTML = '<p class="text-sm">Loading...</p>';
    steerFooter.classList.remove('hidden');
    steerInput.value = '';
    modal.classList.remove('hidden');

    // Find agent name from sidebar card
    const card = document.querySelector(`.ta-card[data-agent-id="${agentId}"]`);
    const name = card ? card.querySelector('.ta-name').textContent : agentId;
    title.textContent = `Context: ${name}`;

    startModalContextPolling(agentId);
}

// ---------------------------------------------------------------------------
// Stop + Steer
// ---------------------------------------------------------------------------

async function requestStopAgent(agentId) {
    const card = document.querySelector(`.ta-card[data-agent-id="${agentId}"]`);
    const name = card ? card.querySelector('.ta-name').textContent : agentId;
    if (!confirm(`Stop agent "${name}"? It will finish its current task, then summarize.`)) return;
    try {
        const resp = await fetch(`/tools/context/api/task-agents/${encodeURIComponent(agentId)}/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.error || 'Failed to stop agent');
            return;
        }
        // Status will update on next poll
    } catch (err) {
        alert('Network error: ' + err.message);
    }
}

async function submitSteer(agentId, content) {
    try {
        const resp = await fetch(`/tools/context/api/task-agents/${encodeURIComponent(agentId)}/steer`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: 'user', content }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.error || 'Failed to inject steer');
            return;
        }
        const data = await resp.json();
        if (data.status === 'injected') {
            // Flash "Steered!" in the send button
            const sendBtn = document.getElementById('ta-steer-send');
            if (sendBtn) {
                const original = sendBtn.textContent;
                sendBtn.textContent = 'Steered!';
                setTimeout(() => { sendBtn.textContent = original; }, 1500);
            }
            // The modal context will auto-refresh via pollModalContext
        }
    } catch (err) {
        alert('Network error: ' + err.message);
    }
}

// ---------------------------------------------------------------------------
// Modal listeners (called from DOMContentLoaded)
// ---------------------------------------------------------------------------

function setupTaskAgentModalListeners() {
    document.getElementById('ta-modal-close').addEventListener('click', () => {
        document.getElementById('ta-context-modal').classList.add('hidden');
        stopModalContextPolling();
    });
    document.getElementById('ta-context-modal').addEventListener('click', e => {
        if (e.target.id === 'ta-context-modal') {
            e.target.classList.add('hidden');
            stopModalContextPolling();
        }
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            document.getElementById('ta-context-modal').classList.add('hidden');
            stopModalContextPolling();
        }
    });
    document.getElementById('ta-steer-send').addEventListener('click', () => {
        const input = document.getElementById('ta-steer-input');
        const text = input.value.trim();
        if (!text || !_taModalAgentId) return;
        submitSteer(_taModalAgentId, text);
        input.value = '';
    });
}