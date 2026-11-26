import time
from dataclasses import dataclass


@dataclass
class ActionRecord:
    id: int
    action_intent: str
    entities: dict
    inverse_action: dict | None
    timestamp: float
    chain_id: int


class ActionHistory:
    def __init__(self, max_actions: int = 20, chain_gap_seconds: float = 5.0):
        self.max_actions = max_actions
        self.chain_gap_seconds = chain_gap_seconds
        self._records: list[ActionRecord] = []
        self._next_id = 1
        self._current_chain_id = 1
        self._last_ts = None
        self._force_new_chain = False

    def push(self, record: ActionRecord):
        now = time.time()
        if self._force_new_chain or (self._last_ts is not None and (now - self._last_ts) > self.chain_gap_seconds):
            self._current_chain_id += 1
            self._force_new_chain = False
        record.id = self._next_id
        self._next_id += 1
        record.timestamp = now
        record.chain_id = self._current_chain_id
        self._records.append(record)
        self._last_ts = now
        if len(self._records) > self.max_actions:
            self._records = self._records[-self.max_actions :]

    def pop_last(self) -> ActionRecord | None:
        if not self._records:
            return None
        rec = self._records.pop()
        return rec

    def pop_chain(self, chain_id: int) -> list[ActionRecord]:
        chain = [r for r in self._records if r.chain_id == chain_id]
        if not chain:
            return []
        self._records = [r for r in self._records if r.chain_id != chain_id]
        return chain

    def get_last(self) -> ActionRecord | None:
        if not self._records:
            return None
        return self._records[-1]

    def get_chain(self, chain_id: int) -> list[ActionRecord]:
        return [r for r in self._records if r.chain_id == chain_id]

    def clear(self):
        self._records.clear()
        self._last_ts = None

    def break_chain(self):
        self._force_new_chain = True

