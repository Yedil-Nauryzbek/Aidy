import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ActionRecord:
    id: int
    action_intent: str
    entities: dict
    inverse_action: Optional[dict]
    timestamp: float
    chain_id: int


class ActionHistory:
    def __init__(self, max_actions: int = 20, chain_gap_seconds: float = 5.0, max_token_buffer: int = 256):
        self.max_actions = max_actions
        self.chain_gap_seconds = chain_gap_seconds
        self.max_token_buffer = max(16, int(max_token_buffer))
        self._records: List[ActionRecord] = []
        self._recent_tokens: List[str] = []
        self._next_id = 1
        self._current_chain_id = 1
        self._last_ts = None
        self._force_new_chain = False

    def _append_tokens_from_value(self, value):
        if value is None:
            return
        if isinstance(value, str):
            for tok in value.lower().split():
                tok = tok.strip()
                if tok:
                    self._recent_tokens.append(tok)
            return
        if isinstance(value, dict):
            for v in value.values():
                self._append_tokens_from_value(v)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._append_tokens_from_value(item)

    def _tail_cut_tokens(self):
        if len(self._recent_tokens) > self.max_token_buffer:
            self._recent_tokens = self._recent_tokens[-self.max_token_buffer :]

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

        self._append_tokens_from_value(record.action_intent)
        self._append_tokens_from_value(record.entities)
        self._append_tokens_from_value(record.inverse_action)
        self._tail_cut_tokens()

        if len(self._records) > self.max_actions:
            self._records = self._records[-self.max_actions :]

    def pop_last(self) -> Optional[ActionRecord]:
        if not self._records:
            return None
        rec = self._records.pop()
        return rec

    def pop_chain(self, chain_id: int) -> List[ActionRecord]:
        chain = [r for r in self._records if r.chain_id == chain_id]
        if not chain:
            return []
        self._records = [r for r in self._records if r.chain_id != chain_id]
        return chain

    def get_last(self) -> Optional[ActionRecord]:
        if not self._records:
            return None
        return self._records[-1]

    def get_chain(self, chain_id: int) -> List[ActionRecord]:
        return [r for r in self._records if r.chain_id == chain_id]

    def get_recent_tokens(self) -> List[str]:
        return list(self._recent_tokens)

    def clear(self):
        self._records.clear()
        self._recent_tokens.clear()
        self._last_ts = None

    def break_chain(self):
        self._force_new_chain = True
