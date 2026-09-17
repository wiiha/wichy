from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

from wichy.console import user_console
from wichy.constants import HIDE_FROM_LLM_PREFIX
from wichy.hooks.executor import HookExecutor
from wichy.tools import kill_registry
from wichy.tools.errors import format_error, format_tool_killed
from wichy.tools.registry import ToolMeta

console_tool_result = Console(quiet=True)


class ParametersModel(BaseModel):
    def info(self) -> str:
        """
        Generates a human readable string of the parameters
        that the model contains. Implementation is up to each
        tools parameters model.

        :return: Printable human readable string of the params.
        :rtype: str
        """
        return ""


class BaseTool(ABC, metaclass=ToolMeta):
    """Base class for all tools in the agent system."""

    name: str
    description: str
    description_long: Optional[str] = None
    """
    If a tool contains both a description and description_long. Then description_long
    will be passed to the LLM and description will be shown in the tool listing for
    the user.
    """
    #: True for the task tool: its kill record gets the longer async-raise
    #: grace, because the cascade kill is tried first.
    is_task_agent_tool: bool = False
    parameters_model: Type[ParametersModel]
    # -------------------------------------------------------------------------
    # Result offload control
    # -------------------------------------------------------------------------
    # Set to True to opt in to result offloading for this tool
    # Default is False (offloading disabled)
    enable_result_offload: bool = False

    # Tools exposed via the server API need an explicit caller verification
    # flag. A tool opts out by setting this to False when it is safe to run
    # unattended. Unknown / dynamically loaded tools inherit True.
    needs_verification_in_api: bool = True

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters."""
        pass

    def to_function_definition(self) -> Dict[str, Any]:
        """Convert tool to OpenAI/Anthropic function definition format."""
        schema = self.parameters_model.model_json_schema()

        # Clean up the schema (remove title, etc.)
        if "title" in schema:
            del schema["title"]

        if "properties" in schema:
            props_to_del = []
            for prop_name in schema["properties"]:
                # This is to be able to define params on tools
                # and hide these params from being presented to
                # the LLM model. Not pretty but it works.
                prop = schema["properties"][prop_name]
                if "description" in prop and prop["description"].startswith(
                    HIDE_FROM_LLM_PREFIX
                ):
                    props_to_del.append(prop_name)
            for pn in props_to_del:
                del schema["properties"][pn]

            for prop_name in schema["properties"]:
                prop = schema["properties"][prop_name]
                if "anyOf" in prop:
                    x = ""
                    for k in prop["anyOf"]:
                        if k["type"] != "null":
                            x += " or " + k["type"]

                    if x.startswith(" or "):
                        x = x[len(" or ") :]

                    prop["type"] = x
                    del prop["anyOf"]
                    schema["properties"][prop_name] = prop

        # Ensure 'required' field is present
        # Pydantic generates 'required' automatically based on Field(...) vs Field(default)
        if "required" not in schema and "properties" in schema:
            # If Pydantic didn't add it, create empty list (all optional)
            schema["required"] = []

        description = self.description
        if self.description_long:
            description = self.description_long

        description = description.strip()

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": schema,
            },
        }

    def validate_and_execute(self, **kwargs) -> str:
        """Validate parameters and execute; never lets a kill exception escape.

        Kill-exception containment: the impl completes its kill
        bookkeeping in a guarded tail; this backstop converts any
        delivery popping outside those guards. Only converts when THIS
        thread's call was kill-marked; an unrelated re-delivery (e.g. an
        inner tool's straggler surfacing here) becomes a neutral retry
        hint so nesting never mis-attributes a kill.
        """
        try:
            # Clear any stale finalize handoff left on this thread.
            kill_registry.mark_last_call_killed(False, None)
            return self._validate_and_execute_impl(**kwargs)
        except kill_registry.ToolKilledError:
            if kill_registry.last_call_was_killed():
                return format_tool_killed(
                    self.name, kill_registry.last_call_kill_reason()
                )
            return format_error(
                f"{self.name}: result was lost to a force-stop re-delivery; "
                "retry the tool call if it is still needed"
            )

    def _validate_and_execute_impl(self, **kwargs) -> str:
        """Register the call, run execute(), finalize the kill state."""
        # This will raise ValidationError if params are invalid
        # and also catch errors that are not handled by the tool
        # itself.
        res = ""
        final_args: Dict[str, Any] = {}
        execution_error: Optional[Exception] = None
        killed = False
        # None-init: a straggler re-delivery may land in this prologue.
        record: Optional[kill_registry.KillRecord] = None
        tool_call_id: Optional[str] = None
        previous_record: Optional[kill_registry.KillRecord] = None

        # Call id and agent id arrive as hidden kwargs (like
        # _can_query_results) and must not reach pydantic validation.
        tool_call_id = kwargs.pop("_tool_call_id", None) or (
            kill_registry.generate_tool_call_id()
        )
        agent_id = kwargs.pop("_agent_id", None) or (
            kill_registry.current_agent_id() or "root"
        )
        # Register before validation starts, so a kill can arrive at any point
        # of this call's life (hooks / verification included).
        record = kill_registry.register(
            tool_call_id=tool_call_id,
            tool_name=self.name,
            arguments=kwargs,
            agent_id=agent_id,
            in_task_agent_frame=self.is_task_agent_tool,
        )
        # Expose the record to execute() via a thread-local: tools like
        # task/bash attach their spawned agent/process to it so kills
        # can reach them. Saved/restored around execute() for nesting.
        previous_record = kill_registry.current_record()
        kill_registry.set_current_record(record)

        # try/finally: the record MUST leave the registry however this method
        # ends. The finally body stays tiny -- an async-kill straggler landing
        # there would escape both except handlers.
        try:
            try:
                validated_params = self.parameters_model(**kwargs)
                cmd_info = validated_params.info()
                if cmd_info != "":
                    cmd_info = " [pre]" + cmd_info + "[/pre]"
                user_console.print(
                    f"[dim][bold]→[/bold] Calling tool:[/dim] [bold]{self.name}[/bold][dim]{cmd_info}[/dim]"
                )

                # Run pre-tool hooks
                pre_result = HookExecutor.run_pre_hooks(
                    self, self.name, validated_params.model_dump()
                )

                # If pre-hook denied execution, return error immediately
                if not pre_result.approved:
                    res = format_error(
                        pre_result.error_message
                        or f"{self.name}: Hook denied execution"
                    )
                    user_console.print(
                        f"[red bold]✗[/red bold] tool {self.name} denied by hook"
                    )
                else:
                    # Use modified input if hooks changed it, otherwise use original
                    if pre_result.modified_input:
                        # Re-validate modified input
                        validated_params = self.parameters_model(
                            **pre_result.modified_input
                        )
                        final_args = validated_params.model_dump()
                    else:
                        final_args = validated_params.model_dump()

                    start_time = time.time()
                    # Pre-start kill: never run execute(). The check reads the
                    # record object, so a re-emitted tool_call_id cannot flip
                    # this fresh call.
                    if kill_registry.record_is_killed(record):
                        res = format_tool_killed(
                            self.name, kill_registry.record_kill_reason(record)
                        )
                        user_console.print(
                            f"[yellow bold]⨯[/yellow bold] tool {self.name} killed before start"
                        )
                        killed = True
                    else:
                        killed = False
                        try:
                            res = self.execute(**final_args)
                        except Exception as e:
                            # A force-kill delivers ToolKilledError here; any
                            # other exception is a genuine failure. The kill mark
                            # decides the final result below.
                            execution_error = e
                            res = format_error(f"{self.name}: {type(e).__name__}: {e}")
                            user_console.print(
                                f"[red bold]✗[/red bold] tool {self.name} failed"
                            )

                    # Calculate execution time
                    end_time = time.time()
                    execution_time = end_time - start_time

                    # Killed mid-execute: swap in the kill string and drop the
                    # partial output (the user stopped the call, the tool did
                    # not fail). This precedes the offload block so the nudge is
                    # never offloaded out of context.
                    if not killed and kill_registry.record_is_killed(record):
                        execution_error = None
                        killed = True
                        res = format_tool_killed(
                            self.name, kill_registry.record_kill_reason(record)
                        )
                        user_console.print(
                            f"[yellow bold]⨯[/yellow bold] tool {self.name} killed by user"
                        )

                    # Calculate result size metrics
                    char_count = len(res)
                    token_estimate = (
                        char_count // 4
                    )  # rough estimate: 1 token ≈ 4 chars

                    # A kill landing after the swap above but before this print
                    # must not produce a green "completed" line.
                    late_kill = (
                        not execution_error
                        and not killed
                        and (kill_registry.record_is_killed(record))
                    )
                    if late_kill:
                        user_console.print(
                            f"[yellow bold]⨯[/yellow bold] tool {self.name} killed by user"
                        )
                    elif not execution_error and not killed:
                        msg = f"[green bold]✓[/green bold] tool {self.name} completed"
                        size_info = f" [dim]({char_count} chars, ~{token_estimate} tokens)[/dim]"
                        if execution_time > 3:
                            if execution_time > 60:
                                minutes = int(execution_time // 60)
                                seconds = int(execution_time % 60)
                                time_str = f"{minutes}m {seconds}s"
                            else:
                                time_str = f"{execution_time:.2f}s"
                            msg = f"{msg} in {time_str}"

                        msg = f"{msg}{size_info}"
                        user_console.print(msg)

                    # Run post-tool hooks (even on exception for logging/monitoring).
                    # killed=True lets hooks tell a user-stopped call apart
                    # from a normal success (the res is the kill notice).
                    post_result = HookExecutor.run_post_hooks(
                        self,
                        self.name,
                        final_args,
                        res,
                        error=execution_error,
                        killed=killed,
                    )

                    # Use modified output if hooks changed it. A killed
                    # call's notice is never replaced: it must reach the
                    # LLM verbatim.
                    if post_result.modified_output and not killed:
                        res = post_result.modified_output

                    # ---------------------------------------------------------------------
                    # Result offload check
                    # ---------------------------------------------------------------------
                    # Never offload a killed call: its nudge must stay in context.
                    if not execution_error and not killed:
                        # Lazy import to avoid circular import with result_offload module
                        from wichy.result_offload import get_result_store, result_or_ref

                        # Clean up expired results periodically (1% chance per call)
                        if random.random() < 0.01:
                            store = get_result_store()
                            store.cleanup_expired()

                        # Apply offload logic
                        res = result_or_ref(
                            result=res,
                            tool_name=self.name,
                            input_args=final_args,
                            model_str=kwargs.get("model_str"),  # May be None
                            enable_offload=self.enable_result_offload,
                            can_query_results=kwargs.get(
                                "_can_query_results", True
                            ),  # Default True if not provided
                        )

            except Exception as e:
                res = format_error(f"{self.name}: {type(e).__name__}: {e}")
                user_console.print(f"[red bold]✗[/red bold] tool {self.name} failed")
        finally:
            # Straggler-safe tail: a re-delivered kill consumes itself on
            # landing, so retrying the ops is enough.
            killed_flag = False
            kill_reason = None
            for _attempt in range(3):
                try:
                    kill_registry.set_current_record(previous_record)
                    kill_registry.finish_call(tool_call_id)
                    killed_flag = kill_registry.record_is_killed(record)
                    kill_reason = kill_registry.record_kill_reason(record)
                    kill_registry.mark_last_call_killed(killed_flag, kill_reason)
                    break
                except kill_registry.ToolKilledError:
                    killed_flag = False
                    kill_reason = None
            if killed_flag:
                try:
                    res = format_tool_killed(self.name, kill_reason)
                    execution_error = None
                except BaseException:
                    pass

        # Log detailed error for debugging
        console_tool_result.log(
            Markdown(f"\n\n---\n\n### tool {self.name}\n\n{res}\n\n---\n\n"),
        )

        return res
