import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Optional

from wichy.console import user_console
from wichy.helpers.verification_provider import (
    VerificationProvider,
    VerificationResponse,
)


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

        try:
            result = future.result(timeout=self._default_timeout)
        except Exception:  # TimeoutError, CancelledError, etc.
            # Auto-deny on timeout so the tool thread unblocks
            result = VerificationResponse(ok=False, reason="Verification timed out")
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
            return True
