import json
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from rich import print
from rich.console import Console
from rich.markdown import Markdown

from wichy.constants import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER
from wichy.context.handler import new_context
from wichy.helpers.console import console
from wichy.helpers.multimodal import (
    build_multimodal_user_message,
    extract_multimodal_content,
    fix_multimodal_context,
)
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import (
    LLMBackendMultimodalNotSupported,
    Message,
    call,
    called_tool,
)
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool


class ContextResetStrategies(str, Enum):
    NUKE = "nuke"
    SUMMARY = "summary"


class RootAgent:
    def __init__(
        self,
        model_str,
        tools: List[BaseTool],
        name: str = "NOT SET",
        context=None,
        skills=None,
        agent_has_first_initiative: bool = True,
        auto_compact_threshold: Optional[int] = None,
    ):
        if context is not None:
            self.context = context
        else:
            self.context = new_context()
        self.name = name
        self.model_str = model_str
        self.tools = tools
        self.skills = skills or {}
        console.log(
            {"model_str": self.model_str, "tools": ", ".join([t.name for t in tools])}
        )
        tool_str = ""
        for t in tools:
            tool_str += t.name + ", "
        tool_str = tool_str.removesuffix(", ")

        # Build info string
        info_lines = [
            f"### Root Agent Info\n - **template name:** {self.name}\n- **model string:** {self.model_str}\n- **tools:**\n{tool_str}"
        ]

        # Add skills in alphabetical order
        if self.skills:
            skill_names = sorted(self.skills.keys())
            skills_str = ", ".join(skill_names)
            info_lines.append(f"- **skills:** {skills_str}")

        self.context.add_log(
            {"source": "root_agent", "data": {"info_lines": info_lines}}
        )
        print(Markdown("\n".join(info_lines)))

        # Start file watching for live sync with web editor
        self.context.start_watching(interval=2.0)

        self.agent_has_first_initiative = agent_has_first_initiative
        self.auto_compact_threshold = auto_compact_threshold
        self.current_prompt_tokens = 0

    def tool_call(
        self, tools, item: called_tool
    ) -> Tuple[Dict, Optional[List[Dict[str, Any]]]]:
        """
        Execute a tool call and return the result message along with any multimodal content.

        Returns:
            Tuple of (tool_result_message, multimodal_content_parts or None)
        """
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console.log({"tool": name, "args": args})

        args["model_str"] = self.model_str

        for tool in tools:
            if name == tool.name:
                result = tool.validate_and_execute(**args)
                break

        if result is None:
            result = "There is no tool called " + item.function.name + "."

        # Check for multimodal content in tool result
        display_content, multimodal_parts = extract_multimodal_content(result)

        tool_message = {
            "role": ROLE_TOOL,
            "tool_call_id": item.id,
            "content": display_content,
        }
        return tool_message, multimodal_parts

    def handle_tools(self, tools, response: Message):
        if response.finish_reason != "tool_calls":
            return False

        if strip_thinking_content(response.content) != "":
            result = (
                "\n---\n\n### Assistant\n"
                + strip_thinking_content(response.content)
                + "\n\n---"
            )
            markdown = Markdown(result)
            print(markdown)

        self.context.append(
            {
                "role": ROLE_ASSISTANT,
                "content": response.content,
                "tool_calls": [t.model_dump() for t in response.tool_calls],
            }
        )

        console.log(
            "[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]"
        )
        osz = len(self.context)

        # Collect multimodal content from all tool calls
        multimodal_parts: List[Dict[str, Any]] = []

        for item in response.tool_calls:
            tool_message, mm_parts = self.tool_call(tools, item)
            self.context.append(tool_message)
            if mm_parts:
                multimodal_parts.extend(mm_parts)

        # If any tool returned multimodal content, inject a user message with it
        if multimodal_parts:
            # Build and add multimodal user message
            multimodal_message = build_multimodal_user_message(multimodal_parts)
            self.context.append(multimodal_message)
            console.log("[italic]injected multimodal content into context[/italic]")

        return len(self.context) != osz

    def _update_token_counts(self, usage):
        """
        Update token counts from usage information.

        Args:
            usage: Dictionary containing token usage information
        """
        if usage:
            self.current_prompt_tokens = usage.get("prompt_tokens", 0)

    def check_token_threshold(self) -> bool:
        """
        Check if current token count exceeds auto_compact_threshold.

        Returns:
            True if threshold is exceeded, False otherwise
        """
        return bool(
            self.auto_compact_threshold
            and self.current_prompt_tokens >= self.auto_compact_threshold
        )

    def _auto_compact_context(self):
        """
        Auto-compact context when token threshold is exceeded.
        """
        console.log("[yellow]Auto-compacting...[/yellow]")
        Console().print(
            "[dim][bold]→[/bold] Root Agent: [yellow]Auto-compacting...[/yellow][/dim]"
        )
        self.current_prompt_tokens = 0

        msg = (
            "This is an auto compaction mid session. The summary you generate should contain:\n"
            "- The over all goal of the current session.\n"
            "- Gotchas and other key things from the current session.\n"
            "- Any tool results that are of particular importance.\n"
            "- What the current task is and what is the next step in order to reach task completion."
        )

        self.compact_context(
            guideline_from_user_on_what_to_keep=msg, is_auto_compact=True
        )

    def _fix_multimodal_context(self) -> bool:
        """
        Find and replace multimodal content in context with text placeholders.

        Returns:
            True if any multimodal content was found and replaced, False otherwise.
        """
        found = fix_multimodal_context(self.context)
        if found:
            console.log("[yellow]Fixed multimodal content in context[/yellow]")
        return found

    def process(self, line):
        self.context.append({"role": ROLE_USER, "content": line})
        tool_defs = get_tool_definitions(self.tools)

        try:
            response = call(
                context=self.context(), tool_defs=tool_defs, model_str=self.model_str
            )
        except LLMBackendMultimodalNotSupported as e:
            # Try to fix the context by replacing multimodal content with text
            console.log(f"[yellow]Multimodal not supported: {e.message}[/yellow]")
            console.log("[yellow]Attempting to fix context...[/yellow]")

            if self._fix_multimodal_context():
                console.log("[yellow]Fixed context, retrying...[/yellow]")
                # Retry with fixed context
                response = call(
                    context=self.context(),
                    tool_defs=tool_defs,
                    model_str=self.model_str,
                )
            else:
                # No multimodal content found, re-raise
                raise

        # Update token counts
        self._update_token_counts(response.usage)
        # Log token usage after first LLM call
        if response.usage:
            self.context.add_log(
                {
                    "type": "token_usage",
                    "prompt_tokens": response.usage.get("prompt_tokens", 0),
                    "completion_tokens": response.usage.get("completion_tokens", 0),
                    "total_tokens": response.usage.get("total_tokens", 0),
                    "current_prompt_tokens": self.current_prompt_tokens,
                }
            )
        # might need to already do a compaction
        if self.check_token_threshold():
            self._auto_compact_context()
        while self.handle_tools(self.tools, response.message):
            try:
                response = call(
                    context=self.context(),
                    tool_defs=tool_defs,
                    model_str=self.model_str,
                )
            except LLMBackendMultimodalNotSupported as e:
                # Handle multimodal error during tool loop
                console.log(f"[yellow]Multimodal not supported: {e.message}[/yellow]")
                console.log("[yellow]Attempting to fix context...[/yellow]")

                if self._fix_multimodal_context():
                    console.log("[yellow]Fixed context, retrying...[/yellow]")
                    response = call(
                        context=self.context(),
                        tool_defs=tool_defs,
                        model_str=self.model_str,
                    )
                else:
                    raise

        # Log token usage before final assistant response
        if response.usage:
            self.context.add_log(
                {
                    "type": "token_usage",
                    "prompt_tokens": response.usage.get("prompt_tokens", 0),
                    "completion_tokens": response.usage.get("completion_tokens", 0),
                    "total_tokens": response.usage.get("total_tokens", 0),
                    "current_prompt_tokens": self.current_prompt_tokens,
                }
            )

        self.context.append(
            {"role": ROLE_ASSISTANT, "content": response.message.content}
        )
        # Final token update after processing complete
        self._update_token_counts(response.usage)
        if self.check_token_threshold():
            self._auto_compact_context()
            # in the case we auto compacted,
            # I still want the last response
            # from the model to be part of
            # the context.
            self.context.append(
                {"role": ROLE_ASSISTANT, "content": response.message.content}
            )

        return response.message.content

    def drop_last_context_entry(self):
        if len(self.context) < 2:
            # only system msg left, cant drop that
            return

        self.context.drop()

    def reset_context(self, strategy: ContextResetStrategies):
        if strategy == ContextResetStrategies.SUMMARY:
            return self.compact_context()

        # nuke, default case
        first_prompt = self.context()[0]
        old_context = self.context
        ctx = new_context()
        ctx.append(first_prompt)
        self.context = ctx
        # Start watching new context
        self.context.start_watching(interval=2.0)
        # Stop watching old context
        try:
            old_context.stop_watching()
        except Exception:
            pass
        # Notify web editor of context change
        try:
            from wichy.tools.context_editor import api as context_editor_api

            context_editor_api.set_active_context(self.context)
        except Exception:
            pass

    def compact_context(
        self,
        guideline_from_user_on_what_to_keep: Optional[str] = None,
        is_auto_compact=False,
    ):
        first_prompt = self.context()[0]
        old_context = self.context
        guideline_for_compacting = "Please summarize our conversation. Keep it structured. Include any external sources mentioned."
        if guideline_from_user_on_what_to_keep:
            guideline_for_compacting += (
                " Further guidelines to follow when summarizing:\n"
                + guideline_from_user_on_what_to_keep
            )
        # Keep the original system prompt from the first context entry
        ctx = new_context()
        ctx.start_watching()
        ctx.append(first_prompt)
        # add in messages from old context
        for i in old_context.context[1:]:
            ctx.append(i)

        # Add a summary message to the context
        ctx.add(
            role="user",
            content=guideline_for_compacting,
        )

        # Generate the summary
        response = call(context=ctx(), model_str=self.model_str)

        # we have no more use of the temporary summary context
        ctx.delete()

        summary_msg = response.message.content

        if not is_auto_compact:
            # Create the summary message
            summary_msg = (
                "\n\n---\n\n### Summary of context\n\n" + response.message.content
            )

            # Print the summary
            print(Markdown(summary_msg))

        # Create new context with original system prompt and summary
        n_ctx = new_context()
        n_ctx.append(first_prompt)
        n_ctx.add(role="user", content=summary_msg)
        self.context = n_ctx
        self.context.start_watching(interval=2.0)
        # Stop watching old context
        try:
            old_context.stop_watching()
        except Exception:
            pass
        # Notify web editor of context change
        try:
            from wichy.tools.context_editor import api as context_editor_api

            context_editor_api.set_active_context(self.context)
        except Exception:
            pass
        return
