# Background Agent Web Chat Architecture

**Date**: 2026-03-29
**Status**: Planning
**Author**: Architecture exploration session

---

## Executive Summary

This document outlines the architecture for implementing a web-based chat client for wichy that runs the agent in a background thread, enabling real-time tool visibility, web-based human verification, and browser-initiated abort functionality. The architecture is explicitly designed to be transport-agnostic, supporting future extensions like Telegram or Discord bots.

---

## Problem Statement

The current REPL interface works well for terminal usage but has limitations for web-based interaction:

| Current Mechanism                           | Web Chat Problem                    |
| ------------------------------------------- | ----------------------------------- |
| `prompt_session.prompt("Proceed? (y/n): ")` | Blocks on stdin → web request hangs |
| Ctrl+C caught only in REPL loop             | No way to abort from browser        |
| `user_console.print()` for tool output      | Web client can't see progress       |
| Synchronous `root_agent.process()`          | Cannot show incremental updates     |

---

## Architecture Overview

### Layered Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    Transport Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Flask/SSE    │  │ Telegram Bot │  │ Discord Bot          │  │
│  │ (Web UI)     │  │ (future)     │  │ (future)             │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Session Layer                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ChatSession                                              │   │
│  │  - event_queue (events to ANY consumer)                 │   │
│  │  - verification_provider (pluggable interface)           │   │
│  │  - abort_event (works from ANY caller)                  │   │
│  │  - background_thread (runs RootAgent)                   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Agent Layer                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ RootAgent (background thread)                            │   │
│  │  - Emits events to queue                                 │   │
│  │  - Checks abort between tools                            │   │
│  │  - Calls verification_provider for destructive ops       │   │
│  │  - Persists to ContextHandler (JSONL)                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Abstractions

1. **ChatSession** - Transport-agnostic session manager
2. **VerificationProvider** - Interface for prompting approval (web, telegram, CLI)
3. **EventEmitter** - Emits structured events to subscribers
4. **AbortController** - Cooperative cancellation checked in agent loop

---

## Component Details

### 1. ChatSession

**File**: `src/wichy/chat/session.py`

The core session manager that runs the agent in a background thread.

```python
class ChatSession:
    def __init__(self, root_agent: RootAgent, context: ContextHandler):
        self.root_agent = root_agent
        self.context = context
        self._event_queue: queue.Queue[ChatEvent] = queue.Queue()
        self._abort_event = threading.Event()
        self._verification_pending: Optional[dict] = None
        self._verification_condition = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._subscribers: list[Callable[[ChatEvent], None]] = []

    # === Message Handling ===

    def send_message(self, message: str) -> None:
        """Queue a user message for processing."""
        self._event_queue.put(ChatEvent(
            type="user_message",
            data={"content": message},
            timestamp=iso_timestamp(),
        ))
        # Signal background thread to process

    # === Lifecycle ===

    def start(self) -> None:
        """Start background thread."""
        self._thread = threading.Thread(target=self._run_agent, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background thread."""
        self._abort_event.set()
        self._thread.join(timeout=5.0)

    def _run_agent(self) -> None:
        """Background thread entry point."""
        while not self._abort_event.is_set():
            try:
                message = self._message_queue.get(timeout=0.5)
                self.root_agent.process(message)
            except queue.Empty:
                continue
            except AbortException:
                self._emit(ChatEvent(type="aborted", data={}))
                break

    # === Event System ===

    def subscribe(self, callback: Callable[[ChatEvent], None]) -> None:
        """Add event subscriber. Supports multiple consumers."""
        self._subscribers.append(callback)

    def _emit(self, event: ChatEvent) -> None:
        """Emit event to all subscribers."""
        self._event_queue.put(event)
        for callback in self._subscribers:
            callback(event)

    def get_events(self, timeout: float = 0.1) -> list[ChatEvent]:
        """Get pending events (for polling)."""
        events = []
        while True:
            try:
                event = self._event_queue.get(timeout=timeout)
                events.append(event)
            except queue.Empty:
                break
        return events

    # === Abort ===

    def abort(self) -> None:
        """Request abort of current processing."""
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear abort flag for new message."""
        self._abort_event.clear()

    # === Verification ===

    def respond_to_verification(self, approved: bool, reason: str = None) -> None:
        """Respond to pending verification request."""
        self._verification_response = {"approved": approved, "reason": reason}
        with self._verification_condition:
            self._verification_condition.notify()
```

