import time


class ContextManager:
    def __init__(self, ttl_seconds: float = 7.5, min_confidence: float = 0.2, main_confidence: float = 0.4):
        self.ttl_seconds = ttl_seconds
        self.min_confidence = min_confidence
        self.main_confidence = main_confidence
        self._intent = None
        self._entities = None
        self._ts = None

    def set_context(self, intent: str, entities: dict | None):
        self._intent = (intent or "").strip().lower() or None
        self._entities = entities or {}
        self._ts = time.time()

    def get_context(self) -> dict | None:
        if not self.is_valid():
            return None
        return {
            "last_intent": self._intent,
            "last_entities": self._entities or {},
            "timestamp": self._ts,
        }

    def clear_context(self):
        self._intent = None
        self._entities = None
        self._ts = None

    def is_valid(self) -> bool:
        if not self._intent or self._ts is None:
            return False
        if (time.time() - self._ts) > self.ttl_seconds:
            self.clear_context()
            return False
        return True


def should_merge_context(ctx_intent: str, api_intent: str) -> bool:
    if not ctx_intent:
        return False
    if api_intent and api_intent != ctx_intent:
        return False
    return True
