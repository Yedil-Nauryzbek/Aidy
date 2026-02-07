from dataclasses import dataclass, field
import time


PENDING_NEED_STEPS = "NEED_STEPS"
PENDING_NEED_CHOICE = "NEED_CHOICE"
PENDING_NEED_TARGET = "NEED_TARGET"


@dataclass
class PendingAction:
    pending_type: str
    base_intent: str
    direction: str | None = None
    entities: dict = field(default_factory=dict)
    max_choice: int | None = None
    created_at: float = field(default_factory=time.time)
    invalid_attempts: int = 0


class FollowUpManager:
    def __init__(self, ttl_seconds: float = 8.0):
        self.ttl_seconds = ttl_seconds
        self._pending: PendingAction | None = None

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

    def get_pending(self) -> PendingAction | None:
        if not self.is_valid():
            return None
        return self._pending

    def register_invalid_attempt(self) -> int:
        pending = self.get_pending()
        if pending is None:
            return 0
        pending.invalid_attempts += 1
        return pending.invalid_attempts
