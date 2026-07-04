/**
 * Activity Graph widget.
 *
 * Renders a read-only SVG tree of root agent -> task agents -> tool calls.
 *
 * @param {HTMLElement} container
 * @param {object} [options]
 * @returns {{ element: SVGSVGElement, refresh: () => Promise<void>, destroy: () => void }}
 */
export default function createActivityGraph(container, options = {}) {
  if (!container) {
    throw new Error("createActivityGraph: container is required");
  }

  const width = options.width || 1800;
  const height = options.height || 1000;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.classList.add("ag-svg");

  const rootGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  rootGroup.classList.add("ag-root-group");
  svg.appendChild(rootGroup);

  const edgesGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  edgesGroup.classList.add("ag-edges");
  const nodesGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  nodesGroup.classList.add("ag-nodes");
  rootGroup.appendChild(edgesGroup);
  rootGroup.appendChild(nodesGroup);


    const controls = document.createElement("div");
    controls.classList.add("ag-controls");

    const refreshBtn = document.createElement("button");
    refreshBtn.classList.add("btn", "btn-secondary", "btn-sm", "ag-refresh-btn");
    refreshBtn.textContent = "Refresh";
    refreshBtn.title = "Refresh activity graph now";
    refreshBtn.addEventListener("click", () => {
      refresh().catch((err) => showError(`Refresh failed: ${err.message || err}`));
    });
    controls.appendChild(refreshBtn);

    const pollToggle = document.createElement("label");
    pollToggle.classList.add("ag-poll-toggle");
    const pollCheckbox = document.createElement("input");
    pollCheckbox.type = "checkbox";
    pollCheckbox.checked = true;
    pollCheckbox.classList.add("ag-poll-checkbox");
    pollToggle.appendChild(pollCheckbox);
    const pollLabel = document.createElement("span");
    pollLabel.textContent = "Auto-refresh";
    pollToggle.appendChild(pollLabel);
    controls.appendChild(pollToggle);

    container.appendChild(controls);
  const emptyState = document.createElement("div");
  emptyState.classList.add("ag-empty-state");
  emptyState.innerHTML = `
    <div class="ag-empty-state-icon">🔥</div>
    <div class="ag-empty-state-title">No activity yet</div>
    <div class="ag-empty-state-description">
      Run a tool or task agent while wichy is in server mode. Activity appears automatically every few seconds.
    </div>
  `;

  const errorBanner =
    document.getElementById("activity-graph-error") ||
    (() => {
      const el = document.createElement("div");
      el.classList.add("ag-error-banner");
      el.hidden = true;
      container.parentNode.insertBefore(el, container.nextSibling);
      return el;
    })();

  const tooltip = document.createElement("div");
  tooltip.classList.add("ag-tooltip");
  tooltip.hidden = true;
  container.appendChild(tooltip);

  container.appendChild(svg);
  container.appendChild(emptyState);

  let graphData = { nodes: [], edges: [] };
  const collapsedAgents = new Set();
  const transform = { x: 0, y: 0, scale: 1 };
  let isDragging = false;
  let dragStart = { x: 0, y: 0 };
  const listeners = [];

  function on(target, type, handler, opts) {
    target.addEventListener(type, handler, opts);
    listeners.push(() => target.removeEventListener(type, handler, opts));
  }

  function showError(message) {
    if (!message) {
      errorBanner.hidden = true;
      errorBanner.textContent = "";
      return;
    }
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { method: "GET", credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`${url} returned ${response.status}`);
    }
    return response.json();
  }

  async function withConcurrencyLimit(items, limit, fn) {
    const results = new Array(items.length);
    let index = 0;
    async function worker() {
      while (index < items.length) {
        const i = index++;
        results[i] = await fn(items[i], i);
      }
    }
    const workers = [];
    for (let i = 0; i < Math.min(limit, items.length); i++) {
      workers.push(worker());
    }
    await Promise.all(workers);
    return results;
  }

  function truncate(str, maxLen = 80) {
    if (!str) return "";
    return str.length > maxLen ? str.slice(0, maxLen) + "…" : str;
  }

  function basename(path) {
    if (!path) return "";
    return (path.split(/[\\/]/).pop() || "").replace(/\.jsonl?$/i, "");
  }

  function formatArgs(args) {
    if (!args || typeof args !== "object") return "";
    const text = JSON.stringify(args, null, 1);
    return text.length > 400 ? text.slice(0, 400) + "…" : text;
  }

  function tryParseArgs(toolCall) {
    const raw =
      (toolCall.function && toolCall.function.arguments) ||
      toolCall.arguments ||
      toolCall.args;
    if (typeof raw === "string") {
      try {
        return JSON.parse(raw);
      } catch (_e) {
        return raw;
      }
    }
    return raw;
  }

  function buildToolLabel(toolCall) {
    const name =
      (toolCall.function && toolCall.function.name) || toolCall.name || "Tool";
    const args = tryParseArgs(toolCall);
    let detail = "";
    if (args) {
      if (args.command) detail = truncate(args.command, 40);
      else if (args.path) detail = truncate(args.path, 40);
      else if (args.query) detail = truncate(args.query, 40);
      else if (args.url) detail = truncate(args.url, 40);
      else if (args.message) detail = truncate(args.message, 40);
      else {
        const first = Object.values(args)[0];
        if (typeof first === "string") detail = truncate(first, 40);
      }
    }
    return detail ? `${name}: ${detail}` : name;
  }

  function extractToolCallsFromEntry(entry) {
    if (!entry || entry.role !== "assistant") return [];
    const raw = entry.tool_calls;
    if (!Array.isArray(raw)) return [];
    return raw;
  }

  function buildNodesFromData(rootContext, subAgents, subAgentContexts) {
    const nodes = [];
    const edges = [];
    const errors = [];

    nodes.push({
      id: "root",
      type: "root",
      label: "Root Agent",
      parentId: null,
      timestamp: null,
      data: {},
    });

    const rootEntries = rootContext.entries || [];

    // Index sub-agents by their id.
    const agentsById = new Map();
    (subAgents.agents || []).forEach((agent) => {
      if (agent && agent.id) agentsById.set(agent.id, agent);
    });

    // Record context filename for each agent.
    subAgentContexts.forEach(({ id, context }) => {
      const agent = agentsById.get(id);
      if (agent && context && context.filename) {
        agent._contextFilename = context.filename;
      }
    });

    // Map context filename (basename) -> agent id.
    const contextFileToAgentId = new Map();
    agentsById.forEach((agent, agentId) => {
      if (agent._contextFilename) {
        contextFileToAgentId.set(basename(agent._contextFilename), agentId);
      }
    });

    // Create task nodes from task_agent_started logs.
    const taskAgentMap = new Map();
    rootEntries.forEach((entry) => {
      if (entry.type === "log" && entry.event === "task_agent_started") {
        const contextFile = entry.task_context_file || "";
        const contextBasename = basename(contextFile);
        const agentId = contextFileToAgentId.get(contextBasename) || contextBasename;
        const agent = agentsById.get(agentId);
        taskAgentMap.set(agentId, {
          id: agentId,
          type: "task",
          label: agent ? agent.name : entry.task_agent_type || agentId,
          parentId: "root",
          timestamp: entry.timestamp || (agent && agent.started_at) || null,
          description: entry.description || (agent && agent.description) || "",
          status: (agent && agent.status) || "stopped",
          turnsUsed: (agent && agent.turns_used) || null,
          turnsLimit: (agent && agent.turns_limit) || null,
          data: { log: entry, agent },
        });
      }
    });

    // Add sub-agents not linked by a log entry.
    agentsById.forEach((agent) => {
      if (taskAgentMap.has(agent.id)) return;
      taskAgentMap.set(agent.id, {
        id: agent.id,
        type: "task",
        label: agent.name || agent.id,
        parentId: "root",
        timestamp: agent.started_at || null,
        description: agent.description || "",
        status: agent.status || "stopped",
        turnsUsed: agent.turns_used || null,
        turnsLimit: agent.turns_limit || null,
        data: { agent },
      });
    });

    taskAgentMap.forEach((node) => {
      nodes.push(node);
      edges.push({ id: `edge-root-${node.id}`, source: "root", target: node.id });
    });

    // Root-level tool calls (skip functions.task, which launches sub-agents).
    rootEntries.forEach((entry) => {
      extractToolCallsFromEntry(entry).forEach((tc, idx) => {
        const name = ((tc.function && tc.function.name) || tc.name || "").toLowerCase();
        if (name === "task") return;
        const id = `tool-root-${tc.id || idx}`;
        nodes.push({
          id,
          type: "tool",
          label: buildToolLabel(tc),
          parentId: "root",
          timestamp: entry.timestamp || null,
          args: tryParseArgs(tc),
          data: { ...tc, entry },
        });
        edges.push({ id: `edge-root-${id}`, source: "root", target: id });
      });
    });

    // Per-task-agent tool calls.
    subAgentContexts.forEach(({ id, context, error }) => {
      if (error || !context || !context.entries) {
        if (error) errors.push(`Could not load context for ${id}: ${error}`);
        return;
      }
      const taskId = id;
      if (!taskAgentMap.has(taskId)) {
        taskAgentMap.set(taskId, {
          id: taskId,
          type: "task",
          label: taskId,
          parentId: "root",
          timestamp: (context.entries[0] || {}).timestamp || null,
          description: "",
          status: "stopped",
          turnsUsed: null,
          turnsLimit: null,
          data: { context_file: context.filename },
        });
        const node = taskAgentMap.get(taskId);
        nodes.push(node);
        edges.push({ id: `edge-root-${node.id}`, source: "root", target: node.id });
      }
      (context.entries || []).forEach((entry) => {
        extractToolCallsFromEntry(entry).forEach((tc, idx) => {
          const toolId = `tool-${taskId}-${tc.id || idx}`;
          nodes.push({
            id: toolId,
            type: "tool",
            label: buildToolLabel(tc),
            parentId: taskId,
            timestamp: entry.timestamp || null,
            args: tryParseArgs(tc),
            data: { ...tc, entry },
          });
          edges.push({ id: `edge-${taskId}-${toolId}`, source: taskId, target: toolId });
        });
      });
    });

    return { nodes, edges, errors };
  }

  async function loadData() {
    showError("");
    let rootContext = { entries: [] };
    let subAgents = { agents: [] };
    const fetchErrors = [];

    try {
      rootContext = await fetchJson("/server/api/root/context");
    } catch (e) {
      fetchErrors.push(`Root context: ${e.message}`);
    }

    try {
      subAgents = await fetchJson("/server/api/sub-agents?include_history=true");
    } catch (e) {
      fetchErrors.push(`Sub-agents: ${e.message}`);
    }

    const agentIds = (subAgents.agents || [])
      .map((a) => a.id)
      .filter(Boolean);

    const subAgentContexts = await withConcurrencyLimit(agentIds, 5, async (id) => {
      try {
        const context = await fetchJson(`/server/api/sub-agents/${encodeURIComponent(id)}/context`);
        return { id, context, error: null };
      } catch (e) {
        return { id, context: null, error: e.message };
      }
    });

    const { nodes, edges, errors } = buildNodesFromData(
      rootContext,
      subAgents,
      subAgentContexts
    );

    const allErrors = [...fetchErrors, ...errors];
    if (allErrors.length) {
      showError("Some activity data could not be loaded: " + allErrors.join("; "));
    }

    graphData = { nodes, edges };
  }

  function render() {
    nodesGroup.innerHTML = "";
    edgesGroup.innerHTML = "";

    const hasData = graphData.nodes.some((n) => n.id !== "root");
    emptyState.hidden = hasData;

    if (!hasData) return;

    // Layout: left-to-right tree with fixed spacing.
    const visibleNodes = graphData.nodes.filter((n) => {
      if (n.type !== "tool") return true;
      if (n.parentId === "root") return true;
      return !collapsedAgents.has(n.parentId);
    });

    const nodeMap = new Map(visibleNodes.map((n) => [n.id, n]));
    const levelOf = new Map();
    const childrenOf = new Map();

    visibleNodes.forEach((n) => childrenOf.set(n.id, []));
    graphData.edges.forEach((edge) => {
      if (nodeMap.has(edge.source) && nodeMap.has(edge.target)) {
        childrenOf.get(edge.source).push(edge.target);
      }
    });

    function assignLevel(id, level) {
      if (levelOf.has(id)) return;
      levelOf.set(id, level);
      childrenOf.get(id).forEach((childId) => assignLevel(childId, level + 1));
    }
    assignLevel("root", 0);

    const colWidth = 260;
    const rowHeight = 58;
    const levelGroups = new Map();
    visibleNodes.forEach((n) => {
      const level = levelOf.get(n.id) || 0;
      if (!levelGroups.has(level)) levelGroups.set(level, []);
      levelGroups.get(level).push(n);
    });

    const positions = new Map();
    levelGroups.forEach((nodesAtLevel, level) => {
      const startY = -(nodesAtLevel.length - 1) * rowHeight / 2;
      nodesAtLevel.forEach((node, idx) => {
        positions.set(node.id, {
          x: level * colWidth + 80,
          y: startY + idx * rowHeight + height / 2,
        });
      });
    });

    graphData.edges.forEach((edge) => {
      if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return;
      const src = positions.get(edge.source);
      const dst = positions.get(edge.target);
      if (!src || !dst) return;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", src.x);
      line.setAttribute("y1", src.y);
      line.setAttribute("x2", dst.x);
      line.setAttribute("y2", dst.y);
      line.classList.add("ag-edge");
      edgesGroup.appendChild(line);
    });

    visibleNodes.forEach((node) => {
      const pos = positions.get(node.id);
      if (!pos) return;
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.classList.add("ag-node", `ag-node-${node.type}`);
      g.setAttribute("data-id", node.id);
      g.setAttribute("data-type", node.type);

      if (node.type === "root") {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", pos.x);
        circle.setAttribute("cy", pos.y);
        circle.setAttribute("r", 16);
        g.appendChild(circle);

        const text = createSvgText(pos.x + 22, pos.y + 4, node.label, "ag-node-title");
        g.appendChild(text);
      } else if (node.type === "task") {
        const rectWidth = 200;
        const rectHeight = 46;
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", pos.x - 10);
        rect.setAttribute("y", pos.y - rectHeight / 2);
        rect.setAttribute("width", rectWidth);
        rect.setAttribute("height", rectHeight);
        rect.setAttribute("rx", 8);
        g.appendChild(rect);

        const title = createSvgText(pos.x + rectWidth / 2 - 10, pos.y - 8, truncate(node.label, 22), "ag-node-title");
        g.appendChild(title);

        const sub = createSvgText(pos.x + rectWidth / 2 - 10, pos.y + 10, truncate(node.description, 32), "ag-node-subtitle");
        g.appendChild(sub);

        const statusX = pos.x + rectWidth - 20;
        const statusY = pos.y - rectHeight / 2 + 12;
        const badge = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        badge.setAttribute("cx", statusX);
        badge.setAttribute("cy", statusY);
        badge.setAttribute("r", 5);
        badge.classList.add("ag-status-badge", node.status === "running" ? "ag-status-running" : "ag-status-stopped");
        g.appendChild(badge);
      } else {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", pos.x);
        circle.setAttribute("cy", pos.y);
        circle.setAttribute("r", 7);
        g.appendChild(circle);

        const text = createSvgText(pos.x + 16, pos.y + 4, truncate(node.label, 42), "ag-node-tool-label");
        g.appendChild(text);
      }

      nodesGroup.appendChild(g);
    });
  }

  function createSvgText(x, y, content, className) {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x);
    text.setAttribute("y", y);
    if (className) text.classList.add(className);
    text.textContent = content;
    return text;
  }

  function applyTransform() {
    rootGroup.setAttribute(
      "transform",
      `translate(${transform.x}, ${transform.y}) scale(${transform.scale})`
    );
  }

  function setupInteractions() {
    on(svg, "mousedown", (e) => {
      if (e.button !== 0 || e.target.closest(".ag-node")) return;
      isDragging = true;
      dragStart = { x: e.clientX - transform.x, y: e.clientY - transform.y };
      svg.classList.add("dragging");
    });

    on(window, "mousemove", (e) => {
      if (!isDragging) return;
      transform.x = e.clientX - dragStart.x;
      transform.y = e.clientY - dragStart.y;
      applyTransform();
    });

    on(window, "mouseup", () => {
      isDragging = false;
      svg.classList.remove("dragging");
    });

    on(svg, "click", (e) => {
      const nodeEl = e.target.closest(".ag-node[data-type='task']");
      if (!nodeEl) return;
      const id = nodeEl.getAttribute("data-id");
      if (collapsedAgents.has(id)) {
        collapsedAgents.delete(id);
      } else {
        collapsedAgents.add(id);
      }
      render();
    });

    on(svg, "mouseover", (e) => {
      const nodeEl = e.target.closest(".ag-node");
      if (!nodeEl) {
        tooltip.hidden = true;
        return;
      }
      const id = nodeEl.getAttribute("data-id");
      const node = graphData.nodes.find((n) => n.id === id);
      if (!node) return;
      const lines = [`<strong>${node.label}</strong>`];
      if (node.timestamp) lines.push(`Time: ${node.timestamp}`);
      if (node.status) lines.push(`Status: ${node.status}`);
      if (node.turnsUsed != null) lines.push(`Turns: ${node.turnsUsed}${node.turnsLimit ? ` / ${node.turnsLimit}` : ""}`);
      if (node.description) lines.push(truncate(node.description, 160));
      if (node.args) lines.push(`Args:\n${formatArgs(node.args)}`);
      tooltip.innerHTML = lines.join("\u003cbr\u003e").replace(/\n/g, "\u003cbr\u003e");
      tooltip.hidden = false;
    });

    on(svg, "mousemove", (e) => {
      if (tooltip.hidden) return;
      const rect = container.getBoundingClientRect();
      tooltip.style.left = `${e.clientX - rect.left + 12}px`;
      tooltip.style.top = `${e.clientY - rect.top + 12}px`;
    });

    on(svg, "mouseout", (e) => {
      if (!e.target.closest(".ag-node")) {
        tooltip.hidden = true;
      }
    });

    on(svg, "wheel", (e) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const oldScale = transform.scale;
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      const newScale = Math.max(0.2, Math.min(5, oldScale * delta));
      transform.x = mx - (mx - transform.x) * (newScale / oldScale);
      transform.y = my - (my - transform.y) * (newScale / oldScale);
      transform.scale = newScale;
      applyTransform();
    }, { passive: false });
  }

  let pollTimer = null;
  const pollInterval = options.pollInterval || 5000;

  function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(() => {
      if (document.hidden) return;
      refresh().catch((err) => {
        // eslint-disable-next-line no-console
        console.error("Activity Graph auto-refresh failed:", err);
      });
    }, pollInterval);
  }

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  pollCheckbox.addEventListener("change", () => {
    if (pollCheckbox.checked) {
      startPolling();
    } else {
      stopPolling();
    }
  });

  function destroy() {
    stopPolling();
    listeners.forEach((remove) => remove());
    if (svg.parentNode) svg.parentNode.removeChild(svg);
    if (emptyState.parentNode) emptyState.parentNode.removeChild(emptyState);
    if (tooltip.parentNode) tooltip.parentNode.removeChild(tooltip);
    if (controls.parentNode) controls.parentNode.removeChild(controls);
    showError("");
  }

  async function refresh() {
    await loadData();
    render();
  }

  loadData()
    .then(render)
    .then(setupInteractions)
    .then(() => {
      if (pollCheckbox.checked) startPolling();
    })
    .catch((err) => {
      showError(`Failed to load activity graph: ${err.message || err}`);
      // eslint-disable-next-line no-console
      console.error("Activity Graph failed to load:", err);
    });

  return {
    element: svg,
    refresh,
    destroy,
  };
}
