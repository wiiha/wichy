"""Tests for the root agent and slash command server API endpoints."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Blueprint, Flask

from wichy.context.handler import ContextHandler
from wichy.slash_commands import SlashCommandChecker
from wichy.wichy_server.api import register_routes, set_active_session


class MockContext(ContextHandler):
    """ContextHandler that can be initialized without a real contexts dir."""

    def __init__(self, path: Path):
        # Skip the normal __init__ path generation
        self.context = []
        self.logs = []
        self._path = path
        self._lock = type(
            "MockLock",
            (object,),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
                "acquire": lambda self, *args: None,
                "release": lambda self: None,
            },
        )()
        self._file_mtime = 0


class MockRootAgent:
    def __init__(self, context=None):
        if context is None:
            context = MockContext(Path("/tmp/fake_context.json"))
        self.context = context
        self._name = "root-agent-basic"
        self._display_name = "Assistant"
        self._model_str = "ollama/kimi-k2.6:cloud"
        self.current_prompt_tokens = 1234
        self.auto_compact_threshold = 8000

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return self._display_name

    @property
    def model_str(self):
        return self._model_str


class MockSession:
    def __init__(self, root_agent=None, cmd_checker=None):
        self.root_agent = root_agent
        self.cmd_checker = cmd_checker


@pytest.fixture
def client():
    """Provide a Flask test client with a fresh app and blueprint."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)

    set_active_session(None)

    with app.test_client() as client:
        yield client

    set_active_session(None)


class TestRootContextEndpoint:
    def test_root_context_no_session(self, client):
        response = client.get("/server/api/root/context")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_root_context_returns_entries_and_filename(self, client, tmp_path):
        ctx_path = tmp_path / "2026-07-03_12345.json"
        ctx = ContextHandler.__new__(ContextHandler)
        ctx.context = []
        ctx.logs = []
        ctx._path = ctx_path
        ctx._lock = MagicMock()
        ctx._file_mtime = 0

        # Write a JSONL file directly with interleaved messages and logs
        ctx_path.write_text(
            json.dumps(
                {
                    "role": "user",
                    "content": "hello",
                    "type": "message",
                    "timestamp": "t1",
                    "_tick": 0,
                }
            )
            + "\n"
            + json.dumps({"type": "log", "event": "test", "timestamp": "t2"})
            + "\n"
            + json.dumps(
                {
                    "role": "assistant",
                    "content": "hi",
                    "type": "message",
                    "timestamp": "t3",
                    "_tick": 1,
                }
            )
            + "\n"
        )

        agent = MockRootAgent(context=ctx)
        set_active_session(MockSession(root_agent=agent))

        response = client.get("/server/api/root/context")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["filename"] == "2026-07-03_12345.json"
        assert len(data["entries"]) == 3
        assert data["entries"][0]["role"] == "user"
        assert data["entries"][1]["type"] == "log"
        assert data["entries"][2]["role"] == "assistant"


class TestRootStatusEndpoint:
    def test_root_status_no_session(self, client):
        response = client.get("/server/api/root/status")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_root_status_schema(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.get("/server/api/root/status")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["model"] == "ollama/kimi-k2.6:cloud"
        assert data["name"] == "root-agent-basic"
        assert data["display_name"] == "Assistant"
        assert data["message_count"] == 0
        assert data["current_prompt_tokens"] == 1234
        assert data["auto_compact_threshold"] == 8000


class TestSlashCommandsEndpoint:
    def test_slashcommands_no_session(self, client):
        response = client.get("/server/api/slashcommands")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active session"

    def test_slashcommands_returns_commands(self, client):
        checker = SlashCommandChecker(root_agent=None)
        set_active_session(MockSession(cmd_checker=checker))

        response = client.get("/server/api/slashcommands")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "commands" in data
        assert len(data["commands"]) > 0
        for cmd in data["commands"]:
            assert "name" in cmd
            assert "description" in cmd
            assert cmd["name"].startswith("/")
