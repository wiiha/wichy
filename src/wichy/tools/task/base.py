from datetime import date
from typing import Dict, List, Optional
import fnmatch
import threading

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

from wichy.agent.core import AgentCore
from wichy.config import settings
from wichy.constants import ROLE_ASSISTANT, ROLE_SYSTEM, ROLE_USER
from wichy.context.handler import ContextHandler
from wichy.helpers.environment_info import environment_information
from wichy.helpers.gen_id import gen_id
from wichy.helpers.prompt import preprocess_prompt
from wichy.llm_backend import (
    LLMBackendContextLimitReached,
    LLMBackendMultimodalNotSupported,
    Message,
    call,
)
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool
from wichy.tool_manager import _matches_tool_patterns

console_task_agents = Console(quiet=True)

# ---------------------------------------------------------------------------
# Turn-count warning threshold
# ---------------------------------------------------------------------------
_TURNS_WARNING_THRESHOLD = 5

# ---------------------------------------------------------------------------
# Global in-memory registry of running task agents
# ---------------------------------------------------------------------------
_TASK_AGENT_REGISTRY: dict[str, "TaskAgent"] = {}
_TASK_AGENT_REGISTRY_LOCK = threading.Lock()


def get_task_agent(agent_id: str) -> Optional["TaskAgent"]:
    """Return a running task agent by id, or None if not found."""
    with _TASK_AGENT_REGISTRY_LOCK:
        return _TASK_AGENT_REGISTRY.get(agent_id)


def list_task_agents() -> list["TaskAgent"]:
    """Return a snapshot of currently running task agents."""
    with _TASK_AGENT_REGISTRY_LOCK:
        return list(_TASK_AGENT_REGISTRY.values())


class TaskAgentDefinitionBase(BaseModel):
    name: str
    description: str
    tools: list[str] | None = None
    not_tools: list[str] | None = None
    system_prompt: str
    include_env_info: bool = False
    model: Optional[str] = None


