from dataclasses import dataclass, field
import time


@dataclass
class LastStepAction:
    base_intent: str
    direction: str
    last_steps: int
    entities: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class LastStepActionManager:
    def __init__(self, ttl_seconds: float = 12.0):
        self.ttl_seconds = ttl_seconds
        self._last: LastStepAction | None = None

    def record(self, base_intent: str, direction: str, steps: int, entities: dict | None = None):
        self._last = LastStepAction(
            base_intent=base_intent,
            direction=(direction or "").upper(),
            last_steps=max(1, min(10, int(steps))),
            entities=dict(entities or {}),
        )

    def get_if_fresh(self, ttl_seconds: float | None = None) -> LastStepAction | None:
        if self._last is None:
            return None
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if (time.time() - self._last.timestamp) > ttl:
            self.clear()
            return None
        return self._last

    def clear(self):
        self._last = None
