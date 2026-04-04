# Session Map Implementation Plan

## Overview

Incremental implementation of the session map feature per the spec. Each increment is small, testable, and builds on previous work.

---

## Increment 1: Data Models

**File:** `src/wichy/session_map/models.py`

**Dependencies:** None

**Tasks:**
1. Create `session_map/` directory with `__init__.py`
2. Define `NodeType` enum (QUESTION, FINDING, DECISION, FILE, DEAD_END, NOTE)
3. Define `EdgeType` enum (LED_TO, ANSWERED_BY, EXPLORED, RULED_OUT, RELATED, FOLLOWS)
4. Define `Node` dataclass with to_dict/from_dict methods
5. Define `Edge` dataclass with to_dict/from_dict methods
6. Define `SessionMap` dataclass with to_dict/from_json/get_summary methods
7. Implement `generate_node_id()` helper

**Acceptance:**
```python
from wichy.session_map.models import Node, Edge, SessionMap, NodeType, EdgeType, generate_node_id

# Can create and serialize nodes
node = Node(id=generate_node_id(), type=NodeType.QUESTION, content="Test?", created_at=datetime.now(), turn=1)
assert node.to_dict()["type"] == "question"
assert Node.from_dict(node.to_dict()).id == node.id

# Can create session map and get summary
sm = SessionMap(context_id="test")
sm.nodes.append(node)
summary = sm.get_summary()
assert "QUESTION" in summary
```

---

## Increment 2: Configuration Settings

**File:** `src/wichy/config/settings.py` (modify existing)

**Dependencies:** None (can parallel with Increment 1)

**Tasks:**
1. Add `session_map_enabled: bool = True`
2. Add `session_map_interval: int = 10`
3. Add `session_map_model: str | None = None`
4. Add `session_map_validation_retries: int = 2`
5. Add `session_map_db_path` computed property

**Acceptance:**
```python
from wichy.config import settings

assert settings.session_map_enabled == True
assert settings.session_map_interval == 10
assert settings.session_map_validation_retries == 2
# Path should be under .wichy/
assert ".wichy" in str(settings.session_map_db_path)
```

---

## Increment 3: Validation Helpers

**File:** `src/wichy/session_map/validation.py`

**Dependencies:** Increment 1 (models for type definitions)

**Tasks:**
1. Define `ValidationResult` dataclass
2. Implement `validate_node_types()` function
3. Implement `validate_edge_types()` function
4. Implement `validate_references()` function

**Acceptance:**
```python
from wichy.session_map.validation import validate_node_types, validate_edge_types, validate_references, ValidationResult

# Invalid node type
issues = validate_node_types([{"type": "invalid"}], {"question", "finding"})
assert len(issues) == 1
assert "invalid type" in issues[0]

# Valid nodes
issues = validate_node_types([{"type": "question"}], {"question", "finding"})
assert len(issues) == 0

# Invalid edge references
issues = validate_references([{"from": "missing", "to": "also_missing"}], {"existing_id"})
assert len(issues) == 2
```

---

## Increment 4: SQLite Store

**File:** `src/wichy/session_map/store.py`

**Dependencies:** Increment 1 (models)

**Tasks:**
1. Implement `SessionMapStore` singleton class
2. Create SQLite schema on init
3. Implement `get()` - retrieve session map
4. Implement `save()` - upsert session map
5. Implement `get_last_turn()` / `set_last_turn()`
6. Implement `merge_nodes()` - add new nodes/edges
7. Implement `add_manual_node()` - user-created nodes
8. Implement `delete_node()` - remove node and edges
9. Implement `clear()` - delete map for context

**Acceptance:**
```python
from wichy.session_map.store import SessionMapStore
from wichy.session_map.models import Node, NodeType
import tempfile
import os

# Use temp db for test
db_path = tempfile.mktemp(suffix=".db")
store = SessionMapStore(db_path)

# Save and retrieve
sm = SessionMap(context_id="test-ctx")
node = Node(id="n1", type=NodeType.QUESTION, content="Test?", created_at=datetime.now(), turn=1)
sm.nodes.append(node)
store.save(sm)

retrieved = store.get("test-ctx")
assert retrieved is not None
assert len(retrieved.nodes) == 1
assert retrieved.nodes[0].content == "Test?"

# Merge nodes
new_node = Node(id="n2", type=NodeType.FINDING, content="Found!", created_at=datetime.now(), turn=2)
store.merge_nodes("test-ctx", [new_node], [], 2)
retrieved = store.get("test-ctx")
assert len(retrieved.nodes) == 2
```

