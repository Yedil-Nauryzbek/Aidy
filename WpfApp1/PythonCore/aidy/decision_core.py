import re
from typing import Callable

from .followup import FollowUpManager, PendingAction, PENDING_NEED_STEPS, PENDING_NEED_CHOICE
from .config import NUMERIC_FOLLOWUP_WORD_TO_VALUE
from .last_step_action import LastStepActionManager


STEP_REQUIRED = {
    "volume_up": {"base": "volume_change", "direction": "UP"},
    "volume_down": {"base": "volume_change", "direction": "DOWN"},
    "brightness_up": {"base": "brightness_change", "direction": "UP"},
    "brightness_down": {"base": "brightness_change", "direction": "DOWN"},
}

STEP_REQUIRED_PHRASES = {
    "volume_up": {
        "volume up",
        "sound up",
        "increase volume",
        "louder",
        "make it louder",
    },
    "volume_down": {
        "volume down",
        "sound down",
        "decrease volume",
        "quieter",
        "make it quieter",
    },
    "brightness_up": {
        "brightness up",
        "increase brightness",
        "brighten screen",
        "make screen brighter",
    },
    "brightness_down": {
        "brightness down",
        "decrease brightness",
        "dim screen",
        "make screen darker",
    },
}

STEP_INTENT_TO_LEGACY = {
    "volume_up": "volume up",
    "volume_down": "volume down",
    "brightness_up": "brightness up",
    "brightness_down": "brightness down",
}

def normalize_intent_name(intent: str) -> str:
    return " ".join((intent or "").strip().lower().split()).replace(" ", "_")


def api_intent_to_step_intent(intent: str) -> str | None:
    key = normalize_intent_name(intent)
    if key in STEP_REQUIRED:
        return key
    return None


def detect_step_intent_from_text(text: str) -> str | None:
    t = " ".join((text or "").strip().lower().split())
    for intent, phrases in STEP_REQUIRED_PHRASES.items():
        if t in phrases:
            return intent
    return None


def parse_numeric_input(text: str) -> int | None:
    t = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    normalized = " ".join(t.split())
    if not normalized:
        return None
    mapped = NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(normalized)
    if mapped is not None:
        return mapped
    tokens = normalized.split()
    if len(tokens) != 1:
        return None
    tok = tokens[0]
    if tok.isdigit():
        value = int(tok)
        if 1 <= value <= 10:
            return value
    return NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(tok)


def extract_steps_value(text: str) -> int | None:
    t = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    normalized = " ".join(t.split())
    if not normalized:
        return None
    mapped = NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(normalized)
    if mapped is not None:
        return mapped
    tokens = normalized.split()
    for tok in tokens:
        if tok.isdigit():
            value = int(tok)
            if 1 <= value <= 10:
                return value
        mapped_tok = NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(tok)
        if mapped_tok is not None:
            return mapped_tok
    for i in range(len(tokens) - 1):
        pair = f"{tokens[i]} {tokens[i + 1]}"
        mapped_pair = NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(pair)
        if mapped_pair is not None:
            return mapped_pair
    return None


