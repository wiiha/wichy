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

    def steer(self, role: str, content: str) -> None:
        self.context.steer(role=role, content=content)

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

    def test_inject_after_delete_fails(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        record_id = json.loads(exec_resp.data)["id"]

        client.delete(f"/server/api/tools/results/{record_id}")

        inject_resp = client.post("/server/api/tools/inject", json={"id": record_id})
        assert inject_resp.status_code == 404
        assert json.loads(inject_resp.data)["error"] == "result not found"
        assert len(agent.context._steered) == 0

    def test_inject_after_clear_all_fails(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        record_id = json.loads(exec_resp.data)["id"]

        client.delete("/server/api/tools/results?confirm=true")

        inject_resp = client.post("/server/api/tools/inject", json={"id": record_id})
        assert inject_resp.status_code == 404
        assert json.loads(inject_resp.data)["error"] == "result not found"
        assert len(agent.context._steered) == 0


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


class TestDeleteToolResult:
    def test_delete_no_session(self, client):
        response = client.delete("/server/api/tools/results/someid")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_delete_unknown_id(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.delete("/server/api/tools/results/missing")
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "result not found"

    def test_delete_existing_id(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        record_id = json.loads(exec_resp.data)["id"]

        del_resp = client.delete(f"/server/api/tools/results/{record_id}")
        assert del_resp.status_code == 200
        assert json.loads(del_resp.data) == {"status": "ok"}

        # Verify it's gone from the list
        list_resp = client.get("/server/api/tools/results")
        results = json.loads(list_resp.data)["results"]
        assert len(results) == 0

    def test_delete_one_of_many(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        id_a = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "a"}},
            ).data
        )["id"]
        id_b = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "b"}},
            ).data
        )["id"]

        client.delete(f"/server/api/tools/results/{id_a}")

        results = json.loads(client.get("/server/api/tools/results").data)["results"]
        assert len(results) == 1
        assert results[0]["id"] == id_b

    def test_delete_already_deleted(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        record_id = json.loads(exec_resp.data)["id"]

        first = client.delete(f"/server/api/tools/results/{record_id}")
        assert first.status_code == 200

        second = client.delete(f"/server/api/tools/results/{record_id}")
        assert second.status_code == 404
        assert json.loads(second.data)["error"] == "result not found"

    def test_delete_middle_maintains_order(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        id_a = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "a"}},
            ).data
        )["id"]
        id_b = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "b"}},
            ).data
        )["id"]
        id_c = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "c"}},
            ).data
        )["id"]

        client.delete(f"/server/api/tools/results/{id_b}")

        results = json.loads(client.get("/server/api/tools/results").data)["results"]
        assert len(results) == 2
        assert results[0]["id"] == id_c
        assert results[0]["arguments"] == {"command": "c"}
        assert results[1]["id"] == id_a
        assert results[1]["arguments"] == {"command": "a"}

    def test_delete_preserves_other_result_data(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        id_a = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "alpha"}},
            ).data
        )["id"]
        id_b = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "beta"}},
            ).data
        )["id"]

        client.delete(f"/server/api/tools/results/{id_a}")

        results = json.loads(client.get("/server/api/tools/results").data)["results"]
        assert len(results) == 1
        assert results[0]["id"] == id_b
        assert results[0]["tool"] == "fake_tool"
        assert results[0]["arguments"] == {"command": "beta"}
        assert "beta" in results[0]["result"]


class TestClearToolResults:
    def test_clear_no_session(self, client):
        response = client.delete("/server/api/tools/results?confirm=true")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active root agent"

    def test_clear_without_confirm(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.delete("/server/api/tools/results")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "confirm=true is required"

    def test_clear_empty_store(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        response = client.delete("/server/api/tools/results?confirm=true")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == {"status": "ok", "deleted": 0}

    def test_clear_all(self, client):
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

        response = client.delete("/server/api/tools/results?confirm=true")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == {"status": "ok", "deleted": 2}

        results = json.loads(client.get("/server/api/tools/results").data)["results"]
        assert len(results) == 0

    def test_clear_confirm_variants_rejected(self, client):
        agent = MockRootAgent()
        set_active_session(MockSession(root_agent=agent))

        for variant in ("True", "TRUE", "yes", "1", "false"):
            resp = client.delete(f"/server/api/tools/results?confirm={variant}")
            assert resp.status_code == 400
            assert json.loads(resp.data)["error"] == "confirm=true is required"

    def test_clear_confirm_with_extra_params(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "a"}},
        )

        response = client.delete("/server/api/tools/results?confirm=true&foo=bar")
        assert response.status_code == 200
        assert json.loads(response.data) == {"status": "ok", "deleted": 1}


class TestDeleteClearInteraction:
    def test_clear_then_delete_specific_id(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        exec_resp = client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "hello"}},
        )
        record_id = json.loads(exec_resp.data)["id"]

        clear_resp = client.delete("/server/api/tools/results?confirm=true")
        assert json.loads(clear_resp.data) == {"status": "ok", "deleted": 1}

        del_resp = client.delete(f"/server/api/tools/results/{record_id}")
        assert del_resp.status_code == 404
        assert json.loads(del_resp.data)["error"] == "result not found"

    def test_delete_then_clear_remaining(self, client):
        agent = MockRootAgent(tools=[FakeTool()])
        set_active_session(MockSession(root_agent=agent))

        id_a = json.loads(
            client.post(
                "/server/api/tools/execute",
                json={"name": "fake_tool", "arguments": {"command": "a"}},
            ).data
        )["id"]
        client.post(
            "/server/api/tools/execute",
            json={"name": "fake_tool", "arguments": {"command": "b"}},
        )

        client.delete(f"/server/api/tools/results/{id_a}")

        clear_resp = client.delete("/server/api/tools/results?confirm=true")
        assert json.loads(clear_resp.data) == {"status": "ok", "deleted": 1}

        results = json.loads(client.get("/server/api/tools/results").data)["results"]
        assert len(results) == 0
