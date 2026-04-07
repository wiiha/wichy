/**
 * Session Map Web UI
 */

// =============================================================================
// State
// =============================================================================

let network = null;
let nodes = null;
let edges = null;
let selectedNode = null;
let pollInterval = null;
let lastErrorTime = 0;
let lastErrorMessage = "";
let lastMapData = null;
let lastMapDataStr = null; // For comparison

const nodeColors = {
  question: "#4A90D9", // Blue
  finding: "#5CB85C", // Green
  decision: "#F0AD4E", // Orange
  file: "#5BC0DE", // Cyan
  dead_end: "#D9534F", // Red
  note: "#999999", // Gray
};

const filterState = {
  question: true,
  finding: true,
  decision: true,
  file: true,
  dead_end: false,
  note: true,
};

// =============================================================================
// Error Notification
// =============================================================================

function showError(message) {
  // Suppress duplicate errors within 30 seconds
  const now = Date.now();
  if (message === lastErrorMessage && now - lastErrorTime < 30000) {
    return;
  }
  lastErrorTime = now;
  lastErrorMessage = message;

  // Remove existing error banner
  const existing = document.getElementById("error-banner");
  if (existing) existing.remove();

  const banner = document.createElement("div");
  banner.id = "error-banner";
  banner.style.cssText =
    "position:fixed;top:0;left:0;right:0;background:#fee;border:1px solid #f00;padding:10px;color:#900;z-index:1000;";
  banner.textContent = message;
  document.body.appendChild(banner);

  // Auto-remove after 5 seconds
  setTimeout(() => banner.remove(), 5000);
}

function clearError() {
  lastErrorTime = 0;
  lastErrorMessage = "";
}

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener("DOMContentLoaded", () => {
  initNetwork();
  initFilters();
  initActions();
  loadMap();
  startPolling();
});

function initNetwork() {
  const container = document.getElementById("network");

  nodes = new vis.DataSet([]);
  edges = new vis.DataSet([]);

  const data = { nodes, edges };

  const options = {
    nodes: {
      shape: "box",
      shapeProperties: {
        borderRadius: 6,
      },
      font: {
        size: 14,
        face: "Arial",
      },
      widthConstraint: {
        maximum: 200,
      },
      heightConstraint: {
        minimum: 40,
      },
      labelHighlightBold: false,
      borderWidth: 2,
      shadow: true,
    },
    edges: {
      arrows: "to",
      color: { color: "#888", highlight: "#4A90D9" },
      smooth: {
        type: "continuous",
      },
    },
    physics: {
      stabilization: { iterations: 150, fit: true },
      solver: "forceAtlas2Based",
      forceAtlas2Based: {
        gravitationalConstant: -160,
        centralGravity: 0.01,
        springLength: 200,
        springConstant: 0.05,
        avoidOverlap: 0.8,
      },
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
    },
  };

  network = new vis.Network(container, data, options);

  // Click handler for node selection
  network.on("click", (params) => {
    if (params.nodes.length > 0) {
      selectNode(params.nodes[0]);
    } else {
      deselectNode();
    }
  });
}

function initFilters() {
  document.querySelectorAll('.filters input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", (e) => {
      filterState[e.target.dataset.type] = e.target.checked;
      updateNodeVisibility();
    });
  });
}

function initActions() {
  // Extract now
  document
    .getElementById("btn-extract")
    .addEventListener("click", triggerExtraction);

  // Add note
  document
    .getElementById("btn-add-note")
    .addEventListener("click", showAddNoteModal);
  document.getElementById("btn-save-note").addEventListener("click", saveNote);
  document
    .getElementById("btn-cancel-note")
    .addEventListener("click", hideAddNoteModal);

  // Clear map
  document.getElementById("btn-clear").addEventListener("click", clearMap);

  // Node detail
  document
    .getElementById("btn-close-detail")
    .addEventListener("click", deselectNode);
  document
    .getElementById("btn-delete-node")
    .addEventListener("click", deleteSelectedNode);
}

// =============================================================================
// Data Loading
// =============================================================================

async function loadMap() {
  try {
    const [mapRes, statusRes] = await Promise.all([
      fetch("/tools/session-map/api/map"),
      fetch("/tools/session-map/api/status"),
    ]);

    // Process map response independently
    if (mapRes.ok) {
      const mapData = await mapRes.json();
      // Compare data before updating to prevent unnecessary redraws
      const newDataStr = JSON.stringify({
        nodes: mapData.nodes,
        edges: mapData.edges,
      });
      if (newDataStr !== lastMapDataStr) {
        lastMapData = mapData;
        lastMapDataStr = newDataStr;
        updateNetwork(mapData);
      }
    }

    // Status is optional - only show error if map also failed
    if (!mapRes.ok) {
      showError(`Failed to load session map: ${mapRes.status}`);
      return;
    }
    if (!statusRes.ok) {
      showError(`Failed to load status: ${statusRes.status}`);
      return;
    }

    const statusData = await statusRes.json();
    updateStatus(statusData);

    // Clear error suppression on success
    clearError();
  } catch (err) {
    console.error("Failed to load session map:", err);
    showError("Failed to load session map. Please try refreshing.");
  }
}

function updateNetwork(mapData) {
  // Save the currently selected node ID before clearing
  const previouslySelectedId = selectedNode ? selectedNode.id : null;

  // Clear existing
  nodes.clear();
  edges.clear();

  // Add nodes
  mapData.nodes.forEach((node) => {
    nodes.add({
      id: node.id,
      label: truncate(node.content, 50),
      title: node.content,
      color: nodeColors[node.type],
      type: node.type,
      data: node,
    });
  });

  // Add edges
  mapData.edges.forEach((edge) => {
    edges.add({
      id: `${edge.from}-${edge.to}`,
      from: edge.from,
      to: edge.to,
      arrows: "to",
    });
  });

  updateNodeVisibility();

  // Re-select the previously selected node if it still exists
  if (previouslySelectedId && nodes.get(previouslySelectedId)) {
    selectNode(previouslySelectedId);
  }
}