class TaskAgent(AgentCore):
    def __init__(
        self,
        agent_definition: TaskAgentDefinitionBase,
        prompt: str,
        model: str,
        all_tools_not_instantiated: list[type[BaseTool]],
        max_turns: Optional[int] = None,
    ):
        super().__init__()
        self._name = agent_definition.name
        self._max_turns = max_turns
        self._turns_remaining = None
        # Stop / steer machinery
        self._stop_event = threading.Event()
        self._steer_queue: list[tuple[str, str]] = []
        self._steer_queue_lock = threading.Lock()
        self._turns_used = 0
        self.description = agent_definition.description
        self.model_str = (
            settings.task_tool_model_str
            or getattr(agent_definition, "model", None)
            or getattr(agent_definition, "model_str", None)
            or model
        )

        tools: list[BaseTool] = []
        for t in all_tools_not_instantiated:
            tools.append(t())

        allowed_tools = agent_definition.tools
        if allowed_tools is not None and len(allowed_tools) > 0:
            new_tools = []
            for tool in tools:
                if _matches_tool_patterns(tool.name, allowed_tools):
                    new_tools.append(tool)

            for tool_name in allowed_tools:
                if not any(
                    fnmatch.fnmatch(t.name.lower(), tool_name.strip().lower())
                    for t in tools
                ):
                    console_task_agents.log(
                        f"[yellow]warning[/yellow] task agent definition {agent_definition.name} mentions tool {tool_name} which does not exist."
                    )
            tools = new_tools

        if agent_definition.not_tools and len(agent_definition.not_tools) > 0:
            new_tools = []
            for tool in tools:
                if _matches_tool_patterns(tool.name, agent_definition.not_tools):
                    # listed as tool to skip
                    continue
                new_tools.append(tool)
            tools = new_tools

        self.tools = tools

        system_prompt = preprocess_prompt(
            prompt=agent_definition.system_prompt,
            verify_against={"tools": [x.name for x in self.tools]},
        )

        if agent_definition.include_env_info:
            system_prompt += (
                "\n\nHere is useful information about the environment you are running in:\n"
                + environment_information()
                + "\n\n"
            )
        else:
            today = date.today().isoformat()
            system_prompt += f"\n\nToday's date: {today}"

        context = ContextHandler(
            custom_suffix=f"{self._name}-{gen_id()}", sub_dir="task_agents"
        )
        if self._max_turns is not None:
            system_prompt += (
                f"\n\nYou have {self._max_turns} turns available for this task."
            )
            self._turns_remaining = self._max_turns
        else:
            self._turns_remaining = None
        context.add(role=ROLE_SYSTEM, content=system_prompt)
        context.add(role=ROLE_USER, content=prompt)
        self.context = context

    # -------------------------------------------------------------------------
    # AgentCore abstract property implementation
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return the agent name."""
        return self._name

    # -------------------------------------------------------------------------
    # AgentCore logging overrides - use TaskAgent's console
    # -------------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """Log a debug message using TaskAgent's console."""
        console_task_agents.log(message)

    def _log_dict(self, data: Dict) -> None:
        """Log a dictionary using TaskAgent's console."""
        console_task_agents.log(data)

    # -------------------------------------------------------------------------
    # Tool handling - uses base class method
    # -------------------------------------------------------------------------

    def _handle_tools(self, tools: List[BaseTool], response: Message) -> bool:
        """Handle tool calls from LLM response."""
        modified, _ = self._handle_tools_base(
            tools, response, inject_model_str=True, pre_append_hook=None
        )
        return modified

    # -------------------------------------------------------------------------
    # TaskAgent-specific methods
    # -------------------------------------------------------------------------

    def run(self):
        # Register in global registry
        agent_id = self.context.custom_suffix  # format: <name>-<12-char-hex>
        with _TASK_AGENT_REGISTRY_LOCK:
            _TASK_AGENT_REGISTRY[agent_id] = self

        try:
            console_task_agents.log(
                Markdown(
                    "\n\n---\n\n ### Task Agent "
                    + self._name
                    + " called\n\n- llm model: "
                    + self.model_str
                    + "\n\n- available tools: "
                    + ",".join([t.name for t in self.tools])
                    + "\n\n- given task:\n\n"
                    + (
                        self.context()[1]["content"]
                        if len(self.context) >= 2
                        else self.context()[0]["content"]
                    )
                )
            )
            res = self._process()
            console_task_agents.log(
                Markdown(
                    "\n\n---\n\n ### Task Agent "
                    + self._name
                    + " - final message\n\n"
                    + res
                )
            )
            return res
        finally:
            with _TASK_AGENT_REGISTRY_LOCK:
                _TASK_AGENT_REGISTRY.pop(agent_id, None)

    def _process(self, line=""):

        try:
            tools = self.tools
            if line != "":
                self.context.add(role=ROLE_USER, content=line)
            tool_defs = get_tool_definitions(tools)

            # --- stop / steer hook before initial call ---
            if self._stop_event.is_set():
                self._drain_steer_queue()
                return self._gen_summary()
            self._drain_steer_queue()
            # --- end ---

            try:
                response = call(
                    context=self.context(tick=True),
                    tool_defs=tool_defs,
                    model_str=self.model_str,
                )
            except LLMBackendMultimodalNotSupported as e:
                # Try to fix the context by replacing multimodal content with text
                console_task_agents.log(
                    f"[yellow]Multimodal not supported: {e.message}[/yellow]"
                )
                console_task_agents.log("[yellow]Attempting to fix context...[/yellow]")

                if self._fix_multimodal_context():
                    console_task_agents.log(
                        "[yellow]Fixed context, retrying...[/yellow]"
                    )
                    response = call(
                        context=self.context(),
                        tool_defs=tool_defs,
                        model_str=self.model_str,
                    )
                else:
                    raise

            self._turns_used = 1  # Initial call counts as turn 1

            while True:
                # Guard: if max turns reached and assistant emitted tools, do not execute them
                if (
                    self._max_turns is not None
                    and self._turns_used >= self._max_turns
                    and response.message.tool_calls
                ):
                    self.context.add(
                        role=ROLE_USER,
                        content="No more tool calls are allowed. Provide your final answer.",
                    )
                    break
                elif not self._handle_tools(tools, response.message):
                    break

                self._turns_used += 1

                # Max turns enforcement — exceeded limit, force summary
                if self._max_turns is not None and self._turns_used > self._max_turns:
                    return self._gen_summary()

                # Penultimate round warning
                if self._max_turns is not None and self._turns_used == self._max_turns:
                    warning = (
                        "This is your last turn with tools available. "
                        "The next turn will be tool-free and you must provide a final answer. "
                        "Use your remaining tools wisely."
                    )
                    self.context.add(role=ROLE_USER, content=warning)

                # Accumulating late reminders (cache-friendly)
                if self._max_turns is not None:
                    remaining = self._max_turns - self._turns_used
                    effective_threshold = min(
                        _TURNS_WARNING_THRESHOLD, max(2, self._max_turns - 1)
                    )
                    if remaining <= effective_threshold:
                        self.context.add(
                            role=ROLE_USER,
                            content=f"You have {remaining} turns remaining for this task.",
                        )

                # --- stop / steer hook at bottom of loop ---
                if self._stop_event.is_set():
                    self._drain_steer_queue()
                    return self._gen_summary()
                self._drain_steer_queue()
                # --- end ---

                tool_defs_for_call = (
                    None
                    if self._max_turns is not None
                    and self._turns_used == self._max_turns
                    else tool_defs
                )

                try:
                    response = call(
                        self.context(tick=True),
                        tool_defs_for_call,
                        model_str=self.model_str,
                    )
                except LLMBackendMultimodalNotSupported as e:
                    console_task_agents.log(
                        f"[yellow]Multimodal not supported: {e.message}[/yellow]"
                    )
                    console_task_agents.log(
                        "[yellow]Attempting to fix context...[/yellow]"
                    )

                    if self._fix_multimodal_context():
                        console_task_agents.log(
                            "[yellow]Fixed context, retrying...[/yellow]"
                        )
                        response = call(
                            context=self.context(),
                            tool_defs=tool_defs_for_call,
                            model_str=self.model_str,
                        )
                    else:
                        raise

            # Append assistant message with reasoning if present
            entry = {"role": ROLE_ASSISTANT, "content": response.message.content}
            if response.message.reasoning:
                entry["reasoning"] = response.message.reasoning
            self.context.append(entry)
            return response.message.content
        except KeyboardInterrupt:
            return self._handle_interrupt(
                fallback_exception=Exception("user aborted execution of " + self._name)
            )
        except LLMBackendContextLimitReached as e:
            # okay, context exploded while working
            # let us stop agent execution and return
            # summary. Let us assume that it was the
            # last context entry that made it go BOOM.
            self.context.drop()
            return self._handle_interrupt(fallback_exception=e)

    def _gen_summary(self):
        c = (
            "Your next answer will be your last message. "
            + "Consider your initial task and try answering "
            + "it to the best of you ability given the information at hand. "
            + "However, do not lie, if the the available information isn't enough then just say that."
        )
        self.context.add(
            role="user",
            content=c,
        )
        # There is a very sad case in which we reach this part of the code
        # and the context will still explode. For now I think we will just
        # let the task agent die on us.
        response = call(
            self.context(tick=True), tool_defs=None, model_str=self.model_str
        )
        entry = {"role": ROLE_ASSISTANT, "content": response.message.content}
        if response.message.reasoning:
            entry["reasoning"] = response.message.reasoning
        self.context.append(entry)
        return response.message.content

    def _handle_interrupt(self, fallback_exception: Exception):
        # the goal here is to force an exit and summarize
        # what the agent managed so far.

        # context could be in a broken state at this point,
        # meaning there might be tool calls that didn't get answered.

        last_entry = self.context()[-1]

        if last_entry["role"] == "assistant":
            # if tool calls, drop entry
            if last_entry.get("tool_calls"):
                self.context.drop()
            return self._gen_summary()

        if last_entry["role"] == "user":
            # this should not really happen for an agent,
            # but let us have it as a case.
            return self._gen_summary()

        if last_entry["role"] == "tool":
            # okay, so we aborted somewhere in a
            # or after a tool exec. We now dont
            # know if there is additional tool
            # calls that were never answered.
            # Let us find the last assistant msg
            # and see how many tool calls there were
            observed_tool_answer_ids = []
            i = len(self.context) - 1

            while i > 1:
                e = self.context()[i]
                if e["role"] == "tool":
                    observed_tool_answer_ids.append(e["tool_call_id"])
                if e["role"] == "assistant":
                    # okay, so we are back at the assistant, let us explore the tool calls
                    tcs = list(e["tool_calls"])
                    missing_call = False
                    for tc in tcs:
                        id = str(tc["id"])
                        if id not in observed_tool_answer_ids:
                            missing_call = True
                            # only one missing is enough
                            break
                    if missing_call:
                        self.context.drop(n=i)
                    return self._gen_summary()

                i = i - 1

        # we should never end up here
        # if we do, use fallback error
        raise fallback_exception

    # ---------------------------------------------------------------------------
    # Stop / steer / status API
    # ---------------------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal the agent to stop after its current tool/LLM round."""
        self._stop_event.set()

    def steer(self, role: str, content: str) -> None:
        """Queue a steer message to be injected before the next LLM call."""
        with self._steer_queue_lock:
            self._steer_queue.append((role, content))

    def _drain_steer_queue(self) -> None:
        """Flush all queued steer messages into the context.

        Uses self.context.steer() to preserve the existing console log
        (ContextHandler.steer() prints [italic]steer injected...[/italic]).
        """
        with self._steer_queue_lock:
            queue = self._steer_queue[:]
            self._steer_queue.clear()
        for role, content in queue:
            self.context.steer(role=role, content=content)

    def status(self) -> dict:
        """Return a JSON-serializable status snapshot."""
        return {
            "id": self.context.custom_suffix,
            "name": self._name,
            "description": self.description,
            "model": self.model_str,
            "turns_used": self._turns_used,
            "turns_limit": self._max_turns,
            "status": "stopping" if self._stop_event.is_set() else "running",
        }
