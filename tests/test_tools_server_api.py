"""Tests for the /server/api/tools endpoints."""

import json
from pathlib import Path

import pytest
from flask import Blueprint, Flask

from wichy.tools.base import BaseTool, ParametersModel
from wichy.wichy_server.api import register_routes, set_active_session


class FakeParams(ParametersModel):
    """Minimal parameters model for a test tool."""

    command: str


class FakeTool(BaseTool):
    """A safe tool that doubles as a test tool."""

    name = "fake_tool"
    description = "A fake tool for testing."
    parameters_model = FakeParams
    needs_verification_in_api = False

    def execute(self, **kwargs) -> str:
        return f"executed with {kwargs}"


class DangerousParams(ParametersModel):
    command: str


class DangerousTool(BaseTool):
    """A tool that inherits the default verification requirement."""

    name = "dangerous_tool"
    description = "A dangerous tool for testing."
    parameters_model = DangerousParams

    def execute(self, **kwargs) -> str:
        return f"dangerous result with {kwargs}"


class MockContext:
    def __init__(self, path: Path):
        self._path = path
        self._entries = []
        self._steered = []

    @property
    def path(self) -> Path:
        return self._path

    def get_entries(self) -> list[dict]:
        return list(self._entries)

    def steer(self, role: str, content: str) -> None:
        self._steered.append((role, content))

    def add(self, role: str, content: str) -> None:
        self._entries.append({"role": role, "content": content})


class MockRootAgent:
    def __init__(self, tools=None):
        self.tools = tools or []
        self.context = MockContext(Path("/tmp/fake_context.json"))
        self._name = "root-agent-basic"
        self._display_name = "Assistant"
        self._model_str = "ollama/kimi-k2.6:cloud"
        self.current_prompt_tokens = 0
        self.auto_compact_threshold = None

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
    def __init__(self, root_agent=None):
        self.root_agent = root_agent


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Provide a Flask test client with a fresh app and isolated result store."""
    from wichy.wichy_server import tool_results_store

    monkeypatch.setattr(
        tool_results_store,
        "_store",
        tool_results_store.ToolResultsStore(tmp_path / "tool_results.db"),
    )

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)
    set_active_session(None)

    with app.test_client() as client:
        yield client

    set_active_session(None)


class TestListTools:
    def test_list_tools_no_session(self, client):
        response = client.get("/server/api/tools")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_list_tools_returns_tools(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.get("/server/api/tools")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "tools" in data
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "fake_tool"
        assert "schema" in data["tools"][0]
        assert "description" in data["tools"][0]


class TestExecuteTool:
    def test_execute_no_session(self, client):
        response = client.post("/server/api/tools/execute", json={"name": "fake_tool"})
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_execute_missing_name(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.post("/server/api/tools/execute", json={"arguments": {}})
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "tool name is required"

    def test_execute_tool_not_found(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.post(
            "/server/api/tools/execute", json={"name": "missing", "arguments": {}}
        )
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "tool not found"

    def test_execute_safe_tool_stores_result(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["tool"] == "fake_tool"
        assert "id" in data
        assert "executed with" in data["result"]

        # The result should be retrievable
        results_resp = client.get("/server/api/tools/results")
        assert results_resp.status_code == 200
        results = json.loads(results_resp.data)
        assert len(results["results"]) == 1
        assert results["results"][0]["id"] == data["id"]
        assert results["results"][0]["tool"] == "fake_tool"

    def test_execute_dangerous_tool_without_verified(self, client):
        agent = MockRootAgent(tools=[DangerousTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.post(
            "/server/api/tools/execute",
            json={"name": "dangerous_tool", "arguments": {"command": "rm -rf /"}},
        )
        assert response.status_code == 403
        data = json.loads(response.data)
        assert "verification" in data["error"]

    def test_execute_dangerous_tool_with_verified(self, client):
        agent = MockRootAgent(tools=[DangerousTool()])
        set_active_session(MockSession(root_agent=agent))

        response = client.post(
            "/server/api/tools/execute",
            json={
                "name": "dangerous_tool",
                "arguments": {"command": "rm -rf /"},
                "verified": True,
            },
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["tool"] == "dangerous_tool"
        assert "id" in data


class TestInjectToolResult:
    def test_inject_no_session(self, client):
        response = client.post("/server/api/tools/inject", json={"id": "abc"})
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_inject_requires_id(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.post("/server/api/tools/inject", json={})
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "id is required"

    def test_inject_unknown_id(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.post("/server/api/tools/inject", json={"id": "missing"})
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "result not found"

    def test_inject_uses_stored_result(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        exec_data = json.loads(exec_resp.data)
        record_id = exec_data["id"]

        inject_resp = client.post("/server/api/tools/inject", json={"id": record_id})
        assert inject_resp.status_code == 200
        data = json.loads(inject_resp.data)
        assert data["status"] == "ok"

        assert len(agent.context._steered) == 1
        role, content = agent.context._steered[0]
        assert role == "user"
        assert "[SYNTHETIC — Manual API tool execution]" in content
        assert "Tool: fake_tool" in content
        assert '"command": "hello"' in content
        assert "executed with" in content


class TestListToolResults:
    def test_list_results_empty(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.get("/server/api/tools/results")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == {"results": []}

    def test_list_results_after_execute(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "a"}},
        )
        client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "b"}},
        )

        response = client.get("/server/api/tools/results")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data["results"]) == 2
        assert data["results"][0]["tool"] == "fake_tool"
        assert data["results"][0]["arguments"] == {"command": "b"}
        assert data["results"][1]["arguments"] == {"command": "a"}
