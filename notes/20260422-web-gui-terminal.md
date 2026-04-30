# Web GUI Terminal

## Overview

Add an interactive terminal to the web GUI using a PTY bridge and WebSocket. The server swaps from Werkzeug's `run_simple` to `gevent.pywsgi.WSGIServer` (drop-in replacement that adds raw WebSocket support). Terminal is standalone — no agent integration. xterm.js frontend talks to a gevent WebSocket handler that bridges to a PTY process.

## Dependencies to Add

**`pyproject.toml`** — Add:
```toml
"gevent>=24.0.0",
"gevent-websocket>=0.10",
```

## Files to Modify

### 1. `src/wichy/server.py`

**Replace Werkzeug with gevent** (Lines 177-187):

Before:
```python
def run_app():
    from werkzeug.serving import run_simple
    run_simple(host, actual_port, _server_app, threaded=True, use_reloader=False, use_debugger=False)
```

After:
```python
def run_app():
    from gevent.pywsgi import WSGIServer
    server = WSGIServer((host, actual_port), _server_app)
    _server_app.logger.info(f"Gevent WSGI server started on {host}:{actual_port}")
    server.serve_forever()
```

**Add terminal blueprint registration** (Lines 121-133, in `register_blueprints()`):

```python
from wichy.tools.terminal import register as register_terminal  # ~line 128
register_terminal(app)  # ~line 134
```

**Update `stop_background_server()`** (Lines 196-205):

Need to keep a reference to the gevent WSGIServer object so we can call `server.stop()` on shutdown, instead of just nulling globals.

### 2. `src/wichy/templates/landing.html`

Add a terminal tool card to the tools grid on the landing page.

## Files to Create

### `src/wichy/tools/terminal/` (new directory)

```
src/wichy/tools/terminal/
├── __init__.py          # Blueprint def + register(app)
├── api.py               # WebSocket route for PTY bridge
├── pty_manager.py       # PTY session management
├── templates/
│   └── terminal.html    # xterm.js frontend page
└── static/
    └── terminal.js      # WebSocket client glue code
```

### `src/wichy/tools/terminal/__init__.py`

```python
from flask import Blueprint, render_template

bp = Blueprint("terminal", __name__, url_prefix="/terminal")

def register(app):
    from . import api
    api.register_routes(bp)

    @bp.route("/")
    def index():
        return render_template("terminal.html")

    app.register_blueprint(bp)
```

### `src/wichy/tools/terminal/pty_manager.py`

```python
import os
import pty
import select
import signal
import threading

class PTYSession:
    """Manages a single PTY process bridged to a WebSocket."""
    def __init__(self, cwd=None):
        self.master_fd, self.slave_fd = pty.openpty()
        self.pid = os.fork()
        if self.pid == 0:
            # Child process — start shell
            os.setsid()
            os.dup2(self.slave_fd, 0)
            os.dup2(self.slave_fd, 1)
            os.dup2(self.slave_fd, 2)
            os.close(self.master_fd)
            if cwd:
                os.chdir(cwd)
            os.execvp(os.environ.get("SHELL", "/bin/bash"), [])
        # Parent process
        os.close(self.slave_fd)

    def write(self, data: bytes):
        os.write(self.master_fd, data)

    def read(self) -> bytes | None:
        try:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if r:
                return os.read(self.master_fd, 4096)
        except (OSError, IOError):
            pass
        return None

    def resize(self, rows, cols):
        # fcntl.ioctl to set window size
        ...

    def kill(self):
        os.kill(self.pid, signal.SIGTERM)
        os.close(self.master_fd)
```

### `src/wichy/tools/terminal/api.py`

```python
from flask import request
from geventwebsocket.handler import WebSocketHandler

_sessions = {}  # track PTY sessions

def register_routes(bp):
    @bp.route("/ws")
    def terminal_ws():
        ws = request.environ.get("wsgi.websocket")
        if not ws:
            return "WebSocket expected", 400

        pty_session = PTYSession(cwd=os.getcwd())
        _sessions[id(pty_session)] = pty_session

        try:
            # Read from PTY → WebSocket
            def read_loop():
                while True:
                    output = pty_session.read()
                    if output:
                        ws.send(output)
                    # gevent sleep/yield

            # Read from WebSocket → PTY
            for msg in ws:
                if isinstance(msg, str):
                    pty_session.write(msg.encode())
                elif isinstance(msg, bytes):
                    pty_session.write(msg)
        finally:
            pty_session.kill()
            del _sessions[id(pty_session)]
```

### `src/wichy/tools/terminal/templates/terminal.html`

- Uses shared.css design system
- Loads xterm.js (from vendor or CDN)
- Creates xterm `Terminal` instance
- Connects to `ws://localhost:7891/terminal/ws`
- Bridges xterm.onData → ws.send, ws.onmessage → xterm.write
- Handles resize events

### Static assets

xterm.js vendor files need to be added to `src/wichy/static/`:
- `xterm.min.js`
- `xterm.css`
- `xterm-addon-fit.min.js`

## Key Design Notes

- **Server swap (werkzeug → gevent)** is the biggest risk — all existing routes must be tested after the change. gevent's WSGIServer is a drop-in replacement for Werkzeug's `run_simple` for standard HTTP routes, but adds WebSocket support via `gevent-websocket`.
- Terminal is standalone — no agent integration.
- **Security**: localhost only, no auth needed.
- **Multiple terminal sessions**: each WebSocket connection gets its own PTY session.
- gevent's cooperative threading means the read_loop can yield control between reads without blocking the server.

## Size

Large — server.py swap + new terminal blueprint + frontend + deps