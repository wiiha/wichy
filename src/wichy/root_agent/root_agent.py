import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from rich.markdown import Markdown

from wichy.agent.core import AgentCore
from wichy.console import user_console
from wichy.constants import ROLE_ASSISTANT, ROLE_USER
from wichy.context.handler import new_context
from wichy.helpers.console import console
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import (
    LLMBackendMultimodalNotSupported,
    Message,
    call,
    called_tool,
)
from wichy.hooks.executor import HookExecutor
from wichy.hooks.types import HookType
from wichy.config import settings
from wichy.session_map.extractor import SessionMapExtractor
from wichy.session_map.store import SessionMapStore
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool


class ContextResetStrategies(str, Enum):
    NUKE = "nuke"
    SUMMARY = "summary"


class RootAgent(AgentCore):
    def __init__(
        self,
        model_str,
        tools: List[BaseTool],
        name: str = "NOT SET",
        context=None,
        skills=None,
        agent_has_first_initiative: bool = True,
        auto_compact_threshold: Optional[int] = None,
        print_info_lines: bool = True,
        session_map_model: Optional[str] = None,
    ):
        super().__init__()
        if context is not None:
            self.context = context
        else:
            self.context = new_context()
        self._name = name
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
            f"### Root Agent Info\n - **template name:** {self._name}\n- **model string:** {self.model_str}\n- **tools:**\n{tool_str}"
        ]

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
        self.context.start_watching(interval=2.0)

        self.agent_has_first_initiative = agent_has_first_initiative
        self.auto_compact_threshold = auto_compact_threshold
        self.current_prompt_tokens = 0

        # Session map components (lazy initialization)
        # session_map_model: None=disabled, ""=use root agent's model, "model"=specific model
        self._session_map_model = session_map_model
        self._session_map_store: SessionMapStore | None = None
        self._session_map_extractor: SessionMapExtractor | None = None

    # -------------------------------------------------------------------------
    # AgentCore abstract property implementation
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return the agent name."""
        return self._name

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
            if strip_thinking_content(resp.content) != "":
                result = (
                    "\n---\n\n### Assistant\n"
                    + strip_thinking_content(resp.content)
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

    def _init_session_map(self):
        """Initialize session map components."""
        # session_map_model: None=disabled, ""=use root agent's model, "model"=specific model
        if self._session_map_model is None:
            return

        if self._session_map_store is None:
            self._session_map_store = SessionMapStore()

        if self._session_map_extractor is None:
            # If session_map_model is empty string, use root agent's model
            model_str = (
                self._session_map_model if self._session_map_model else self.model_str
            )
            self._session_map_extractor = SessionMapExtractor(model_str=model_str)

    def _get_user_turn_count(self) -> int:
        """Count number of user turns in conversation."""
        # Context is likely a list-like object with messages
        # Each message has a 'role' key
        return len([m for m in self.context.context if m.get("role") == "user"])

    def _get_messages_since_turn(self, last_turn: int) -> list[dict]:
        """Get messages since the last extracted turn."""
        messages = []
        user_turn_count = 0

        for msg in self.context.context:
            if msg.get("role") == "user":
                user_turn_count += 1

            if user_turn_count > last_turn:
                messages.append(msg)

        return messages

    def _maybe_extract_session_map(self):
        """Check if session map extraction is due and run it."""
        # session_map_model: None=disabled, ""=use root agent's model, "model"=specific model
        if self._session_map_model is None:
            return

        self._init_session_map()

        # Ensure context handler is current (in case of reset/compaction)
        from wichy.session_map.api import set_context_handler

        set_context_handler(self.context)

        current_turn = self._get_user_turn_count()
        context_id = str(self.context.path)
        last_extracted = self._session_map_store.get_last_turn(context_id)

        if current_turn - last_extracted < settings.session_map_interval:
            return  # Not time yet

        # Get messages since last extraction
        messages_since_last = self._get_messages_since_turn(last_extracted)

        if not messages_since_last:
            return

        # Get existing map
        existing_map = self._session_map_store.get(context_id)

        # Extract with validation
        try:
            is_valid, nodes, edges, feedback = (
                self._session_map_extractor.extract_with_validation(
                    messages=messages_since_last,
                    existing_map=existing_map,
                    start_turn=last_extracted,
                )
            )

            # Log extraction feedback at DEBUG level
            logging.debug(
                f"Session map extraction: valid={is_valid}, nodes={len(nodes)}, feedback={feedback}"
            )

            if is_valid and nodes:
                self._session_map_store.merge_nodes(
                    context_id=context_id,
                    new_nodes=nodes,
                    new_edges=edges,
                    turn=current_turn,
                )
        except Exception as e:
            # Surface to user but don't crash the main loop
            from wichy.console import user_console

            user_console.print(
                f"[yellow]Warning: Session map extraction failed: {e}[/yellow]"
            )

    def process(self, line):
        self.context.append({"role": ROLE_USER, "content": line})
        tool_defs = get_tool_definitions(self.tools)

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
        self._maybe_extract_session_map()
        return response.message.content

    def drop_last_context_entry(self):
        if len(self.context) < 2:
            # only system msg left, cant drop that
            return

        self.context.drop()

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

        # Fire CONTEXT_COMPACT_POST hook after context is replaced with summary
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_COMPACT_POST,
            context_handler=self.context,
            root_agent=self,
            summary=summary_msg,
            is_auto_compact=is_auto_compact,
        )
        return
