from datetime import date
from typing import Dict, List

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

from wichy.agent.core import AgentCore
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

console_task_agents = Console(quiet=True)


class TaskAgentDefinitionBase(BaseModel):
    name: str
    description: str
    tools: list[str] | None = None
    not_tools: list[str] | None = None
    system_prompt: str
    include_env_info: bool = False


class TaskAgent(AgentCore):
    def __init__(
        self,
        agent_definition: TaskAgentDefinitionBase,
        prompt: str,
        model: str,
        all_tools_not_instantiated: list[BaseTool],
    ):
        super().__init__()
        self._name = agent_definition.name
        self.description = agent_definition.description
        self.model_str = model  # Store in base class's model_str

        tools: list[BaseTool] = []
        for t in all_tools_not_instantiated:
            tools.append(t())

        allowed_tools = agent_definition.tools
        if allowed_tools is not None and len(allowed_tools) > 0:
            new_tools = []
            for tool in tools:
                if tool.name in allowed_tools:
                    new_tools.append(tool)

            all_tool_names = [t.name.lower() for t in tools]
            for t in allowed_tools:
                if t not in all_tool_names:
                    console_task_agents.log(
                        f"[yellow]warning[/yellow] task agent definition {agent_definition.name} mentions tool {t} which does not exist."
                    )
            tools = new_tools

        if agent_definition.not_tools and len(agent_definition.not_tools) > 0:
            new_tools = []
            for t in tools:
                if t.name in agent_definition.not_tools:
                    # listed as tool to skip
                    continue
                new_tools.append(t)
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

    def _process(self, line=""):

        try:
            tools = self.tools
            if line != "":
                self.context.add(role=ROLE_USER, content=line)
            tool_defs = get_tool_definitions(tools)

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

            while self._handle_tools(tools, response.message):
                try:
                    response = call(
                        self.context(tick=True), tool_defs, model_str=self.model_str
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
                            tool_defs=tool_defs,
                            model_str=self.model_str,
                        )
                    else:
                        raise

            self.context.append(
                {"role": ROLE_ASSISTANT, "content": response.message.content}
            )
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
        self.context.append(
            {"role": ROLE_ASSISTANT, "content": response.message.content}
        )
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
