from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from uuid import uuid4


@dataclass(frozen=True)
class PendingMessage:
    id: str
    line: str


class TrackedInputQueue(Queue[object]):
    def put(  # type: ignore[override]
        self,
        item: str,
        block: bool = True,
        timeout: float | None = None,
    ) -> str:
        message = PendingMessage(id=uuid4().hex, line=item)
        super().put(message, block=block, timeout=timeout)
        return message.id

    def put_nowait(self, item: str) -> str:  # type: ignore[override]
        return self.put(item, block=False)

    def get(self, block: bool = True, timeout: float | None = None) -> str:
        return super().get(block=block, timeout=timeout)  # type: ignore[return-value]

    def _get(self) -> str:
        message = super()._get()
        if not isinstance(message, PendingMessage):
            raise TypeError("TrackedInputQueue contains an unexpected item")
        return message.line

    def get_nowait(self) -> str:
        return self.get(block=False)

    def snapshot(self) -> list[PendingMessage]:
        with self.mutex:
            return [
                message for message in self.queue if isinstance(message, PendingMessage)
            ]

    def edit(self, message_id: str, line: str) -> int | None:
        with self.mutex:
            for position, message in enumerate(self.queue):
                if isinstance(message, PendingMessage) and message.id == message_id:
                    self.queue[position] = PendingMessage(id=message.id, line=line)
                    return position
            return None

    def delete(self, message_id: str) -> bool:
        with self.mutex:
            for position, message in enumerate(self.queue):
                if isinstance(message, PendingMessage) and message.id == message_id:
                    del self.queue[position]
                    # Deletion retires one unfinished task and frees a slot.
                    if self.unfinished_tasks > 0:
                        self.unfinished_tasks -= 1
                        if self.unfinished_tasks == 0:
                            self.all_tasks_done.notify_all()
                    self.not_full.notify()
                    return True
            return False
