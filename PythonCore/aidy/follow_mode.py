from dataclasses import dataclass
import time

from .last_step_action import LastStepAction


@dataclass
class FollowModeState:
    active: bool = False
    expires_at: float = 0.0
    last_step_action: LastStepAction | None = None


class FollowModeManager:
    def __init__(self, ttl_seconds: float = 10.0, enabled: bool = True):
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self.state = FollowModeState()

    def activate(self, last_step_action: LastStepAction):
        if not self.enabled:
            self.clear()
            return
        self.state.active = True
        self.state.expires_at = time.time() + self.ttl_seconds
        self.state.last_step_action = last_step_action

    def is_active(self) -> bool:
        if not self.enabled:
            return False
        if not self.state.active:
            return False
        if time.time() > self.state.expires_at:
            self.clear()
            return False
        return True

    def clear(self):
        self.state = FollowModeState()

    def get_last_step_action_if_active(self) -> LastStepAction | None:
        if not self.is_active():
            return None
        return self.state.last_step_action


def extract_after_wake(text: str, wake_keywords: set[str]) -> str | None:
    t = " ".join((text or "").strip().lower().split())
    if not t:
        return None
    ordered = sorted({w.strip().lower() for w in wake_keywords if w and w.strip()}, key=len, reverse=True)
    for w in ordered:
        if t == w:
            return ""
        prefix = f"{w} "
        if t.startswith(prefix):
            return t[len(prefix):].strip()
    return None


def classify_follow_input(
    text: str,
    wake_keywords: set[str],
    more_phrases: set[str],
    less_phrases: set[str],
    pending_active: bool,
):
    t = " ".join((text or "").strip().lower().split())
    if pending_active and (t in more_phrases or t in less_phrases):
        return {"kind": "pending_block"}
    wake_tail = extract_after_wake(t, wake_keywords)
    if wake_tail is not None:
        return {"kind": "wake", "tail": wake_tail}
    if t in more_phrases:
        return {"kind": "more"}
    if t in less_phrases:
        return {"kind": "less"}
    if t in {"stop", "cancel"}:
        return {"kind": "cancel"}
    return {"kind": "other"}


def resolve_follow_mode_gate(
    text: str,
    wake_keywords: set[str],
    more_phrases: set[str],
    less_phrases: set[str],
    pending_active: bool,
    follow_mode_active: bool,
):
    t = " ".join((text or "").strip().lower().split())
    if pending_active and (t in more_phrases or t in less_phrases):
        return {"kind": "pending_block"}
    if follow_mode_active:
        return classify_follow_input(
            text=text,
            wake_keywords=wake_keywords,
            more_phrases=more_phrases,
            less_phrases=less_phrases,
            pending_active=False,
        )
    if t in more_phrases or t in less_phrases:
        return {"kind": "require_wake"}
    return {"kind": "inactive"}
