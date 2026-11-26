import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.config import WAKE_KEYWORDS, MORE_ACTION_PHRASES, LESS_ACTION_PHRASES
from aidy.decision_core import DecisionCore
from aidy.follow_mode import FollowModeManager, resolve_follow_mode_gate
from aidy.last_step_action import LastStepActionManager


class TestFollowMode(unittest.TestCase):
    def test_more_without_wake_after_step_action(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        last_mgr = LastStepActionManager(ttl_seconds=12)
        core = DecisionCore(executor=executor, last_step_actions=last_mgr, repeat_last_steps=False)
        core.handle_step_intent("volume_up", {"magnitude_steps": 3})

        mode = FollowModeManager(ttl_seconds=10, enabled=True)
        mode.activate(last_mgr.get_if_fresh())
        gate = resolve_follow_mode_gate(
            text="more",
            wake_keywords=WAKE_KEYWORDS,
            more_phrases=MORE_ACTION_PHRASES,
            less_phrases=LESS_ACTION_PHRASES,
            pending_active=False,
            follow_mode_active=mode.is_active(),
        )
        self.assertEqual(gate["kind"], "more")

        r = core.handle_more_action()
        self.assertTrue(r["executed"])
        self.assertEqual(calls[-1][0], "volume_change")
        self.assertEqual(calls[-1][1]["direction"], "UP")
        self.assertEqual(calls[-1][1]["magnitude_steps"], 1)

    def test_wake_clears_follow_and_extracts_new_command(self):
        mode = FollowModeManager(ttl_seconds=10, enabled=True)
        last_mgr = LastStepActionManager(ttl_seconds=12)
        last_mgr.record("volume_change", "UP", 3, {})
        mode.activate(last_mgr.get_if_fresh())

        gate = resolve_follow_mode_gate(
            text="hey aidy open chrome",
            wake_keywords=WAKE_KEYWORDS,
            more_phrases=MORE_ACTION_PHRASES,
            less_phrases=LESS_ACTION_PHRASES,
            pending_active=False,
            follow_mode_active=mode.is_active(),
        )
        self.assertEqual(gate["kind"], "wake")
        self.assertEqual(gate["tail"], "open chrome")

        mode.clear()
        self.assertFalse(mode.is_active())

    def test_follow_mode_expires_then_requires_wake(self):
        mode = FollowModeManager(ttl_seconds=0.01, enabled=True)
        last_mgr = LastStepActionManager(ttl_seconds=12)
        last_mgr.record("volume_change", "UP", 3, {})
        mode.activate(last_mgr.get_if_fresh())
        time.sleep(0.02)

        gate = resolve_follow_mode_gate(
            text="more",
            wake_keywords=WAKE_KEYWORDS,
            more_phrases=MORE_ACTION_PHRASES,
            less_phrases=LESS_ACTION_PHRASES,
            pending_active=False,
            follow_mode_active=mode.is_active(),
        )
        self.assertEqual(gate["kind"], "require_wake")

    def test_pending_priority_blocks_more(self):
        gate = resolve_follow_mode_gate(
            text="more",
            wake_keywords=WAKE_KEYWORDS,
            more_phrases=MORE_ACTION_PHRASES,
            less_phrases=LESS_ACTION_PHRASES,
            pending_active=True,
            follow_mode_active=True,
        )
        self.assertEqual(gate["kind"], "pending_block")

    def test_stop_cancel_clears_follow_mode(self):
        mode = FollowModeManager(ttl_seconds=10, enabled=True)
        last_mgr = LastStepActionManager(ttl_seconds=12)
        last_mgr.record("brightness_change", "DOWN", 2, {})
        mode.activate(last_mgr.get_if_fresh())
        self.assertTrue(mode.is_active())

        gate = resolve_follow_mode_gate(
            text="cancel",
            wake_keywords=WAKE_KEYWORDS,
            more_phrases=MORE_ACTION_PHRASES,
            less_phrases=LESS_ACTION_PHRASES,
            pending_active=False,
            follow_mode_active=mode.is_active(),
        )
        self.assertEqual(gate["kind"], "cancel")
        mode.clear()
        self.assertFalse(mode.is_active())


if __name__ == "__main__":
    unittest.main()
