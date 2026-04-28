# Wichy Server Mode — Headless HTTP API

## Overview

A new `wichy-server` entry point that provides a headless HTTP API for external applications to consume. No frontend, no REPL, no web chat. External apps create agent sessions, send messages, poll for events, and handle verification requests — all via a REST API. All communication is polling-based (no SSE/WebSocket).

## Architecture

- **Separate entry point**: `wichy-server` command starts a Flask server in the main thread
- **ChatSession**: Manages a single agent running in a background thread with an event queue
- **WebVerificationProvider**: Replaces `PromptSession`-based verification with HTTP API polling
- **API routes**: Flask Blueprint under `/api/` prefix
- **Session management**: Each session = one agent + one context handler, identified by context file ID

## Files to Create

### `src/wichy/server_main.py` (new entry point)

```python
"""Entry point for wichy-server: headless HTTP API mode, no REPL."""
from wichy.cli_parser import CliParser
from wichy.agent_builder import build_agent_from_config, AgentBuilderError
# ... shared initialization ...

def main():
    parser = CliParser()
    args = parser.parse()
    # Shared init: skills, tools, hooks (same as __main__.py)
    # Build root agent
    # Set verification provider to WebVerificationProvider
    # Create ChatSession, start background agent thread
    # Start Flask server on main thread (blocking)
```

Also update `pyproject.toml`:
```toml
[project.scripts]
wichy = "wichy.__main__:main"
wichy-server = "wichy.server_main:main"
```

### `src/wichy/chat_session.py` (new)

```python
class ChatSession:
    """Manages a single agent running in a background thread with an event queue."""
    def __init__(self, agent, context):
        self._agent = agent
        self._context = context
        self._event_queue = queue.Queue()
        self._event_log = []  # Persistent event history for since=N queries
        self._status = "idle"  # idle | processing | awaiting_verification
        self._thread = None
        self._verification_condition = threading.Condition()

    def start(self): ...      # Start background thread
    def send_message(self, content): ...  # Queue user message, wake agent
    def get_events(self, since=0): ...    # Return events since index N
    def get_status(self): ...             # Return current status
    def respond_to_verification(self, approved, reason=""): ...  # Unblock agent
    def abort(self): ...                  # Set abort event
```

### `src/wichy/web_verification.py` (new)

```python
class WebVerificationProvider:
    """Human verification via HTTP API instead of stdin prompt_session."""
    def __init__(self, chat_session):
        self._session = chat_session

    def request_verification(self, command, description):
        # Emit verification_request event to queue
        # Block on threading.Condition.wait(timeout=300)
        # Return (approved, reason) when unblocked or timeout
```

### `src/wichy/api/` (new directory)

```
src/wichy/api/
├── __init__.py       # Flask Blueprint definition, register(app)
├── routes.py         # All API endpoints
├── sessions.py       # Session manager (dict of ChatSession by ID)
```

## API Endpoints

| Endpoint | Method | Handler |
|----------|--------|---------|
| `POST /api/sessions` | POST | Create new agent session (model, tools, name, resume_session_id) |
| `GET /api/sessions` | GET | List active sessions with status |
| `GET /api/sessions/{id}` | GET | Session status + metadata |
| `DELETE /api/sessions/{id}` | DELETE | Destroy a session and its agent thread |
| `POST /api/sessions/{id}/message` | POST | `{content: str}` → send user message |
| `GET /api/sessions/{id}/events?since=N` | GET | Get events since index N |
| `GET /api/sessions/{id}/history` | GET | Full conversation history from context JSONL |
| `POST /api/sessions/{id}/verification` | POST | `{approved: bool, reason: str}` → respond to pending verification |
| `POST /api/sessions/{id}/abort` | POST | Abort current processing |

## Event Types

Events returned by `GET /api/sessions/{id}/events?since=N`:

```json
{"type": "assistant_response", "content": "...", "index": 0}
{"type": "tool_call", "tool": "bash", "args": {...}, "index": 1}
{"type": "tool_result", "tool": "bash", "result": "...", "index": 2}
{"type": "verification_request", "tool": "bash", "command": "rm -rf /", "index": 3}
{"type": "error", "message": "...", "index": 4}
{"type": "status_change", "status": "idle", "index": 5}
```

Each event has a monotonically increasing `index` field. The `since=N` parameter returns all events with `index > N`, enabling efficient incremental polling.

## Files to Modify

### `src/wichy/tools/human_verification.py`

Add `set_verification_provider()` so server mode can swap out the `PromptSession`-based verification for `WebVerificationProvider`. Current code hardcodes `prompt_session.prompt()` — need to make this pluggable via a module-level provider.

### `pyproject.toml`

Add entry point:
```toml
[project.scripts]
wichy = "wichy.__main__:main"
wichy-server = "wichy.server_main:main"
```

## Key Design Notes

- **No frontend** — this is an HTTP API for external applications to consume.
- **All polling** — `GET /events?since=N` returns incremental events, no SSE/WebSocket.
- **REPL mode and Server mode are mutually exclusive** entry points.
- **Session management**: each session = one agent + one context handler, identified by context file ID.
- **Verification**: agent blocks on `threading.Condition.wait(timeout=300)`, external app polls events and POSTs response.
- The existing background web server for tools (context editor, etc.) is separate from this API server.
- This is NOT related to the `notes/background-agent-web-chat-architecture.md` document (which proposed SSE-based web chat) — that approach was explicitly rejected.

## Size

Very Large — new entry point + ChatSession + API directory + verification provider + human_verification modification