---

## Increment 5: LLM Extraction

**File:** `src/wichy/session_map/extractor.py`

**Dependencies:** Increment 1 (models), Increment 3 (validation)

**Tasks:**
1. Define `EXTRACTION_PROMPT` template
2. Define `VALIDATION_PROMPT` template
3. Implement `format_messages_for_extraction()` helper
4. Implement `format_extraction_for_display()` helper
5. Implement `parse_extraction_response()` - parse JSON from LLM
6. Implement `parse_validation_response()` - parse validation result
7. Implement `SessionMapExtractor` class with:
   - `_get_model_str()` method
   - `extract()` method - basic extraction
   - `extract_with_validation()` method - with retry loop
   - `_convert_to_objects()` helper

**Acceptance:**
```python
from wichy.session_map.extractor import format_messages_for_extraction, parse_extraction_response

# Format messages
formatted = format_messages_for_extraction([
    {"role": "user", "content": "What is this file?"},
    {"role": "assistant", "content": "It's a config file."},
], start_turn=0)
assert "[Turn 1]" in formatted
assert "USER:" in formatted

# Parse JSON response
json_response = '{"nodes": [{"type": "question", "content": "Test?", "turn": 1}], "edges": []}'
nodes, edges = parse_extraction_response(json_response)
assert len(nodes) == 1
assert nodes[0]["type"] == "question"
```

---

## Increment 6: API Routes

**File:** `src/wichy/session_map/api.py`

**Dependencies:** Increment 4 (store), Increment 5 (extractor)

**Tasks:**
1. Create blueprint with `/tools/session-map` prefix
2. Implement global store/context handler references
3. Implement `set_session_map_store()` and `set_context_handler()`
4. Implement `GET "/"` - render HTML template
5. Implement `GET "/api/map"` - get current map
6. Implement `GET "/api/status"` - extraction status
7. Implement `POST "/api/node"` - add manual node
8. Implement `DELETE "/api/node/<node_id>"` - delete node
9. Implement `POST "/api/extract"` - manual extraction trigger
10. Implement `POST "/api/clear"` - clear map

**Acceptance:**
- All routes return correct HTTP status codes
- Routes handle missing store/context gracefully (500 error)
- JSON responses match expected schema

---

## Increment 7: Blueprint Registration

**File:** `src/wichy/session_map/__init__.py`

**Dependencies:** Increment 6 (API)

**Tasks:**
1. Export key components: `SessionMapStore`, `bp`, `register`, `set_session_map_store`, `set_context_handler`
2. Implement `register(app: Flask)` function
3. Call `api.register_routes(bp)` before registration

**Acceptance:**
```python
from wichy.session_map import register, SessionMapStore, bp

# Can register with Flask app
from flask import Flask
app = Flask(__name__)
register(app)

# Blueprint is registered
assert "/tools/session-map" in [rule.rule for rule in app.url_map.iter_rules()]
```

---

## Increment 8: Web GUI Templates

**Files:** 
- `src/wichy/templates/session_map.html`
- `src/wichy/static/session_map.js`
- `src/wichy/static/session_map.css`

**Dependencies:** Increment 6 (API routes)

**Tasks:**

**HTML:**
1. Create page structure (header, sidebar, canvas)
2. Add status display section
3. Add filter checkboxes for node types
4. Add action buttons
5. Add node detail panel
6. Add add-note modal

**JavaScript:**
1. Initialize vis.js network
2. Implement `loadMap()` and `updateNetwork()`
3. Implement filter toggle logic
4. Implement manual extraction trigger
5. Implement note add/delete
6. Implement polling (5-second interval)

**CSS:**
1. Layout styles (sidebar + canvas)
2. Node detail panel styles
3. Modal styles
4. Button styles

