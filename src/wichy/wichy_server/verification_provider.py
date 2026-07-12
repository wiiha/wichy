import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Optional

from wichy.console import user_console
from wichy.event_log import log_event
from wichy.event_log.schema import preview_text
from wichy.helpers.verification_provider import (
    VerificationProvider,
    VerificationResponse,
)

# Module-level callback to retrieve the current root session id for events.
# Set during server bootstrap to avoid circular imports.
_get_root_session_id: Optional[Callable[[], Optional[str]]] = None


def set_session_id_callback(callback: Optional[Callable[[], Optional[str]]]) -> None:
    """Set a callback that returns the current root session id."""
    global _get_root_session_id
    _get_root_session_id = callback


@dataclass
class PendingVerification:
    id: str
    label: str
    message: Optional[str]
    args: str
    timestamp: float = field(default_factory=time.time)


class ServerVerificationProvider(VerificationProvider):
    def __init__(self, default_timeout: Optional[float] = None):
        self._lock = threading.Lock()
        self._pending: dict[str, PendingVerification] = {}
        self._futures: dict[str, Future[VerificationResponse]] = {}
        self._default_timeout = default_timeout

    def verify(
        self, label: str, message: Optional[str], all_args: str
    ) -> VerificationResponse:
        vid = str(uuid.uuid4())

        with self._lock:
            self._pending[vid] = PendingVerification(
                id=vid, label=label, message=message, args=all_args
            )
            future: Future[VerificationResponse] = Future()
            self._futures[vid] = future

        # Emit with ID so client can correlate transcript with pending list
        msg = f"\n[bold yellow]ACTION ({vid}):[/bold yellow] {label}"
        if message:
            msg += "\n" + message
        if all_args:
            msg += "\n" + all_args
        user_console.print(msg)

        self._emit(
            "verification_requested",
            {
                "vid": vid,
                "label": label,
                "message_preview": preview_text(message or ""),
                "args_preview": all_args[:200],
            },
        )

        try:
            result = future.result(timeout=self._default_timeout)
        except Exception:  # TimeoutError, CancelledError, etc.
            # Auto-deny on timeout so the tool thread unblocks
            result = VerificationResponse(ok=False, reason="Verification timed out")
            self._emit(
                "verification_resolved",
                {
                    "vid": vid,
                    "approved": False,
                    "reason": "timed out",
                },
            )
        finally:
            with self._lock:
                self._pending.pop(vid, None)
                self._futures.pop(vid, None)

        return result

    def list_pending(self) -> list[PendingVerification]:
        with self._lock:
            return list(self._pending.values())

    def respond(self, vid: str, approved: bool, reason: str = "") -> bool:
        with self._lock:
            future = self._futures.get(vid)
            if future is None:
                return False
            future.set_result(VerificationResponse(ok=approved, reason=reason))
        self._emit(
            "verification_resolved",
            {"vid": vid, "approved": approved, "reason": reason},
        )
        return True

    def _emit(self, event_type: str, payload: dict) -> None:
        """Emit a root session event if a session id callback is available."""
        if _get_root_session_id is None:
            return
        try:
            session_id = _get_root_session_id()
            if session_id:
                log_event(event_type, payload, session_id=session_id)
        except Exception:
            pass
