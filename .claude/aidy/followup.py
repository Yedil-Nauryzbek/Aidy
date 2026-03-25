from dataclasses import dataclass, field
import time
from typing import Optional


PENDING_NEED_STEPS = "NEED_STEPS"
PENDING_NEED_CHOICE = "NEED_CHOICE"
PENDING_NEED_TARGET = "NEED_TARGET"
PENDING_NEED_TIMER_DURATION = "NEED_TIMER_DURATION"
PENDING_NEED_SONG = "NEED_SONG"


@dataclass
class PendingAction:
    pending_type: str
    base_intent: str
    direction: Optional[str] = None
    entities: dict = field(default_factory=dict)
    max_choice: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    invalid_attempts: int = 0


class FollowUpManager:
    def __init__(self, ttl_seconds: float = 8.0):
        self.ttl_seconds = ttl_seconds
        self._pending: Optional[PendingAction] = None

    def set_pending(self, pending: PendingAction):
        self._pending = pending

    def clear_pending(self):
        self._pending = None

    def is_valid(self) -> bool:
        if self._pending is None:
            return False
        if (time.time() - self._pending.created_at) > self.ttl_seconds:
            self.clear_pending()
            return False
        return True

    def get_pending(self) -> Optional[PendingAction]:
        if not self.is_valid():
            return None
        return self._pending

    def register_invalid_attempt(self) -> int:
        pending = self.get_pending()
        if pending is None:
            return 0
        pending.invalid_attempts += 1
        return pending.invalid_attempts