### 2. VerificationProvider Interface

**File**: `src/wichy/chat/verification.py`

Abstract interface allowing different UI implementations.

```python
from abc import ABC, abstractmethod

class VerificationProvider(ABC):
    """Pluggable verification - works with any UI transport."""

    @abstractmethod
    def request_verification(
        self,
        action: str,
        args: dict,
        message: str
    ) -> tuple[bool, str]:
        """
        Request human verification for an action.

        Returns:
            tuple[bool, str]: (approved, reason)
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if verification provider is ready to receive requests."""
        pass


class WebVerificationProvider(VerificationProvider):
    """SSE/web implementation - waits on threading.Condition."""

    def __init__(self, session: ChatSession):
        self.session = session

    def request_verification(
        self,
        action: str,
        args: dict,
        message: str
    ) -> tuple[bool, str]:
        verification_id = str(uuid.uuid4())

        # Put verification request on event queue
        self.session._emit(ChatEvent(
            type="verification_request",
            data={
                "id": verification_id,
                "action": action,
                "args": args,
                "message": message,
            },
            timestamp=iso_timestamp(),
        ))

        # Wait for browser response
        self.session._verification_pending = {
            "id": verification_id,
            "response": None,
        }

        with self.session._verification_condition:
            self.session._verification_condition.wait()

        response = self.session._verification_response
        return response["approved"], response.get("reason", "")

    def is_available(self) -> bool:
        return self.session._verification_pending is None


class REPLVerificationProvider(VerificationProvider):
    """Original console-based verification."""

    def __init__(self, prompt_session):
        self.prompt_session = prompt_session

    def request_verification(
        self,
        action: str,
        args: dict,
        message: str
    ) -> tuple[bool, str]:
        needs_user_attention()  # Terminal bell
        while True:
            response = self.prompt_session.prompt("Proceed? (y/n): ")
            if response.startswith("y"):
                return True, ""
            if response.startswith("n"):
                reason = response[2:].strip() if len(response) > 2 else ""
                return False, reason

    def is_available(self) -> bool:
        return True  # Console always available
```

### 3. AbortController

**File**: `src/wichy/chat/abort.py`

Cooperative abort mechanism checked in the agent loop.

```python
class AbortException(Exception):
    """Raised when user requests abort."""
    pass


class AbortController:
    """Cooperative abort checking for agent loop."""

    def __init__(self):
        self._abort_event = threading.Event()

    def request_abort(self) -> None:
        """Request abort (called from transport layer)."""
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear abort flag for new processing."""
        self._abort_event.clear()

    def check_abort(self) -> None:
        """Check if abort requested. Raises AbortException if so."""
        if self._abort_event.is_set():
            raise AbortException("User requested abort")

    def is_abort_requested(self) -> bool:
        """Check if abort requested without raising."""
        return self._abort_event.is_set()
```

### 4. Event Types

**File**: `src/wichy/chat/events.py`

Structured events emitted during agent execution.

