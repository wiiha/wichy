"""Tests for the sub-agent server API endpoints."""

import json
from pathlib import Path

import pytest
from flask import Blueprint, Flask

from wichy.wichy_server.api import register_routes


class MockContext:
    """Minimal stand-in for a TaskAgent context."""

    def __init__(self, path: Path):
        self._path = path
        self._entries = []

    @property
    def path(self) -> Path:
        return self._path

    def get_entries(self) -> list[dict]:
        return list(self._entries)


class MockTaskAgent:
    """Minimal stand-in for wichy.tools.task.base.TaskAgent."""

    def __init__(self, agent_id: str, name: str = "test-agent"):
        self._id = agent_id
        self._name = name
        self.description = "A test agent"
        self.model_str = "ollama/kimi-k2.6:cloud"
        self._turns_used = 2
        self._max_turns = 5
        self._stopping = False
        self._steered: list[tuple[str, str]] = []
        self.context = MockContext(Path(f"/tmp/{agent_id}.json"))

    def status(self) -> dict:
        return {
            "id": self._id,
            "name": self._name,
            "description": self.description,
            "model": self.model_str,
            "turns_used": self._turns_used,
            "turns_limit": self._max_turns,
            "status": "stopping" if self._stopping else "running",
        }

    def steer(self, role: str, content: str) -> None:
        self._steered.append((role, content))

    def request_stop(self) -> None:
        self._stopping = True


@pytest.fixture
def client(monkeypatch):
    """Provide a Flask test client with a fresh app and empty registry."""
    from wichy.tools.task import base as task_base

    monkeypatch.setattr(task_base, "_TASK_AGENT_REGISTRY", {})

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)

    with app.test_client() as client:
        yield client


def _register_agent(agent: MockTaskAgent) -> None:
    from wichy.tools.task import base as task_base

    task_base._TASK_AGENT_REGISTRY[agent._id] = agent


class TestListSubAgents:
    def test_list_sub_agents_empty(self, client):
        response = client.get("/server/api/sub-agents")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == {"agents": []}

    def test_list_sub_agents_returns_status(self, client):
        agent = MockTaskAgent("researcher-abcd1234")
        _register_agent(agent)

        response = client.get("/server/api/sub-agents")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "agents" in data
        assert len(data["agents"]) == 1
        assert data["agents"][0]["id"] == "researcher-abcd1234"
        assert data["agents"][0]["name"] == "test-agent"


class TestSubAgentStatus:
    def test_sub_agent_status_not_found(self, client):
        response = client.get("/server/api/sub-agents/missing-id")
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "agent not found"

    def test_sub_agent_status_returns_snapshot(self, client):
        agent = MockTaskAgent("coder-1234abcd")
        _register_agent(agent)

        response = client.get("/server/api/sub-agents/coder-1234abcd")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["id"] == "coder-1234abcd"
        assert data["name"] == "test-agent"
        assert data["status"] == "running"
        assert data["turns_used"] == 2
        assert data["turns_limit"] == 5


class TestSteerSubAgent:
    def test_steer_sub_agent_not_found(self, client):
        response = client.post(
            "/server/api/sub-agents/missing-id/steer",
            json={"role": "user", "content": "hello"},
        )
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "agent not found"

    def test_steer_sub_agent_defaults_to_user(self, client):
        agent = MockTaskAgent("writer-1111")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-1111/steer",
            json={"content": "focus on APIs"},
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert agent._steered == [("user", "focus on APIs")]

    def test_steer_sub_agent_defaults_to_user_role(self, client):
        agent = MockTaskAgent("writer-2222")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-2222/steer",
            json={"content": "be concise"},
        )
        assert response.status_code == 200
        assert agent._steered == [("user", "be concise")]

    def test_steer_sub_agent_rejects_non_user_role(self, client):
        agent = MockTaskAgent("writer-3333")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-3333/steer",
            json={"role": "system", "content": "be concise"},
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "only the 'user' role is allowed for steering"
        assert agent._steered == []

    def test_steer_sub_agent_rejects_empty_content(self, client):
        agent = MockTaskAgent("writer-4444")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-4444/steer",
            json={"content": ""},
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "content cannot be empty"
        assert agent._steered == []

    def test_steer_sub_agent_rejects_missing_content(self, client):
        agent = MockTaskAgent("writer-5555")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-5555/steer",
            json={"role": "user"},
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "content cannot be empty"
        assert agent._steered == []

    def test_steer_sub_agent_rejects_invalid_role(self, client):
        agent = MockTaskAgent("writer-6666")
        _register_agent(agent)

        response = client.post(
            "/server/api/sub-agents/writer-6666/steer",
            json={"role": "admin", "content": "do something"},
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "only the 'user' role is allowed for steering"
        assert agent._steered == []


class TestStopSubAgent:
    def test_stop_sub_agent_not_found(self, client):
        response = client.post("/server/api/sub-agents/missing-id/stop")
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "agent not found"

    def test_stop_sub_agent_sets_stopping(self, client):
        agent = MockTaskAgent("summarizer-3333")
        _register_agent(agent)

        response = client.post("/server/api/sub-agents/summarizer-3333/stop")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert agent._stopping is True


class TestSubAgentContext:
    def test_sub_agent_context_not_found(self, client):
        response = client.get("/server/api/sub-agents/missing-id/context")
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "agent not found"

    def test_sub_agent_context_returns_entries(self, client):
        agent = MockTaskAgent("explorer-4444")
        agent.context._entries = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "explore"},
        ]
        _register_agent(agent)

        response = client.get("/server/api/sub-agents/explorer-4444/context")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["filename"] == "explorer-4444.json"
        assert len(data["entries"]) == 2
        assert data["entries"][0]["role"] == "system"
