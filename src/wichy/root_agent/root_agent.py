from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from rich.markdown import Markdown

from wichy.agent.core import AgentCore
from wichy.console import user_console
from wichy.constants import ROLE_ASSISTANT, ROLE_USER
from wichy.context.handler import new_context
from wichy.event_log import log_event
from wichy.event_log.schema import preview_content
from wichy.helpers.console import console
from wichy.helpers.string import strip_thinking_content
from wichy.hooks.context_access import set_active_context as hooks_set_active_context
from wichy.hooks.executor import HookExecutor
from wichy.hooks.types import HookType
from wichy.llm_backend import (
    LLMBackendMultimodalNotSupported,
    Message,
    call,
    called_tool,
)
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool

_DEFAULT_WATCH_INTERVAL = 2.0


class ContextResetStrategies(str, Enum):
    NUKE = "nuke"
    SUMMARY = "summary"


class RootAgent(AgentCore):
    def __init__(
        self,
        model_str: str,
        tools: List[BaseTool],
        name: str = "NOT SET",
        display_name: Optional[str] = None,
        context=None,
        skills=None,
        agent_has_first_initiative: bool = True,
        auto_compact_threshold: Optional[int] = None,
        print_info_lines: bool = True,
    ):
        super().__init__()
        if context is not None:
            self.context = context
        else:
            self.context = new_context()
        self._name = name
        self._display_name = display_name
        self._model_str = model_str
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
            f"### Root Agent Info\n - **template name:** {self._name}\n- **model string:** {self.model_str}\n- **tools:**\n{tool_str}"
        ]

        if self._display_name:
            info_lines.append(f"- **display name:** {self._display_name}")

        # Add skills in alphabetical order
        if self.skills:
            skill_names = sorted(self.skills.keys())
            skills_str = ", ".join(skill_names)
            info_lines.append(f"- **skills:** {skills_str}")

        self.context.add_log(
            {"source": "root_agent", "data": {"info_lines": info_lines}}
        )
        if print_info_lines:
            user_console.print(Markdown("\n".join(info_lines)))

        # Start file watching for live sync with web editor
        self.context.start_watching(interval=_DEFAULT_WATCH_INTERVAL)

        self.agent_has_first_initiative = agent_has_first_initiative
        self.auto_compact_threshold = auto_compact_threshold
        self.current_prompt_tokens = 0

    # -------------------------------------------------------------------------
    # AgentCore abstract property implementation
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return the agent name."""
        return self._name

    @property
    def display_name(self) -> str:
        """Return the display name for terminal output. Falls back to 'Assistant'."""
        return self._display_name or "Assistant"

    @property
    def model_str(self) -> str:
        """Return the current model string."""
        return self._model_str

    @model_str.setter
    def model_str(self, value: str) -> None:
        """Set a new model string for subsequent LLM calls."""
        self._model_str = value

    def _emit_event(self, event_type: str, payload: dict) -> None:
        """Emit an event to the root session event log if a context exists."""
        try:
            session_id = self.context.session_id
        except Exception:
            return
        try:
            log_event(event_type, payload, session_id=session_id)
        except Exception as e:
            console.log(f"[yellow]Event emission failed: {e}[/yellow]")

    # -------------------------------------------------------------------------
    # AgentCore logging overrides - use RootAgent's console
    # -------------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """Log a debug message using RootAgent's console."""
        console.log(message)

    def _log_dict(self, data: Dict) -> None:
        """Log a dictionary using RootAgent's console."""
        console.log(data)

    # -------------------------------------------------------------------------
    # Tool execution - delegates to base class
    # -------------------------------------------------------------------------

    def tool_call(
        self, tools: List[BaseTool], item: called_tool
    ) -> Tuple[Dict, Optional[List[Dict[str, Any]]]]:
        """
        Execute a tool call and return the result message along with any multimodal content.

        Returns:
            Tuple of (tool_result_message, multimodal_content_parts or None)
        """
        # RootAgent injects model_str into tool args
        return self._tool_call(tools, item, inject_model_str=True)

    # -------------------------------------------------------------------------
    # Tool handling - delegates to base class with thinking content display
    # -------------------------------------------------------------------------

    def handle_tools(self, tools: List[BaseTool], response: Message) -> bool:
        """Handle tool calls from LLM response, displaying thinking content to user."""

        def display_thinking_content(resp: Message) -> None:
            """Hook to display thinking content before processing tool calls."""
            content = resp.content
            if isinstance(content, str) and strip_thinking_content(content) != "":
                result = (
                    f"\n---\n\n### {self.display_name}\n"
                    + strip_thinking_content(content)
                    + "\n\n---"
                )
                markdown = Markdown(result)
                user_console.print(markdown)

        modified, _ = self._handle_tools_base(
            tools,
            response,
            inject_model_str=True,
            pre_append_hook=display_thinking_content,
        )
        return modified

    # -------------------------------------------------------------------------
    # RootAgent-specific methods
    # -------------------------------------------------------------------------

    def _update_token_counts(self, usage: dict[str, Any] | None) -> None:
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
        user_console.print(
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

    def process(self, line: str) -> str:
        HookExecutor.run_context_hooks(
            HookType.PRE_USER_MESSAGE,
            root_agent=self,
            context_handler=self.context,
            message=line,
        )
        self._emit_event(
            "user_message_received",
            {"content_preview": preview_content(line), "source": "repl"},
        )
        self.context.append({"role": ROLE_USER, "content": line})
        tool_defs = get_tool_definitions(self.tools)

        self._emit_event(
            "llm_call_started",
            {
                "model_str": self.model_str,
                "message_count": len(self.context.context),
                "tool_count": len(tool_defs),
            },
        )
        try:
            response = call(
                context=self.context(tick=True),
                tool_defs=tool_defs,
                model_str=self.model_str,
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
        self._emit_event(
            "llm_call_completed",
            {
                "model_str": self.model_str,
                "finish_reason": response.message.finish_reason,
                "has_tool_calls": bool(getattr(response.message, "tool_calls", None)),
                "has_reasoning": bool(getattr(response.message, "reasoning", None)),
                "usage": response.usage,
            },
        )
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
            self._emit_event(
                "llm_call_started",
                {
                    "model_str": self.model_str,
                    "message_count": len(self.context.context),
                    "tool_count": len(tool_defs),
                },
            )
            try:
                response = call(
                    context=self.context(tick=True),
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
            self._update_token_counts(response.usage)
            self._emit_event(
                "llm_call_completed",
                {
                    "model_str": self.model_str,
                    "finish_reason": response.message.finish_reason,
                    "has_tool_calls": bool(
                        getattr(response.message, "tool_calls", None)
                    ),
                    "has_reasoning": bool(getattr(response.message, "reasoning", None)),
                    "usage": response.usage,
                },
            )

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

        entry = {"role": ROLE_ASSISTANT, "content": response.message.content}
        if response.message.reasoning:
            entry["reasoning"] = response.message.reasoning
        self.context.append(entry)
        # Final token update after processing complete
        self._update_token_counts(response.usage)
        if self.check_token_threshold():
            self._auto_compact_context()
            # in the case we auto compacted,
            # I still want the last response
            # from the model to be part of
            # the context.
            re_entry = {"role": ROLE_ASSISTANT, "content": response.message.content}
            if response.message.reasoning:
                re_entry["reasoning"] = response.message.reasoning
            self.context.append(re_entry)

        self._emit_event(
            "root_agent_response_ready",
            {
                "content_preview": preview_content(response.message.content),
                "has_reasoning": bool(getattr(response.message, "reasoning", None)),
                "usage": response.usage,
            },
        )

        hook_result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=self,
            context_handler=self.context,
            response_content=response.message.content,
            response_reasoning=response.message.reasoning,
            usage=response.usage,
        )

        final_content = response.message.content
        if hook_result.modified_output is not None:
            final_content = hook_result.modified_output
            reasoning_kw = {}
            if response.message.reasoning:
                reasoning_kw["reasoning"] = response.message.reasoning
            try:
                self.context.update_message(
                    len(self.context) - 1,
                    {"role": ROLE_ASSISTANT, "content": final_content, **reasoning_kw},
                )
            except Exception as e:
                console.log(
                    f"[yellow]Warning: PRE_RESPONSE_TO_USER update_message failed: {e}[/yellow]"
                )

        return final_content

    def steer(self, role: str = ROLE_USER, content: str = "") -> None:
        """
        Inject a mid-flight message into the agent's context.

        The message is appended via :meth:`ContextHandler.steer` so it is
        thread-safe and persisted. It will be visible to the LLM on the
        *next* call boundary (after the current LLM call or tool batch
        finishes).

        Args:
            role (str): Message role. Defaults to ``"user"``.
            content (str): Message content.
        """
        self._emit_event(
            "steer_injected",
            {"role": role, "content_preview": preview_content(content)},
        )
        self.context.steer(role=role, content=content)

    def drop_last_context_entry(self):
        if len(self.context) < 2:
            # only system msg left, cant drop that
            return

        self.context.drop()
        self._emit_event(
            "context_dropped",
            {"dropped_count": 1, "context_file": str(self.context.path)},
        )

    def _notify_context_editor(self, old_context: Any) -> None:
        """Stop watching old context and notify the web editor of the change."""
        try:
            old_context.stop_watching()
        except Exception:
            pass
        try:
            from wichy.tools.context_editor import api as context_editor_api

            context_editor_api.set_active_context(self.context)
            hooks_set_active_context(self.context)
        except Exception:
            pass

    def reset_context(self, strategy: ContextResetStrategies):
        if strategy == ContextResetStrategies.SUMMARY:
            return self.compact_context()

        # Fire CONTEXT_RESET_PRE hook before nuking the context
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_RESET_PRE,
            context_handler=self.context,
            root_agent=self,
            reset_strategy=strategy.value,
        )

        # nuke, default case
        first_prompt = self.context()[0]
        old_context = self.context
        ctx = new_context(session_id=self.context.session_id, resumed_after="reset")
        ctx.append(first_prompt)
        self.context = ctx
        # Start watching new context
        self.context.start_watching(interval=_DEFAULT_WATCH_INTERVAL)
        self._notify_context_editor(old_context)
        self._emit_event(
            "context_reset",
            {
                "strategy": strategy.value,
                "previous_context_file": str(old_context.path),
                "context_file": str(self.context.path),
            },
        )

        # Fire CONTEXT_RESET_POST hook after new context is created
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_RESET_POST,
            context_handler=self.context,
            root_agent=self,
            reset_strategy=strategy.value,
        )

    def compact_context(
        self,
        guideline_from_user_on_what_to_keep: Optional[str] = None,
        is_auto_compact=False,
    ):
        first_prompt = self.context()[0]
        old_context = self.context

        # Fire CONTEXT_COMPACT_PRE hook before LLM summarization
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_COMPACT_PRE,
            context_handler=self.context,
            root_agent=self,
            is_auto_compact=is_auto_compact,
        )

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
            user_console.print(Markdown(summary_msg))

        # Create new context with original system prompt and summary
        n_ctx = new_context(session_id=self.context.session_id, resumed_after="compact")
        n_ctx.append(first_prompt)
        n_ctx.add(role="user", content=summary_msg)
        self.context = n_ctx
        self.context.start_watching(interval=_DEFAULT_WATCH_INTERVAL)
        self._notify_context_editor(old_context)
        self._emit_event(
            "context_compacted",
            {
                "is_auto_compact": is_auto_compact,
                "previous_context_file": str(old_context.path),
                "context_file": str(self.context.path),
                "summary_preview": preview_content(summary_msg),
            },
        )

        # Fire CONTEXT_COMPACT_POST hook after context is replaced with summary
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_COMPACT_POST,
            context_handler=self.context,
            root_agent=self,
            summary=summary_msg,
            is_auto_compact=is_auto_compact,
        )
        return
