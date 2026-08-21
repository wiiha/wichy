"""API endpoints for Wichy server mode"""

from __future__ import annotations

import json
import logging
from queue import Queue
from typing import TYPE_CHECKING, Optional

from flask import Blueprint, jsonify, request

from wichy.console import user_console

if TYPE_CHECKING:
    from wichy.wichy_server.chat_session import ChatSession

from wichy.helpers.interaction_provider import get_interaction_provider
from wichy.helpers.verification_provider import get_verification_provider
from wichy.tools.base import BaseTool
from wichy.tools.task.base import (
    get_task_agent,
    get_task_agent_history_entry,
    list_task_agents,
    list_task_agent_history_entries,
)
from wichy.context.handler import read_jsonl_entries
from wichy.event_log import get_agent_event_store, get_event_store
from wichy.event_log.store import EventStore
from wichy.wichy_server.interaction_provider import ServerInteractionProvider
from wichy.wichy_server.tool_results_store import get_tool_results_store
from wichy.wichy_server.verification_provider import ServerVerificationProvider

logger = logging.getLogger(__name__)

_input_queue: Optional[Queue[str]] = None


def set_input_queue(q: Queue[str]):
    """Set the currently active context handler for the web editor to manipulate."""
    global _input_queue
    _input_queue = q


def get_input_queue():
    return _input_queue


_active_session: Optional["ChatSession"] = None


def set_active_session(session: Optional["ChatSession"]):
    """Set the currently active ChatSession so API routes can reach it."""
    global _active_session
    _active_session = session


