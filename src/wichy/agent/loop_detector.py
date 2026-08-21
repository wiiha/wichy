"""
Loop detection for agent tool calls.

Tracks a rolling window of tool-call signatures (SHA-256 of tool name +
canonical arguments + result). If any signature repeats more than the
threshold within the window, the agent is considered to be in a loop.

Each AgentCore instance gets its own LoopDetector, so RootAgent and
TaskAgent are tracked independently.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from typing import Deque

from wichy.config import settings


def compute_signature(tool_name: str, arguments_json: str, result: str) -> str:
    """Compute a SHA-256 signature for a single tool call.

    Args:
        tool_name: Name of the tool called.
        arguments_json: Raw JSON-arguments string from the LLM.
        result: String result returned by the tool (may be empty).

    Returns:
        Hex digest string.
    """
    try:
        canonical_args = json.dumps(
            json.loads(arguments_json), sort_keys=True, separators=(",", ":")
        )
    except (json.JSONDecodeError, TypeError):
        # Fall back to the raw string if JSON is malformed
        canonical_args = str(arguments_json)
    payload = f"{tool_name}|{canonical_args}|{result}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LoopDetector:
    """Rolling-window loop detector for agent tool calls."""

    def __init__(
        self,
        window_size: int | None = None,
        threshold: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Initialize the detector.

        All parameters default to the values from settings, so callers
        can omit them in normal use and override only for testing.

        Args:
            window_size: Max number of recent signatures to track (>= 1).
            threshold: Max repeats before triggering (>= 0). count > threshold
                triggers, so threshold=5 means the 6th repeat triggers.
            enabled: Master switch. If False, recording and checking
                are no-ops.

        Raises:
            ValueError: If window_size < 1 or threshold < 0.
        """
        self.window_size: int = (
            window_size if window_size is not None else settings.loop_detection_window
        )
        self.threshold: int = (
            threshold if threshold is not None else settings.loop_detection_threshold
        )
        self.enabled: bool = (
            enabled if enabled is not None else settings.loop_detection_enabled
        )
        if self.window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {self.window_size}")
        if self.threshold < 0:
            raise ValueError(f"threshold must be >= 0, got {self.threshold}")
        self._window: Deque[str] = deque(maxlen=self.window_size)
        self._lock = threading.Lock()

    def record(self, signature: str) -> bool:
        """Record a signature and return True if a loop is detected.

        The signature is added to the rolling window. After adding, the
        count of occurrences of this signature in the window is checked
        against the threshold.

        Thread-safe: uses an internal lock so concurrent task-agent
        threads cannot race on append+count.

        Args:
            signature: The SHA-256 hex digest from compute_signature().

        Returns:
            True if the signature count exceeds the threshold
            (count > threshold), False otherwise. Returns False when
            the detector is disabled.
        """
        if not self.enabled:
            return False
        with self._lock:
            self._window.append(signature)
            return self._window.count(signature) > self.threshold

    def is_looping(self) -> bool:
        """Return True if any signature in the window exceeds the threshold.

        This is a secondary check that scans the current window without
        recording a new signature.
        """
        if not self.enabled or not self._window:
            return False
        with self._lock:
            most_recent = self._window[-1]
            return self._window.count(most_recent) > self.threshold

    @property
    def window(self) -> Deque[str]:
        """Read-only access to the internal window (for testing)."""
        return self._window

    def reset(self) -> None:
        """Clear all recorded signatures."""
        with self._lock:
            self._window.clear()