**Acceptance:**
- Vis.js network renders nodes and edges
- Filters hide/show node types
- Status displays current turn/last extracted
- Add note modal works
- Node selection shows detail panel
- Delete node removes from view

---

## Increment 9: RootAgent Integration

**File:** `src/wichy/root_agent/root_agent.py` (modify existing)

**Dependencies:** Increment 4 (store), Increment 5 (extractor), Increment 2 (settings)

**Tasks:**
1. Add imports for settings, SessionMapStore, SessionMapExtractor
2. Add `_session_map_store` and `_session_map_extractor` attributes
3. Implement `_init_session_map()` - lazy init
4. Implement `_get_user_turn_count()` - count user messages
5. Implement `_get_messages_since_turn()` - get messages since extraction point
6. Implement `_maybe_extract_session_map()` - timing check + extraction call
7. Add `self._maybe_extract_session_map()` call at end of `process()` method

**Acceptance:**
- Extraction only triggers after N user turns
- Extraction gets correct message slice
- Store is initialized lazily
- No errors when feature disabled

---

## Increment 10: Server Integration

**File:** `src/wichy/server.py` (modify existing)

**Dependencies:** Increment 7 (blueprint registration)

**Tasks:**
1. Import `register as register_session_map` from session_map
2. Add `register_session_map(app)` call in blueprint registration
3. Implement `init_session_map(context_handler)` to set context handler reference
4. Call `init_session_map()` after context handler is created

**Acceptance:**
- Session map blueprint accessible at `/tools/session-map/`
- API routes return data from actual store
- Context handler reference properly set

---

## Increment 11: End-to-End Testing

**Dependencies:** All previous increments

**Tasks:**
1. Write unit tests for models
2. Write unit tests for store
3. Write unit tests for extractor (mock LLM)
4. Write integration test for API routes
5. Manual testing of Web GUI
6. Test extraction timing in real conversation

**Acceptance:**
- All tests pass with `/home/wichy/venv/bin/pytest tests/`
- Manual test: start server, navigate to `/tools/session-map/`, verify UI loads
- Manual test: trigger extraction, verify nodes appear

---

## Increment 12: Polish & Documentation

**Tasks:**
1. Run `ruff check --fix` and fix any issues
2. Run `black --target-version py310 src/ tests/`
3. Add docstrings to public methods
4. Update README with session map feature
5. Add environment variable documentation

**Acceptance:**
- No ruff errors
- Code formatted with black
- Docstrings on all public APIs

---

## Execution Strategy

Per the napkin, delegate each increment to a task agent:

| Increment | Agent Type | Notes |
|-----------|------------|-------|
| 1 | general-purpose | Foundation, needs careful implementation |
| 2 | general-purpose | Can run parallel with #1 |
| 3 | general-purpose | Depends on #1 |
| 4 | general-purpose | Depends on #1 |
| 5 | general-purpose | Depends on #1, #3 |
| 6 | general-purpose | Depends on #4, #5 |
| 7 | general-purpose | Depends on #6 |
| 8 | general-purpose | Depends on #6 |
| 9 | general-purpose | Depends on #2, #4, #5 |
| 10 | general-purpose | Depends on #7 |
| 11 | general-purpose | Depends on all |
| 12 | general-purpose | Final polish |

---

## Quick Reference: File Changes

| File | Action | Increment |
|------|--------|-----------|
| `src/wichy/session_map/__init__.py` | Create | 7 |
| `src/wichy/session_map/models.py` | Create | 1 |
| `src/wichy/session_map/store.py` | Create | 4 |
| `src/wichy/session_map/validation.py` | Create | 3 |
| `src/wichy/session_map/extractor.py` | Create | 5 |
| `src/wichy/session_map/api.py` | Create | 6 |
| `src/wichy/templates/session_map.html` | Create | 8 |
| `src/wichy/static/session_map.js` | Create | 8 |
| `src/wichy/static/session_map.css` | Create | 8 |
| `src/wichy/config/settings.py` | Modify | 2 |
| `src/wichy/root_agent/root_agent.py` | Modify | 9 |
| `src/wichy/server.py` | Modify | 10 |