class DecisionCore:
    def __init__(
        self,
        executor: Callable[[str, dict], bool],
        follow_up: FollowUpManager | None = None,
        step_required: dict | None = None,
        last_step_actions: LastStepActionManager | None = None,
        repeat_last_steps: bool = False,
    ):
        self.executor = executor
        self.follow_up = follow_up or FollowUpManager(ttl_seconds=8.0)
        self.step_required = step_required or STEP_REQUIRED
        self.last_step_actions = last_step_actions or LastStepActionManager(ttl_seconds=12.0)
        self.repeat_last_steps = repeat_last_steps

    def handle_step_intent(self, intent_name: str, entities: dict | None = None) -> dict:
        key = normalize_intent_name(intent_name)
        if key not in self.step_required:
            return {"handled": False}
        payload = dict(entities or {})
        steps = payload.get("magnitude_steps")
        if steps is None:
            self.follow_up.set_pending(
                PendingAction(
                    pending_type=PENDING_NEED_STEPS,
                    base_intent=self.step_required[key]["base"],
                    direction=self.step_required[key]["direction"],
                    entities={k: v for k, v in payload.items() if k != "magnitude_steps"},
                )
            )
            return {"handled": True, "executed": False, "prompt": "How much?"}

        value = int(steps)
        value = max(1, min(10, value))
        ok = self.executor(
            self.step_required[key]["base"],
            {
                **{k: v for k, v in payload.items() if k != "magnitude_steps"},
                "direction": self.step_required[key]["direction"],
                "magnitude_steps": value,
            },
        )
        if ok:
            self.last_step_actions.record(
                base_intent=self.step_required[key]["base"],
                direction=self.step_required[key]["direction"],
                steps=value,
                entities={k: v for k, v in payload.items() if k != "magnitude_steps"},
            )
        return {"handled": True, "executed": bool(ok), "prompt": None}

    def handle_numeric_input(self, value: int) -> dict:
        pending = self.follow_up.get_pending()
        if not pending:
            return {"handled": True, "executed": False, "prompt": "Not now."}

        value = max(1, min(10, int(value)))
        if pending.pending_type == PENDING_NEED_STEPS:
            ok = self.executor(
                pending.base_intent,
                {
                    **(pending.entities or {}),
                    "direction": pending.direction,
                    "magnitude_steps": value,
                },
            )
            self.follow_up.clear_pending()
            if ok:
                self.last_step_actions.record(
                    base_intent=pending.base_intent,
                    direction=(pending.direction or "").upper(),
                    steps=value,
                    entities=pending.entities or {},
                )
            return {"handled": True, "executed": bool(ok), "prompt": None}

        if pending.pending_type == PENDING_NEED_CHOICE:
            max_choice = int(pending.max_choice or 0)
            if max_choice > 0 and 1 <= value <= max_choice:
                ok = self.executor(
                    pending.base_intent,
                    {
                        **(pending.entities or {}),
                        "choice": value,
                    },
                )
                self.follow_up.clear_pending()
                return {"handled": True, "executed": bool(ok), "prompt": None}
            return self.handle_non_numeric_during_pending()

        self.follow_up.clear_pending()
        return {"handled": True, "executed": False, "prompt": "Cancelled."}

    def handle_non_numeric_during_pending(self) -> dict:
        pending = self.follow_up.get_pending()
        if not pending:
            return {"handled": False}
        attempts = self.follow_up.register_invalid_attempt()
        if attempts >= 2:
            self.follow_up.clear_pending()
            return {"handled": True, "executed": False, "prompt": "Cancelled."}
        return {"handled": True, "executed": False, "prompt": "Say a number from one to ten."}

    def handle_more_action(self) -> dict:
        if self.follow_up.get_pending():
            return {"handled": True, "executed": False, "prompt": "Say a number."}
        last = self.last_step_actions.get_if_fresh(ttl_seconds=12)
        if not last:
            return {"handled": True, "executed": False, "prompt": "Nothing to repeat."}
        steps = last.last_steps if self.repeat_last_steps else 1
        ok = self.executor(
            last.base_intent,
            {
                **(last.entities or {}),
                "direction": last.direction,
                "magnitude_steps": steps,
            },
        )
        if ok:
            self.last_step_actions.record(last.base_intent, last.direction, steps, last.entities)
        return {"handled": True, "executed": bool(ok), "prompt": None if ok else "Not now."}

    def handle_less_action(self) -> dict:
        if self.follow_up.get_pending():
            return {"handled": True, "executed": False, "prompt": "Say a number."}
        last = self.last_step_actions.get_if_fresh(ttl_seconds=12)
        if not last:
            return {"handled": True, "executed": False, "prompt": "Nothing to adjust."}
        steps = last.last_steps if self.repeat_last_steps else 1
        direction = "DOWN" if last.direction == "UP" else "UP"
        ok = self.executor(
            last.base_intent,
            {
                **(last.entities or {}),
                "direction": direction,
                "magnitude_steps": steps,
            },
        )
        if ok:
            self.last_step_actions.record(last.base_intent, direction, steps, last.entities)
        return {"handled": True, "executed": bool(ok), "prompt": None if ok else "Not now."}
