import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from wichy.console import user_console
from wichy.event_log import log_event
from wichy.helpers.interaction_provider import InteractionProvider


# Module-level callback to retrieve the current root session id for events.
_get_root_session_id: Optional[Callable[[], Optional[str]]] = None


def set_session_id_callback(callback: Optional[Callable[[], Optional[str]]]) -> None:
    """Set a callback that returns the current root session id."""
    global _get_root_session_id
    _get_root_session_id = callback


@dataclass
class PendingQuestions:
    id: str
    questions: List
    metadata: Optional[Dict]
    timestamp: float = field(default_factory=time.time)


class ServerInteractionProvider(InteractionProvider):
    def __init__(self, default_timeout: Optional[float] = 300.0):
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingQuestions] = {}
        self._futures: Dict[str, Future[Dict]] = {}
        self._default_timeout = default_timeout

    def ask_questions(self, questions: List, metadata: Optional[Dict] = None) -> Dict:
        qid = str(uuid.uuid4())

        with self._lock:
            self._pending[qid] = PendingQuestions(
                id=qid, questions=questions, metadata=metadata
            )
            future: Future[Dict] = Future()
            self._futures[qid] = future

        # Write a hint into the console transcript
        msg = f"[bold blue]AWAITING ANSWERS ({qid}):[/bold blue]"
        for q in questions:
            msg += "\n" + f"- {q.question}"

        user_console.print(msg)
        self._emit(
            "question_asked",
            {
                "qid": qid,
                "question_count": len(questions),
                "headers": [q.header for q in questions],
            },
        )

        try:
            result = future.result(timeout=self._default_timeout)
        except Exception:
            # Timeout or cancellation: auto-fail gracefully
            answers = {q.header: "No selection" for q in questions}
            result = {"answers": answers}
            if metadata:
                result["metadata"] = metadata
            self._emit(
                "question_answered",
                {"qid": qid, "answers_count": len(answers), "timed_out": True},
            )
        finally:
            with self._lock:
                self._pending.pop(qid, None)
                self._futures.pop(qid, None)

        return result

    def list_pending(self) -> List[PendingQuestions]:
        with self._lock:
            return list(self._pending.values())

    def respond(self, qid: str, answers: Dict[str, str]) -> bool:
        """
        Called by the Flask handler to unblock ask_questions().
        Missing headers are filled with "No selection".
        """
        with self._lock:
            future = self._futures.get(qid)
            pending = self._pending.get(qid)

        if future is None or pending is None:
            return False

        merged = {
            q.header: answers.get(q.header, "No selection") for q in pending.questions
        }
        result = {"answers": merged}
        if pending.metadata:
            result["metadata"] = pending.metadata

        future.set_result(result)
        self._emit(
            "question_answered",
            {"qid": qid, "answers_count": len(merged), "timed_out": False},
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
