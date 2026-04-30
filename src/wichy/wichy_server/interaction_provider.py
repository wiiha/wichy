import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from wichy.console import user_console
from wichy.helpers.interaction_provider import InteractionProvider


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

        try:
            result = future.result(timeout=self._default_timeout)
        except Exception:
            # Timeout or cancellation: auto-fail gracefully
            answers = {q.header: "No selection" for q in questions}
            result = {"answers": answers}
            if metadata:
                result["metadata"] = metadata
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
        return True
