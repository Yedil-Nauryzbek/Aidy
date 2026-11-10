import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from aidy.dialogue_clock import pause_dialogue_expiry
from aidy.followup import FollowUpManager, PendingAction
from aidy.context import ContextManager
from aidy.follow_mode import FollowModeManager
from aidy.last_step_action import LastStepActionManager


class DialogueClockTests(unittest.TestCase):
    def make_assistant(self):
        a = SimpleNamespace(follow_up=FollowUpManager(), context_mgr=ContextManager(),
                            follow_mode=FollowModeManager(), last_step_actions=LastStepActionManager(),
                            timer_end_at=150)
        a.follow_up.set_pending(PendingAction("NEED_STEPS", "volume up", created_at=100))
        a.context_mgr.set_context("volume up", {})
        a.last_step_actions.record("volume", "up", 2)
        a.follow_mode.activate(a.last_step_actions.get_if_fresh())
        return a

    def test_slow_inference_preserves_dialogue_but_not_real_timer(self):
        with patch("time.time", return_value=100) as clock:
            a = self.make_assistant()
            with pause_dialogue_expiry(a):
                clock.return_value = 130
            self.assertIsNotNone(a.follow_up.get_pending())
            self.assertIsNotNone(a.context_mgr.get_context())
            self.assertIsNotNone(a.last_step_actions.get_if_fresh())
            self.assertTrue(a.follow_mode.is_active())
            self.assertEqual(a.timer_end_at, 150)
            clock.return_value = 145
            self.assertIsNone(a.follow_up.get_pending())

    def test_cleared_or_expired_state_is_not_revived(self):
        with patch("time.time", return_value=100) as clock:
            a = self.make_assistant()
            with pause_dialogue_expiry(a):
                a.follow_up.clear_pending()
                a.context_mgr.clear_context()
                a.follow_mode.clear()
                a.last_step_actions.clear()
                clock.return_value = 130
            self.assertIsNone(a.follow_up.get_pending())
            self.assertIsNone(a.context_mgr.get_context())
            self.assertIsNone(a.last_step_actions.get_if_fresh())
            self.assertFalse(a.follow_mode.is_active())


if __name__ == "__main__":
    unittest.main()