```python
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
import json

@dataclass
class ChatEvent:
    """Structured event emitted during agent execution."""

    type: str  # See EVENT_TYPES below
    data: dict[str, Any]
    timestamp: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "ChatEvent":
        data = json.loads(json_str)
        return cls(**data)


# Event type constants
EVENT_TYPES = {
    # Lifecycle
    "session_started": "Chat session started",
    "session_stopped": "Chat session stopped",

    # Messages
    "user_message": "User sent a message",
    "assistant_response": "Assistant generated a response",

    # Tool execution
    "tool_call": "Agent called a tool",
    "tool_result": "Tool execution completed",
    "tool_error": "Tool execution failed",

    # Verification
    "verification_request": "Tool requires human verification",
    "verification_response": "User responded to verification",

    # Control flow
    "abort_requested": "User requested abort",
    "aborted": "Agent processing aborted",
    "error": "Error occurred",
}


def iso_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"
```

---

## Integration Points

### 1. Human Verification Integration

**File**: `src/wichy/tools/human_verification.py`

Add pluggable verification provider support.

```python
# Add global verification provider override
_verification_provider: Optional[VerificationProvider] = None

def set_verification_provider(provider: Optional[VerificationProvider]) -> None:
    """Set verification provider for non-CLI contexts (web, telegram, etc.)."""
    global _verification_provider
    _verification_provider = provider


def get_verification_provider() -> Optional[VerificationProvider]:
    """Get current verification provider."""
    return _verification_provider


# Modify require_human_verification decorator
def require_human_verification(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # ... existing predicate check ...

        # NEW: Check for pluggable verification provider
        if _verification_provider and _verification_provider.is_available():
            approved, reason = _verification_provider.request_verification(
                action=label,
                args=all_args,
                message=action_message,
            )
            if approved:
                return func(*args, **kwargs)
            raise PermissionError(f"User denied: {reason}")

        # Existing PIPELINE_MODE check
        if in_pipeline_mode():
            raise PermissionError(...)

        # Existing console prompt
        needs_user_attention()
        while True:
            response = prompt_session.prompt("Proceed? (y/n): ")
            # ... existing logic ...
```

### 2. Agent Abort Support

**File**: `src/wichy/agent/core.py`

Add abort checking in tool loop.

```python
class AgentCore(ABC):
    def __init__(self):
        self.abort_controller: Optional[AbortController] = None

    def set_abort_controller(self, controller: AbortController) -> None:
        """Set abort controller for cooperative cancellation."""
        self.abort_controller = controller

    def _handle_tools_base(self, tools, response) -> bool:
        # ... existing code ...

        for tool_call in response.tool_calls:
            # Check for abort between tool executions
            if self.abort_controller:
                self.abort_controller.check_abort()

            # Execute tool
            result = tool.validate_and_execute(**args)

            # ... existing code ...
```

### 3. Tool Event Emission

**File**: `src/wichy/tools/base.py`

Hook into tool execution for visibility.

```python
class BaseTool(ABC):
    def __init__(self):
        self.event_emitter: Optional[EventEmitter] = None

    def set_event_emitter(self, emitter: EventEmitter) -> None:
        """Set event emitter for tool visibility."""
        self.event_emitter = emitter

    def validate_and_execute(self, **kwargs) -> str:
        # Emit tool_call event
        if self.event_emitter:
            self.event_emitter.emit(ChatEvent(
                type="tool_call",
                data={"tool": self.name, "args": kwargs},
                timestamp=iso_timestamp(),
            ))

        # Execute tool
        result = self.execute(**validated_params)

        # Emit tool_result event
        if self.event_emitter:
            self.event_emitter.emit(ChatEvent(
                type="tool_result",
                data={"tool": self.name, "result": result[:500]},
                timestamp=iso_timestamp(),
            ))

        return result
```

### 4. Server Integration

**File**: `src/wichy/server.py`

Register chat blueprint and pass session reference.

```python
# Add to register_blueprints()
from wichy.tools.chat import register as register_chat

def register_blueprints(app: Flask, chat_session: ChatSession = None) -> None:
    # ... existing registrations ...

    if chat_session:
        register_chat(app, chat_session)
```

### 5. CLI Command

**File**: `src/wichy/cli/chat_command.py`

