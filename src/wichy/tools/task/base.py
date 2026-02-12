import json
from datetime import date

from pydantic import BaseModel, Field
from rich.console import Console
from rich.markdown import Markdown

from wichy.helpers.context import ContextHandler
from wichy.helpers.environment_info import environment_information
from wichy.helpers.markdown import read_markdown_with_frontmatter
from wichy.helpers.prompt import preprocess_prompt
from wichy.helpers.string import strip_thinking_content, truncate_to_len
from wichy.llm_backend import LLMBackendContextLimitReached, Message, call, called_tool
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool, ParametersModel

console_task_agents = Console(quiet=True)

from pydantic import BaseModel


class TaskAgentDefinitionBase(BaseModel):
    name: str
    description: str
    tools: list[str] | None = None
    not_tools: list[str] | None = None
    system_prompt: str
    include_env_info: bool = False


class TaskAgent:
    def __init__(
        self,
        agent_definition: TaskAgentDefinitionBase,
        prompt: str,
        model: str,
        all_tools_not_instantiated: list[BaseTool],
    ):

        self.name = agent_definition.name
        self.description = agent_definition.description
        self.model = model

        tools: list[BaseTool] = []
        for t in all_tools_not_instantiated:
            tools.append(t())

        allowed_tools = agent_definition.tools
        if allowed_tools != None and len(allowed_tools) > 0:
            new_tools = []
            for tool in tools:
                if tool.name in allowed_tools:
                    new_tools.append(tool)

            all_tool_names = [t.name.lower() for t in tools]
            for t in allowed_tools:
                if not t in all_tool_names:
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

        context = ContextHandler(custom_suffix=self.name, sub_dir="task_agents")
        context.add(role="system", content=system_prompt)
        context.add(role="user", content=prompt)
        self.context = context

    def run(self):
        console_task_agents.log(
            Markdown(
                "\n\n---\n\n ### Task Agent "
                + self.name
                + " called\n\n- llm model: "
                + self.model
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
                "\n\n---\n\n ### Task Agent " + self.name + " - final message\n\n" + res
            )
        )
        return res

    def _tool_call(self, tools, item: called_tool):
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console_task_agents.log({"tool": name, "args": args})
        for tool in tools:
            if name == tool.name:
                result = tool.validate_and_execute(**args)

        if result is None:
            result = "There is no tool called " + item.function.name + "."
        return {"role": "tool", "tool_call_id": item.id, "content": result}

    def _handle_tools(self, tools, response: Message):
        if response.finish_reason != "tool_calls":
            return False

        self.context.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [t.model_dump() for t in response.tool_calls],
            }
        )

        console_task_agents.log(
            "[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]"
        )
        osz = len(self.context)
        for item in response.tool_calls:
            self.context.append(self._tool_call(tools, item))
        return len(self.context) != osz

    def _process(self, line=""):

        try:
            tools = self.tools
            if line != "":
                self.context.add(role="user", content=line)
            tool_defs = get_tool_definitions(tools)
            response = call(
                context=self.context(), tool_defs=tool_defs, model_str=self.model
            )

            while self._handle_tools(tools, response):
                response = call(self.context(), tool_defs, model_str=self.model)
            self.context.append({"role": "assistant", "content": response.content})
            return response.content
        except KeyboardInterrupt as e:
            return self._handle_interrupt(
                fallback_exception=Exception("user aborted execution of " + self.name)
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
        response = call(self.context(), tool_defs=None, model_str=self.model)
        self.context.append({"role": "assistant", "content": response.content})
        return response.content

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
                        if not (id in observed_tool_answer_ids):
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