def get_active_session() -> Optional["ChatSession"]:
    return _active_session


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    @bp.route("/messages", methods=["GET"])
    def get_responses():
        msgs = user_console.get_messages()
        return jsonify(msgs)

    @bp.route("/messages", methods=["POST"])
    def post_new_message():
        q = get_input_queue()
        if not q:
            return (
                jsonify({"error": "Server input not available: no active input queue"}),
                503,
            )
        data = request.get_json(silent=True) or {}
        q.put(data.get("line", ""))
        return jsonify({"status": "ok"})

    @bp.route("/steer", methods=["POST"])
    def steer():
        session = get_active_session()
        if session is None:
            return jsonify({"error": "no active session"}), 503
        data = request.get_json(silent=True) or {}
        role = data.get("role", "user")
        content = data.get("content", "")
        session.root_agent.steer(role=role, content=content)
        return jsonify({"status": "ok"})

    @bp.route("/verifications", methods=["GET"])
    def get_pending():
        vp = get_verification_provider()
        if not isinstance(vp, ServerVerificationProvider):
            return jsonify({"error": "not configured"}), 503
        return jsonify(
            [
                {"id": p.id, "label": p.label, "message": p.message, "args": p.args}
                for p in vp.list_pending()
            ]
        )

    @bp.route("/verifications/<vid>", methods=["POST"])
    def post_single(vid):
        vp = get_verification_provider()
        if not isinstance(vp, ServerVerificationProvider):
            return jsonify({"error": "not configured"}), 503

        data = request.get_json(silent=True) or {}
        approved = bool(data.get("approved", False))
        reason = str(data.get("reason", ""))

        if not vp.respond(vid, approved, reason):
            return jsonify({"error": "not found or already resolved"}), 404

        return jsonify({"status": "ok"})

    @bp.route("/questions", methods=["GET"])
    def get_pending_questions():
        provider = get_interaction_provider()
        if not isinstance(provider, ServerInteractionProvider):
            return jsonify({"error": "not configured"}), 503

        pending = provider.list_pending()
        return jsonify(
            [
                {
                    "id": p.id,
                    "timestamp": p.timestamp,
                    "metadata": p.metadata,
                    "questions": [q.model_dump(mode="json") for q in p.questions],
                }
                for p in pending
            ]
        )

    @bp.route("/questions/<qid>", methods=["POST"])
    def post_question_answers(qid):
        provider = get_interaction_provider()
        if not isinstance(provider, ServerInteractionProvider):
            return jsonify({"error": "not configured"}), 503

        data = request.get_json(silent=True) or {}
        answers = data.get("answers")
        if not isinstance(answers, dict):
            return jsonify({"error": "answers dict required"}), 400

        if not provider.respond(qid, answers):
            return jsonify({"error": "not found or already answered"}), 404

        return jsonify({"status": "ok"})

    @bp.route("/root/context", methods=["GET"])
    def get_root_context():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        ctx = session.root_agent.context
        return jsonify(
            {
                "filename": ctx.path.name,
                "entries": ctx.get_entries(),
            }
        )

    @bp.route("/events", methods=["GET"])
    def get_events():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        session_id = session.root_agent.context.session_id
        store = get_event_store(session_id)
        since_id = int(request.args.get("since_id", 0))
        limit = min(int(request.args.get("limit", 100)), 1000)
        events, last_id, has_more = _read_store_events(store, since_id, limit)
        return jsonify(
            {
                "session_id": session_id,
                "events": events,
                "last_id": last_id,
                "has_more": has_more,
            }
        )

    @bp.route("/events/clear", methods=["POST"])
    def clear_events():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        session_id = session.root_agent.context.session_id
        store = get_event_store(session_id)
        backup = store._rotate()
        return jsonify({"status": "ok", "backup": backup.name if backup else None})

    @bp.route("/root/status", methods=["GET"])
    def get_root_status():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        ra = session.root_agent
        return jsonify(
            {
                "model": ra.model_str,
                "name": ra.name,
                "display_name": ra.display_name,
                "message_count": len(ra.context),
                "current_prompt_tokens": ra.current_prompt_tokens,
                "auto_compact_threshold": ra.auto_compact_threshold,
            }
        )

    @bp.route("/slashcommands", methods=["GET"])
    def get_slash_commands():
        session = get_active_session()
        if session is None or session.cmd_checker is None:
            return jsonify({"error": "no active session"}), 503

        commands = session.cmd_checker.list_commands()
        return jsonify({"commands": commands})

    @bp.route("/sub-agents", methods=["GET"])
    def get_sub_agents():
        agents = [agent.status() for agent in list_task_agents()]
        include_history = request.args.get("include_history", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        if include_history:
            agents.extend(
                entry.status_dict() for entry in list_task_agent_history_entries()
            )
        return jsonify({"agents": agents})

    @bp.route("/sub-agents/<agent_id>", methods=["GET"])
    def get_sub_agent_status(agent_id: str):
        agent = get_task_agent(agent_id)
        if agent is None:
            return jsonify({"error": "agent not found"}), 404
        return jsonify(agent.status())

    @bp.route("/sub-agents/<agent_id>/events", methods=["GET"])
    def get_sub_agent_events(agent_id: str):
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        agent = get_task_agent(agent_id)
        if agent is not None:
            session_id = agent.context.session_id
        else:
            # Fall back to the current root session; custom frontends may poll
            # agents that are no longer in the in-memory registries.
            session_id = session.root_agent.context.session_id

        store = get_agent_event_store(session_id, agent_id)
        since_id = int(request.args.get("since_id", 0))
        limit = min(int(request.args.get("limit", 100)), 1000)
        events, last_id, has_more = _read_store_events(store, since_id, limit)
        return jsonify(
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "events": events,
                "last_id": last_id,
                "has_more": has_more,
            }
        )

    @bp.route("/sub-agents/<agent_id>/events/clear", methods=["POST"])
    def clear_sub_agent_events(agent_id: str):
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        store = get_agent_event_store(session.root_agent.context.session_id, agent_id)
        backup = store._rotate()
        return jsonify({"status": "ok", "backup": backup.name if backup else None})

    @bp.route("/sub-agents/<agent_id>/steer", methods=["POST"])
    def steer_sub_agent(agent_id: str):
        agent = get_task_agent(agent_id)
        if agent is None:
            return jsonify({"error": "agent not found"}), 404

        data = request.get_json(silent=True) or {}
        role = data.get("role", "user")
        content = data.get("content", "")
        if role != "user":
            return (
                jsonify({"error": "only the 'user' role is allowed for steering"}),
                400,
            )
        if not isinstance(content, str) or content.strip() == "":
            return jsonify({"error": "content cannot be empty"}), 400

        agent.steer(role=role, content=content)
        return jsonify({"status": "ok"})

    @bp.route("/sub-agents/<agent_id>/stop", methods=["POST"])
    def stop_sub_agent(agent_id: str):
        agent = get_task_agent(agent_id)
        if agent is None:
            return jsonify({"error": "agent not found"}), 404

        agent.request_stop()
        return jsonify({"status": "ok"})

    @bp.route("/sub-agents/<agent_id>/context", methods=["GET"])
    def get_sub_agent_context(agent_id: str):
        agent = get_task_agent(agent_id)
        if agent is not None:
            ctx = agent.context
            return jsonify(
                {
                    "filename": ctx.path.name,
                    "entries": ctx.get_entries(),
                }
            )

        # Fallback to historical agent metadata and read context from disk.
        entry = get_task_agent_history_entry(agent_id)
        if entry is None:
            return jsonify({"error": "agent not found"}), 404

        if not entry.context_file.exists():
            return (
                jsonify(
                    {
                        "error": "agent context file not found",
                        "filename": entry.context_file.name,
                    }
                ),
                500,
            )

        return jsonify(
            {
                "filename": entry.context_file.name,
                "entries": read_jsonl_entries(entry.context_file),
            }
        )

    # -----------------------------------------------------------------------
    # Tools API
    # -----------------------------------------------------------------------

    def _get_active_root_agent():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return None
        return session.root_agent

    def _find_tool_by_name(tools: list[BaseTool], name: str) -> BaseTool | None:
        for tool in tools:
            if tool.name == name:
                return tool
        return None

    @bp.route("/tools", methods=["GET"])
    def get_tools():
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "description_long": tool.description_long,
                "schema": tool.to_function_definition(),
            }
            for tool in root_agent.tools
        ]
        return jsonify({"tools": tools})

    @bp.route("/tools/execute", methods=["POST"])
    def execute_tool():
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        data = request.get_json(silent=True) or {}
        name = data.get("name")
        arguments = data.get("arguments", {})
        verified = bool(data.get("verified", False))

        if not isinstance(name, str) or name == "":
            return jsonify({"error": "tool name is required"}), 400
        if not isinstance(arguments, dict):
            return jsonify({"error": "arguments must be an object"}), 400

        tool = _find_tool_by_name(root_agent.tools, name)
        if tool is None:
            return jsonify({"error": "tool not found"}), 404

        # TODO/FIXME: The "verified" flag is a cooperative signal only. A
        # malicious caller can simply set it to true. This is sufficient for a
        # trusted custom frontend that takes responsibility for approval, but it
        # does not protect against a hostile client. A real solution requires
        # an authentication/authorization model with per-tool permissions or
        # a two-step verification flow through ServerVerificationProvider.
        if _tool_requires_verification(tool) and not verified:
            return (
                jsonify(
                    {
                        "error": "tool requires verification; set verified=true to proceed"
                    }
                ),
                403,
            )

        try:
            if verified:
                # Bypass the human-verification decorator by temporarily
                # disabling interactive verification. We still run the full
                # validate_and_execute pipeline (validation, pre/post hooks,
                # result offloading) so that tool behavior is consistent with
                # agent-driven execution.
                #
                # FIXME/TEMPORARY SIDE EFFECT: this is a process-global flag.
                # If the manually executed tool is a task agent (or any tool
                # that calls other tools), those nested tool calls will also
                # skip verification while the flag is set. In practice this is
                # acceptable for trusted callers that have already verified the
                # top-level call, but it is not a fine-grained authorization
                # model and should be replaced with per-call or per-tool
                # permission tracking.
                from wichy.config.settings import settings

                previous_skip = settings.skip_human_verification
                try:
                    settings.skip_human_verification = True
                    result = tool.validate_and_execute(**arguments)
                finally:
                    settings.skip_human_verification = previous_skip
            else:
                result = tool.validate_and_execute(**arguments)
        except Exception:
            logger.exception("Tool execution failed")
            return jsonify({"error": "tool execution failed"}), 500

        store = get_tool_results_store()
        record_id = store.add(
            tool_name=tool.name,
            arguments=arguments,
            result=result,
            verified=verified,
        )

        return jsonify(
            {
                "status": "ok",
                "id": record_id,
                "tool": tool.name,
                "result": result,
            }
        )

    @bp.route("/tools/inject", methods=["POST"])
    def inject_tool_result():
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        data = request.get_json(silent=True) or {}
        record_id = data.get("id")
        if not isinstance(record_id, str) or record_id == "":
            return jsonify({"error": "id is required"}), 400

        store = get_tool_results_store()
        record = store.get(record_id)
        if record is None:
            return jsonify({"error": "result not found"}), 404

        content = (
            "[SYNTHETIC — Manual API tool execution]\n"
            f"Tool: {record.tool_name}\n"
            f"Arguments: {json.dumps(record.arguments)}\n"
            f"Result:\n{record.result}"
        )
        root_agent.steer(role="user", content=content)
        return jsonify({"status": "ok"})

    @bp.route("/tools/results", methods=["GET"])
    def list_tool_results():
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        store = get_tool_results_store()
        records = store.list_all()
        return jsonify(
            {
                "results": [
                    {
                        "id": r.id,
                        "tool": r.tool_name,
                        "arguments": r.arguments,
                        "result": r.result,
                        "verified": r.verified,
                        "created_at": r.created_at,
                    }
                    for r in records
                ]
            }
        )

    @bp.route("/tools/results/<record_id>", methods=["DELETE"])
    def delete_tool_result(record_id: str):
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        store = get_tool_results_store()
        deleted = store.delete(record_id)
        if not deleted:
            return jsonify({"error": "result not found"}), 404

        return jsonify({"status": "ok"})

    @bp.route("/tools/results", methods=["DELETE"])
    def clear_tool_results():
        root_agent = _get_active_root_agent()
        if root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        if request.args.get("confirm") != "true":
            return jsonify({"error": "confirm=true is required"}), 400

        store = get_tool_results_store()
        count = store.delete_all()
        return jsonify({"status": "ok", "deleted": count})

    def _tool_requires_verification(tool: BaseTool) -> bool:
        """Return True if the tool is marked as needing API-side verification.

        BaseTool defaults needs_verification_in_api to True. Each tool class
        must explicitly opt out by setting it to False if it is safe to execute
        via the external API without additional confirmation. This keeps the
        default behavior safe for unknown or dynamically loaded tools (e.g.
        MCP servers).
        """
        return bool(getattr(tool, "needs_verification_in_api", True))


def _read_store_events(store: EventStore, since_id: int, limit: int):
    """Read events from a store with pagination.

    Returns:
        Tuple of (events, last_id, has_more).
    """
    events: list[dict] = []
    last_id = 0
    has_more = False
    if not store.path.exists():
        return events, last_id, has_more

    try:
        lines = store.path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return events, last_id, has_more

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        entry_id = entry.get("id", 0)
        if entry_id <= since_id:
            continue
        if len(events) >= limit:
            has_more = True
            break
        events.append(entry)
        last_id = max(last_id, entry_id)

    return events, last_id, has_more