New `wichy chat` command entry point.

```python
def run_chat(args) -> None:
    """Entry point for `wichy chat` command."""
    from wichy.chat import ChatSession, WebVerificationProvider
    from wichy.agent_builder import AgentBuilder
    from wichy.server import start_server
    from wichy.tools.human_verification import set_verification_provider

    # Build agent same as REPL
    builder = AgentBuilder(args)
    root_agent = builder.build()

    # Create session
    session = ChatSession(root_agent, root_agent.context)

    # Set up verification provider
    verification_provider = WebVerificationProvider(session)
    set_verification_provider(verification_provider)

    # Start background agent thread
    session.start()

    # Start web server with chat routes
    start_server(root_agent, chat_session=session)

    # Open browser or print URL
    import webbrowser
    url = f"http://localhost:{port}/tools/chat/"
    print(f"\nChat UI: {url}")
    webbrowser.open(url)

    # Wait for shutdown
    try:
        session.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        session.stop()
```

---

## Web Implementation (SSE Transport)

### API Endpoints

**File**: `src/wichy/tools/chat/api.py`

```python
from flask import Blueprint, request, Response, jsonify, stream_with_context
import json
import time

bp = Blueprint("chat", __name__, url_prefix="/tools/chat")
_session: ChatSession = None

def init_session(session: ChatSession) -> None:
    """Initialize session reference (called at startup)."""
    global _session
    _session = session


@bp.route("/")
def index():
    """Chat UI."""
    return render_template("chat.html")


@bp.route("/api/send", methods=["POST"])
def send_message():
    """Send a message to the agent."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    data = request.get_json()
    message = data.get("message", "")

    if not message:
        return jsonify({"error": "Message required"}), 400

    _session.clear_abort()  # Reset abort for new message
    _session.send_message(message)

    return jsonify({"status": "queued"})


@bp.route("/api/events/stream")
def event_stream():
    """SSE endpoint for real-time events."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    def generate():
        while True:
            events = _session.get_events(timeout=0.5)
            for event in events:
                yield f"data: {event.to_json()}\n\n"

            # Small sleep to prevent busy loop
            time.sleep(0.1)

            # Check if session is still active
            if not _session.is_running():
                yield f"data: {json.dumps({'type': 'session_stopped'})}\n\n"
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )


@bp.route("/api/verification", methods=["POST"])
def respond_verification():
    """Respond to pending verification request."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    data = request.get_json()
    approved = data.get("approved", False)
    reason = data.get("reason", "")

    _session.respond_to_verification(approved, reason)

    return jsonify({"status": "ok"})


@bp.route("/api/abort", methods=["POST"])
def abort():
    """Abort current processing."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    _session.abort()
    _session._emit(ChatEvent(type="abort_requested", data={}))

    return jsonify({"status": "aborting"})


@bp.route("/api/history")
def get_history():
    """Get conversation history."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    messages = _session.context()
    return jsonify({"messages": messages})


@bp.route("/api/status")
def get_status():
    """Get session status."""
    if not _session:
        return jsonify({"error": "Session not initialized"}), 500

    return jsonify({
        "running": _session.is_running(),
        "pending_verification": _session._verification_pending is not None,
    })
```

### Frontend Template