function updateStatus(statusData) {
  document.getElementById("current-turn").textContent =
    statusData.current_turn || "-";
  document.getElementById("last-extracted").textContent =
    statusData.last_extracted_turn || "-";
  document.getElementById("next-extraction").textContent =
    statusData.next_extraction_in
      ? `in ${statusData.next_extraction_in} turns`
      : "-";
}

function updateNodeVisibility() {
  nodes.forEach((node) => {
    const visible = filterState[node.type];
    nodes.update({ id: node.id, hidden: !visible });
  });
}

// =============================================================================
// Actions
// =============================================================================

async function triggerExtraction() {
  const btn = document.getElementById("btn-extract");
  btn.textContent = "Extracting...";
  btn.disabled = true;

  try {
    const res = await fetch("/tools/session-map/api/extract", {
      method: "POST",
    });

    if (!res.ok) {
      showError(`Extraction failed: HTTP ${res.status}`);
      return;
    }

    const data = await res.json();

    if (data.success) {
      loadMap();
    } else {
      // Handle both {error: ...} and {success: false, feedback: ...}
      const message = data.feedback || data.error || "Unknown error";
      showError(`Extraction failed: ${message}`);
    }
  } catch (err) {
    console.error("Extraction failed:", err);
    showError("Extraction failed: Network error");
  } finally {
    btn.textContent = "Extract Now";
    btn.disabled = false;
  }
}

function showAddNoteModal() {
  // Populate parent nodes dropdown
  const select = document.getElementById("note-parent-nodes");
  select.innerHTML = "";

  nodes.forEach((node) => {
    const option = document.createElement("option");
    option.value = node.id;
    // Truncate content for display
    const text =
      node.data.content.length > 50
        ? node.data.content.substring(0, 50) + "..."
        : node.data.content;
    option.textContent = `[${node.data.type}] ${text}`;
    option.style.color = nodeColors[node.data.type] || "#999999";
    select.appendChild(option);
  });

  document.getElementById("add-note-modal").classList.remove("hidden");
  document.getElementById("note-content").focus();
}

function hideAddNoteModal() {
  document.getElementById("add-note-modal").classList.add("hidden");
  document.getElementById("note-content").value = "";
  document.getElementById("note-parent-nodes").selectedIndex = -1;
}

async function saveNote() {
  const content = document.getElementById("note-content").value.trim();
  if (!content) return;

  // Get selected parent nodes
  const select = document.getElementById("note-parent-nodes");
  const selectedOptions = Array.from(select.selectedOptions);
  const parentIds = selectedOptions.map((opt) => opt.value);

  try {
    const body = { type: "note", content };
    if (parentIds.length > 0) {
      body.parent_ids = parentIds;
    }

    const res = await fetch("/tools/session-map/api/node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      hideAddNoteModal();
      loadMap();
    } else {
      showError(`Failed to save note: ${res.status}`);
    }
  } catch (err) {
    console.error("Failed to save note:", err);
    showError("Failed to save note.");
  }
}

async function clearMap() {
  if (!confirm("Are you sure you want to clear the session map?")) return;

  try {
    const res = await fetch("/tools/session-map/api/clear", { method: "POST" });
    if (!res.ok) {
      showError(`Failed to clear map: ${res.status}`);
      return;
    }
    const data = await res.json();
    if (data.success === false) {
      showError(data.error || "Failed to clear map");
      return;
    }
    lastMapDataStr = null; // Reset comparison state
    loadMap();
  } catch (err) {
    console.error("Failed to clear map:", err);
    showError("Failed to clear session map.");
  }
}

// =============================================================================
// Node Selection
// =============================================================================

function selectNode(nodeId) {
  const node = nodes.get(nodeId);
  if (!node) return;

  selectedNode = node;

  const detail = document.getElementById("node-detail");
  detail.classList.remove("hidden");

  document.getElementById("node-type-badge").textContent =
    node.data.type.toUpperCase();
  document.getElementById("node-type-badge").style.backgroundColor =
    nodeColors[node.data.type];
  document.getElementById("node-turn-badge").textContent =
    `Turn ${node.data.turn}`;
  document.getElementById("node-content").textContent = node.data.content;
}

function deselectNode() {
  selectedNode = null;
  document.getElementById("node-detail").classList.add("hidden");
}

async function deleteSelectedNode() {
  if (!selectedNode) return;
  if (!confirm("Delete this node?")) return;

  try {
    const res = await fetch(`/tools/session-map/api/node/${selectedNode.id}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      showError(`Failed to delete node: ${res.status}`);
      return;
    }
    deselectNode();
    loadMap();
  } catch (err) {
    console.error("Failed to delete node:", err);
    showError("Failed to delete node.");
  }
}

// =============================================================================
// Polling
// =============================================================================

function startPolling() {
  if (pollInterval) return; // Already polling
  pollInterval = setInterval(loadMap, 5000);
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

// Pause polling when tab is hidden
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPolling();
  } else {
    startPolling();
  }
});

// Cleanup on page unload
window.addEventListener("beforeunload", stopPolling);

// =============================================================================
// Utilities
// =============================================================================

function truncate(str, maxLen) {
  if (!str) return "";
  str = String(str); // Coerce to string
  if (str.length <= maxLen) return str;
  return str.substring(0, maxLen) + "...";
}
