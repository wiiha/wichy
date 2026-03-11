# Graph Editor Design Document (Wichy Integration)

**Goal:** Provide a simple browser-based node-link editor so the user can visually describe relationships. Export JSON for consumption by the wichy agentic framework.

**Approach:** Use Vis.js Network (lightweight JS library) for interactive editing. Integrate a small Flask (or FastAPI) server as a wichy tool/utility, not a separate project. Persist graphs under wichy’s memory area.

---

## 1. Architecture within Wichy

```
+------------------+      1. GET /graph       +------------------+
|                  |------------------------>|                  |
|   Browser        |                        |   Flask (or     |
| (Vis.js Network) |<---------------------- |   FastAPI)       |
|                  |   2. POST /graph/save  |   wichy/tools/   |
+------------------+                        +------------------+
        ^                                          |
        |                                          | 3. writes JSON
        |                                          v
        +----------------------------+  memory/graphs/*.json
```

- **Frontend:** Single HTML page (served as a wichy static asset) loading Vis.js from a local file (`wicjy/static/vis-network.min.js`). Renders a canvas with manipulation enabled (add/move/remove nodes and edges). Includes a “Save” button that POSTs the graph data.
- **Backend:** A small Python server started on-demand by a wichy tool (e.g., `StartGraphEditorTool`) or run as a background process. Routes:
  - `GET /graph` serves the HTML page.
  - `POST /graph/save` accepts JSON `{ nodes: [], edges: [] }`, writes a timestamped file and updates `latest.json`.
- **Storage:** All graphs saved under `memory/graphs/` (relative to workspace). Agent can read `latest.json` or list all via tools.

---

## 2. Data Schema

```json
{
  "nodes": [
    { "id": "n1", "label": "Alice", "shape": "box", "color": "#97C2FC" },
    { "id": "n2", "label": "Bob", "shape": "ellipse", "color": "#FB7E81" }
  ],
  "edges": [
    { "from": "n1", "to": "n2", "label": "knows", "arrows": "to" }
  ]
}
```

- `id`: unique string.
- `label`: displayed text.
- `shape`: any Vis.js shape (box, ellipse, circle, diamond, etc.).
- `color`: CSS color.
- `from`/`to`: node IDs for edges.
- `arrows`: e.g., `"to"`, `"from"`, `"to, from"`.

No extra metadata needed; agent can infer semantics from labels.

---

## 3. API Specification

- `GET /graph`
  - Response: `text/html` (the editor page).
- `POST /graph/save`
  - Content-Type: `application/json`
  - Body: `{ "nodes": [...], "edges": [...] }`
  - Success: `{ "status": "ok", "file": "memory/graphs/graph_20260311_184900.json" }`
  - Error: `{ "status": "error", "message": "..." }` with 400/500.

---

## 4. Wichy Tool Integration

- New tools:
  - `StartGraphEditorTool` – launches the server (if not running) and opens the browser. Stores server PID in a state file under `memory/graph_editor_pid.txt`.
  - `StopGraphEditorTool` – stops the server.
  - `ReadGraphTool` – reads `memory/graphs/latest.json` (or a specific timestamp) and returns content for the agent.
  - `ListGraphsTool` – lists available graph files in `memory/graphs/`.
- Agent usage: The agent can call these tools directly. For example, when the user says “show me my relationship graph”, the agent could `StartGraphEditorTool` then `ReadGraphTool` to display or discuss the content.
- Configuration: The server port could be fixed (e.g., 7891) or randomized per start. State tracking avoids multiple instances.

---

## 5. File Structure (within Wichy)

```
wichy/
├── tools/
│   └── graph_editor/
│       ├── __init__.py
│       ├── server.py          # Flask/FastAPI app
│       ├── static/
│       │   └── vis-network.min.js  # vendored
│       └── templates/
│           └── editor.html
└── memory/
    └── graphs/
        ├── graph_20260311_184900.json
        └── latest.json
```

- `server.py` defines routes and writes to `memory/graphs/` relative to `WORKSPACE`.
- Tools in `tools/graph_editor/__init__.py` expose the wichy `BaseTool` subclasses.
- `vis-network.min.js` is a static asset; ship it with the repo.

---

## 6. Implementation Notes

- Dependencies: Flask (or FastAPI) and waitress/gunicorn for production; but for local use, Flask’s dev server is fine.
- Keep the server single‑threaded; it’s only for the user’s browser.
- Enable `manipulation: true` in Vis.js options.
- Save button uses `fetch('/graph/save', { method: 'POST', body: JSON.stringify(data) })`.
- Error handling: return JSON errors; show simple UI alerts.
- No auth; local only.
- The server should be started as a subprocess from the tool; capture PID and allow graceful shutdown.
- On workspace delete/move, the server may need to be stopped; store PID to help.

---

## 7. References

- Vis.js Network docs: https://visjs.github.io/vis-network/docs/network/
- Flask quickstart: https://flask.palletsprojects.com/quickstart/
- Example manipulation: https://visjs.github.io/vis-network/docs/network/manipulation/

---

## 8. Next Steps (if building)

1. Add `tools/graph_editor/` package with `__init__.py`.
2. Download `vis-network.min.js` into `static/`.
3. Implement `server.py` with GET `/graph` and POST `/graph/save`.
4. Create `templates/editor.html` with manipulation UI and Save button.
5. Implement wichy tools: `StartGraphEditorTool`, `StopGraphEditorTool`, `ReadGraphTool`, `ListGraphsTool`.
6. Wire tools into `wichy/tools/__init__.py` (optional selective enable).
7. Test full flow: start server, edit graph, save, agent reads.
8. Document usage in wichy README (or this file).

---

This design keeps the feature within wichy, avoids CDN, and follows wichy’s tool patterns.