**File**: `src/wichy/tools/chat/templates/chat.html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Wichy Chat</title>
    <link rel="stylesheet" href="/shared/shared.css" />
    <link
      rel="stylesheet"
      href="{{ url_for('chat.static', filename='chat.css') }}"
    />
  </head>
  <body>
    <div class="container">
      <header class="header-simple">
        <div>
          <h1 class="header-title">Wichy Chat</h1>
          <p class="header-subtitle">AI Agent Interface</p>
        </div>
        <nav class="header-nav">
          <button id="abort-btn" class="btn-danger" style="display: none;">
            Abort
          </button>
          <a href="/">Back to Home</a>
        </nav>
      </header>

      <main class="chat-container">
        <div id="messages" class="messages"></div>

        <div
          id="verification-modal"
          class="modal-overlay"
          style="display: none;"
        >
          <div class="modal-content">
            <h3>⚠️ Verification Required</h3>
            <p id="verification-action"></p>
            <pre id="verification-args"></pre>
            <div class="modal-buttons">
              <button id="approve-btn" class="btn-primary">Approve</button>
              <button id="deny-btn" class="btn-danger">Deny</button>
            </div>
          </div>
        </div>

        <div class="input-area">
          <textarea
            id="message-input"
            placeholder="Type a message..."
            rows="3"
          ></textarea>
          <button id="send-btn" class="btn-primary">Send</button>
        </div>
      </main>
    </div>

    <script src="{{ url_for('chat.static', filename='chat.js') }}"></script>
  </body>
</html>
```

### Frontend JavaScript

**File**: `src/wichy/tools/chat/static/chat.js`

```javascript
class WichyChat {
  constructor() {
    this.eventSource = null;
    this.messagesContainer = document.getElementById("messages");
    this.inputField = document.getElementById("message-input");
    this.sendBtn = document.getElementById("send-btn");
    this.abortBtn = document.getElementById("abort-btn");
    this.verificationModal = document.getElementById("verification-modal");

    this.connect();
    this.setupEventListeners();
  }

  connect() {
    this.eventSource = new EventSource("/tools/chat/api/events/stream");

    this.eventSource.onmessage = (e) => {
      const event = JSON.parse(e.data);
      this.handleEvent(event);
    };

    this.eventSource.onerror = (e) => {
      console.error("SSE error:", e);
      // Reconnect after 3 seconds
      setTimeout(() => this.connect(), 3000);
    };
  }

  handleEvent(event) {
    switch (event.type) {
      case "user_message":
        this.addMessage("user", event.data.content);
        break;

      case "assistant_response":
        this.addMessage("assistant", event.data.content);
        this.hideAbort();
        break;

      case "tool_call":
        this.addToolCall(event.data.tool, event.data.args);
        this.showAbort();
        break;

      case "tool_result":
        this.addToolResult(event.data.tool, event.data.result);
        break;

      case "verification_request":
        this.showVerification(event.data);
        break;

      case "aborted":
        this.addMessage("system", "⚠️ Processing aborted by user");
        this.hideAbort();
        break;

      case "error":
        this.addMessage("error", event.data.message);
        this.hideAbort();
        break;
    }
  }

  addMessage(role, content) {
    const div = document.createElement("div");
    div.className = `message ${role}`;
    div.innerHTML = `<div class="message-content">${this.escapeHtml(content)}</div>`;
    this.messagesContainer.appendChild(div);
    this.scrollToBottom();
  }

  addToolCall(tool, args) {
    const div = document.createElement("div");
    div.className = "tool-call";
    div.innerHTML = `
            <span class="tool-name">🔧 ${tool}</span>
            <pre class="tool-args">${JSON.stringify(args, null, 2)}</pre>
        `;
    this.messagesContainer.appendChild(div);
    this.scrollToBottom();
  }

  addToolResult(tool, result) {
    const div = document.createElement("div");
    div.className = "tool-result";
    div.innerHTML = `
            <span class="tool-name">✓ ${tool}</span>
            <div class="result-preview">${this.escapeHtml(result.substring(0, 200))}${result.length > 200 ? "..." : ""}</div>
        `;
    this.messagesContainer.appendChild(div);
    this.scrollToBottom();
  }

  showVerification(data) {
    document.getElementById("verification-action").textContent = data.action;
    document.getElementById("verification-args").textContent = JSON.stringify(
      data.args,
      null,
      2,
    );
    this.verificationModal.style.display = "flex";

    // Store verification ID for response
    this.verificationModal.dataset.verificationId = data.id;
  }

  hideVerification() {
    this.verificationModal.style.display = "none";
  }

  respondVerification(approved) {
    const reason = approved ? "" : prompt("Reason for denial (optional):");

    fetch("/tools/chat/api/verification", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, reason }),
    }).then(() => this.hideVerification());
  }

  sendMessage() {
    const message = this.inputField.value.trim();
    if (!message) return;

    fetch("/tools/chat/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    this.inputField.value = "";
  }

  abort() {
    fetch("/tools/chat/api/abort", { method: "POST" });
  }

  showAbort() {
    this.abortBtn.style.display = "inline-block";
  }

  hideAbort() {
    this.abortBtn.style.display = "none";
  }

  setupEventListeners() {
    this.sendBtn.addEventListener("click", () => this.sendMessage());
    this.abortBtn.addEventListener("click", () => this.abort());

    this.inputField.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    document.getElementById("approve-btn").addEventListener("click", () => {
      this.respondVerification(true);
    });

    document.getElementById("deny-btn").addEventListener("click", () => {
      this.respondVerification(false);
    });
  }

  scrollToBottom() {
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
  }

  escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => new WichyChat());
```

