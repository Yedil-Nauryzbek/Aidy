"""Exclude local processing/playback time from dialogue expiry, never real timers."""
from contextlib import contextmanager
import time

from .context import ContextManager
from .followup import FollowUpManager
from .follow_mode import FollowModeManager
from .last_step_action import LastStepActionManager


@contextmanager
def pause_dialogue_expiry(assistant):
    snapshots = []
    now = time.time()
    context = getattr(assistant, "context_mgr", None)
    if isinstance(context, ContextManager) and context.is_valid():
        snapshots.append((context, "_ts", context._ts, lambda c=context: c))
    follow = getattr(assistant, "follow_up", None)
    if isinstance(follow, FollowUpManager):
        pending = follow.get_pending()
        if pending is not None:
            snapshots.append((pending, "created_at", pending.created_at, lambda f=follow: f._pending))
    steps = getattr(assistant, "last_step_actions", None)
    if isinstance(steps, LastStepActionManager):
        last = steps.get_if_fresh()
        if last is not None:
            snapshots.append((last, "timestamp", last.timestamp, lambda s=steps: s._last))
    mode = getattr(assistant, "follow_mode", None)
    if isinstance(mode, FollowModeManager) and mode.is_active():
        state = mode.state
        snapshots.append((state, "expires_at", state.expires_at, lambda m=mode: m.state))
    try:
        yield
    finally:
        elapsed = max(0.0, time.time() - now)
        for obj, field, original, current in snapshots:
            # A clear or replacement during processing must not be resurrected.
            if current() is obj and getattr(obj, field) == original:
                setattr(obj, field, original + elapsed)
