"""
Hook result types for the Wichy hooks system.

This module defines the action types and result structures returned by hook functions.
Hooks can approve, deny, modify inputs/outputs, or log information.

Usage:
    from wichy.hooks.result import HookAction, HookResult

    # Quick factory methods:
    result = HookResult.approve()
    result = HookResult.deny("Access denied")
    result = HookResult.modify_input({"path": "/new/path"})
    result = HookResult.modify_output("Modified output")
    result = HookResult.log({"key": "value"})
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class HookAction(Enum):
    """Actions a hook can return.

    Each action type determines how the hook system will process
    the result and affect the execution flow.

    Attributes:
        APPROVE: Allow execution to continue (default action)
        DENY: Block execution, return error
        MODIFY_INPUT: Change tool arguments (pre_tool hooks only)
        MODIFY_OUTPUT: Change tool result (post_tool hooks only)
        LOG: Log only, don't affect flow
    """

    APPROVE = "approve"
    DENY = "deny"
    MODIFY_INPUT = "modify_input"
    MODIFY_OUTPUT = "modify_output"
    LOG = "log"


@dataclass
class HookResult:
    """Result returned by a hook function.

    This dataclass encapsulates all possible outcomes from a hook execution,
    including the action to take and any associated data.

    Attributes:
        action: The action the hook system should take (default: APPROVE)
        modified_input: New arguments for the tool (for MODIFY_INPUT action)
        modified_output: New result string (for MODIFY_OUTPUT action)
        error_message: Error description (for DENY action)
        log_data: Data to log (for LOG action)
        hook_name: Name of the hook function that produced this result
        execution_time_ms: Time taken to execute the hook in milliseconds

    Example:
        >>> result = HookResult.approve()
        >>> result.action
        <HookAction.APPROVE: 'approve'>

        >>> result = HookResult.deny("Permission denied")
        >>> result.action
        <HookAction.DENY: 'deny'>
        >>> result.error_message
        'Permission denied'
    """

    action: HookAction = HookAction.APPROVE
    modified_input: Optional[Dict[str, Any]] = None
    modified_output: Optional[str] = None
    error_message: Optional[str] = None
    log_data: Optional[Any] = None
    hook_name: str = ""
    execution_time_ms: Optional[float] = None

    @classmethod
    def approve(cls) -> "HookResult":
        """Create an APPROVE result.

        This allows execution to continue normally.

        Returns:
            HookResult with APPROVE action

        Example:
            >>> result = HookResult.approve()
            >>> result.action
            <HookAction.APPROVE: 'approve'>
        """
        return cls(action=HookAction.APPROVE)

    @classmethod
    def deny(cls, message: str) -> "HookResult":
        """Create a DENY result with an error message.

        This blocks execution and returns the error message.

        Args:
            message: The error message explaining why execution was denied

        Returns:
            HookResult with DENY action and error message

        Example:
            >>> result = HookResult.deny("File access not allowed")
            >>> result.action
            <HookAction.DENY: 'deny'>
            >>> result.error_message
            'File access not allowed'
        """
        return cls(action=HookAction.DENY, error_message=message)

    @classmethod
    def modify_input(cls, new_args: Dict[str, Any]) -> "HookResult":
        """Create a MODIFY_INPUT result with new arguments.

        This changes the tool arguments before execution.
        Only valid for pre_tool hooks.

        Args:
            new_args: Dictionary of new arguments to pass to the tool

        Returns:
            HookResult with MODIFY_INPUT action and new arguments

        Example:
            >>> result = HookResult.modify_input({"path": "/safe/path"})
            >>> result.action
            <HookAction.MODIFY_INPUT: 'modify_input'>
            >>> result.modified_input
            {'path': '/safe/path'}
        """
        return cls(action=HookAction.MODIFY_INPUT, modified_input=new_args)

    @classmethod
    def modify_output(cls, new_output: str) -> "HookResult":
        """Create a MODIFY_OUTPUT result with a new output.

        This changes the tool result after execution.
        Only valid for post_tool hooks.

        Args:
            new_output: The new output string to return

        Returns:
            HookResult with MODIFY_OUTPUT action and new output

        Example:
            >>> result = HookResult.modify_output("Sanitized output")
            >>> result.action
            <HookAction.MODIFY_OUTPUT: 'modify_output'>
            >>> result.modified_output
            'Sanitized output'
        """
        return cls(action=HookAction.MODIFY_OUTPUT, modified_output=new_output)

    @classmethod
    def log(cls, data: Any = None) -> "HookResult":
        """Create a LOG result with optional data.

        This logs information without affecting execution flow.

        Args:
            data: Optional data to log (can be any type)

        Returns:
            HookResult with LOG action

        Example:
            >>> result = HookResult.log({"accessed": "/path/to/file"})
            >>> result.action
            <HookAction.LOG: 'log'>
            >>> result.log_data
            {'accessed': '/path/to/file'}
        """
        return cls(action=HookAction.LOG, log_data=data)