---

## Extensibility: Other Transports

The architecture is explicitly designed to support alternative transports. Here's how:

### Telegram Bot Implementation

````python
# src/wichy/transports/telegram.py

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from wichy.chat import ChatSession, ChatEvent, VerificationProvider
import threading
import uuid

class TelegramVerificationProvider(VerificationProvider):
    """Send verification request via Telegram inline keyboard."""

    def __init__(self, bot: telegram.Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self._pending: dict[str, threading.Condition] = {}

    def request_verification(
        self,
        action: str,
        args: dict,
        message: str
    ) -> tuple[bool, str]:
        verification_id = str(uuid.uuid4())
        condition = threading.Condition()
        self._pending[verification_id] = {"condition": condition, "result": None}

        # Send message with inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("✓ Approve", callback_data=f"verify:{verification_id}:approve"),
                InlineKeyboardButton("✗ Deny", callback_data=f"verify:{verification_id}:deny"),
            ]
        ]
        self.bot.send_message(
            chat_id=self.chat_id,
            text=f"⚠️ *Verification Required*\n\n{action}\n\n{message}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Wait for callback
        with condition:
            condition.wait(timeout=300)  # 5 minute timeout

        result = self._pending.pop(verification_id)["result"]
        if result is None:
            return False, "Timeout"
        return result["approved"], result.get("reason", "")

    def handle_callback(self, verification_id: str, approved: bool, reason: str = None):
        """Called by bot callback handler."""
        pending = self._pending.get(verification_id)
        if pending:
            pending["result"] = {"approved": approved, "reason": reason}
            with pending["condition"]:
                pending["condition"].notify()


class TelegramTransport:
    """Telegram bot that wraps ChatSession."""

    def __init__(self, session: ChatSession, bot_token: str, chat_id: int):
        self.session = session
        self.bot = telegram.Bot(token=bot_token)
        self.chat_id = chat_id
        self.verification_provider = TelegramVerificationProvider(self.bot, chat_id)

        # Subscribe to session events
        self.session.subscribe(self._on_event)

        # Set verification provider
        from wichy.tools.human_verification import set_verification_provider
        set_verification_provider(self.verification_provider)

    def _on_event(self, event: ChatEvent):
        """Handle events from ChatSession - send to Telegram."""
        if event.type == "tool_call":
            self.bot.send_message(
                chat_id=self.chat_id,
                text=f"🔧 *Tool:* {event.data['tool']}\n```{json.dumps(event.data['args'], indent=2)}```",
                parse_mode="Markdown"
            )
        elif event.type == "tool_result":
            result = event.data['result']
            if len(result) > 500:
                result = result[:500] + "..."
            self.bot.send_message(
                chat_id=self.chat_id,
                text=f"✓ *Result:*\n```\n{result}\n```",
                parse_mode="Markdown"
            )
        elif event.type == "assistant_response":
            self.bot.send_message(
                chat_id=self.chat_id,
                text=event.data["content"],
                parse_mode="Markdown"
            )

    async def handle_message(self, update: telegram.Update):
        """Handle incoming Telegram message."""
        user_message = update.message.text
        self.session.send_message(user_message)

    async def handle_callback(self, update: telegram.Update):
        """Handle inline button callback (verification response)."""
        query = update.callback_query
        data = query.data  # "verify:ID:approve" or "verify:ID:deny"

        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "verify":
            verification_id = parts[1]
            approved = parts[2] == "approve"
            self.verification_provider.handle_callback(verification_id, approved)
            await query.answer("Verification received")
````

### Discord Bot Implementation (Sketch)

```python
# src/wichy/transports/discord.py

import discord
from wichy.chat import ChatSession, ChatEvent, VerificationProvider

class DiscordVerificationProvider(VerificationProvider):
    """Discord implementation with reaction-based approval."""

    def __init__(self, client: discord.Client, channel_id: int):
        self.client = client
        self.channel_id = channel_id
        self._pending: dict[str, discord.Message] = {}
        self._conditions: dict[str, threading.Condition] = {}

    async def request_verification(self, action: str, args: dict, message: str) -> tuple[bool, str]:
        # Send message with approve/deny reactions
        channel = self.client.get_channel(self.channel_id)
        msg = await channel.send(f"⚠️ Verification Required\n\n{action}\n\n{message}")
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        # Wait for reaction
        # ... (implementation uses discord.py event listeners)
```

### Transport Comparison

| Aspect            | Web (SSE)                                  | Telegram                                         | Discord                                   |
| ----------------- | ------------------------------------------ | ------------------------------------------------ | ----------------------------------------- |
| **Transport**     | Flask SSE endpoint                         | Bot API webhook                                  | discord.py client                         |
| **Events**        | Browser subscribes to `/api/events/stream` | `session.subscribe(callback)`                    | `session.subscribe(callback)`             |
| **Verification**  | `WebVerificationProvider` + Condition wait | `TelegramVerificationProvider` + inline keyboard | `DiscordVerificationProvider` + reactions |
| **Abort**         | `POST /api/abort`                          | `/abort` command handler                         | `/abort` slash command                    |
| **Message input** | `POST /api/send`                           | Bot message handler                              | Bot message handler                       |
| **Persistence**   | ContextHandler (JSONL)                     | ContextHandler (JSONL)                           | ContextHandler (JSONL)                    |

---

## Implementation Phases

### Phase 1: Core Infrastructure (ChatSession + Events)

**Files to create:**

```
src/wichy/chat/
├── __init__.py
├── session.py         # ChatSession class
├── events.py          # ChatEvent + EventEmitter
└── abort.py           # AbortController
```

**Tasks:**

- [ ] Implement `ChatSession` with background thread management
- [ ] Implement `ChatEvent` dataclass with serialization
- [ ] Implement `AbortController` with cooperative abort
- [ ] Add event emission to `AgentCore` base class

### Phase 2: Verification Provider

**Files to create:**

```
src/wichy/chat/
└── verification.py    # VerificationProvider interface + providers
```

**Tasks:**

- [ ] Define `VerificationProvider` ABC
- [ ] Implement `WebVerificationProvider`
- [ ] Implement `REPLVerificationProvider` (wraps existing logic)
- [ ] Modify `human_verification.py` to use provider pattern

### Phase 3: Abort Support

**Files to modify:**

- `src/wichy/agent/core.py` - Add abort checking in tool loop
- `src/wichy/root_agent/root_agent.py` - Thread abort controller

**Tasks:**

- [ ] Add `abort_controller` attribute to `AgentCore`
- [ ] Add `check_abort()` call in `_handle_tools_base()`
- [ ] Handle `AbortException` gracefully in session thread

### Phase 4: Event Emission

**Files to modify:**

- `src/wichy/tools/base.py` - Add event emitter to `BaseTool`

**Tasks:**

- [ ] Add `event_emitter` attribute to `BaseTool`
- [ ] Emit `tool_call` event before execution
- [ ] Emit `tool_result` event after execution
- [ ] Emit `tool_error` event on exception

### Phase 5: Flask Blueprint

**Files to create:**

```
src/wichy/tools/chat/
├── __init__.py        # Blueprint registration
├── api.py             # Flask routes (SSE)
├── static/
│   ├── chat.css
│   └── chat.js
└── templates/
    └── chat.html
```

**Tasks:**

- [ ] Create Flask blueprint with SSE endpoint
- [ ] Implement `/api/send`, `/api/events/stream`, `/api/verification`, `/api/abort`
- [ ] Create HTML template with chat UI
- [ ] Implement JavaScript EventSource client
- [ ] Add verification modal dialog

### Phase 6: CLI Command

**Files to create/modify:**

- `src/wichy/cli/chat_command.py` - New command entry point
- `src/wichy/__main__.py` - Add `chat` subcommand

**Tasks:**

- [ ] Add `wichy chat` CLI subcommand
- [ ] Wire up `ChatSession`, `WebVerificationProvider`
- [ ] Start web server with chat blueprint
- [ ] Open browser or print URL

---

## Testing Strategy

### Unit Tests

```python
# tests/test_chat_session.py

def test_session_emits_user_message():
    session = ChatSession(mock_agent, mock_context)
    events = []
    session.subscribe(lambda e: events.append(e))
    session.send_message("Hello")
    assert len(events) == 1
    assert events[0].type == "user_message"

def test_abort_stops_processing():
    session = ChatSession(mock_agent, mock_context)
    session.start()
    session.abort()
    assert session._abort_event.is_set()

def test_verification_blocks_until_response():
    provider = WebVerificationProvider(session)
    # ... set up condition wait test
```

### Integration Tests

```python
# tests/test_web_chat.py

def test_sse_stream(client):
    response = client.get('/tools/chat/api/events/stream')
    assert response.mimetype == 'text/event-stream'

def test_send_message(client):
    response = client.post('/tools/chat/api/send', json={"message": "Hello"})
    assert response.status_code == 200

def test_verification_flow(client):
    # Send message requiring verification
    # Mock tool with @require_human_verification
    # Verify SSE emits verification_request
    # POST to /api/verification
    # Verify tool execution continues
```

---

## Open Questions

1. **Multi-Session Support**: Should we support multiple simultaneous chat sessions (e.g., multiple browser tabs, or multiple chat files)? Currently designed as single session.

2. **Session Persistence**: Chat history uses ContextHandler (JSONL) - can reload with `--load-ctx`. Should we add session metadata (model, tools, etc.)?

3. **Streaming LLM**: Current design emits events after tool execution. Could add streaming support for incremental token emission.

4. **Authentication**: No authentication in current design. For remote access, would need auth layer.

5. **Rate Limiting**: No rate limiting on `/api/send`. Could add if needed.

---

## References

- **Napkin entries**:
  - `[2026-03-28] AgentCore base class in src/wichy/agent/core.py`
  - `[2026-03-24] Sub-agent lineage: module-level global in handler.py`
  - `[2026-03-23] Pipeline mode gates human verification`

- **Existing tools for reference**:
  - `src/wichy/tools/context_editor/` - Similar polling pattern
  - `src/wichy/tools/notes/` - Web blueprint example
  - `src/wichy/server.py` - Flask setup and blueprint registration

- **Related files**:
  - `src/wichy/tools/human_verification.py` - Existing verification decorator
  - `src/wichy/agent/core.py` - Agent base class
  - `src/wichy/root_agent/root_agent.py` - RootAgent.process() method
