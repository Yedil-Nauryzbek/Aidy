import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.decision_core import DecisionCore, STEP_REQUIRED, parse_numeric_input, extract_steps_value
from aidy.followup import FollowUpManager, PENDING_NEED_STEPS
from aidy.last_step_action import LastStepActionManager


class TestDecisionCore(unittest.TestCase):
    def test_numeric_variants_parsing(self):
        self.assertEqual(parse_numeric_input("won"), 1)
        self.assertEqual(parse_numeric_input("to"), 2)
        self.assertEqual(parse_numeric_input("tree"), 3)
        self.assertEqual(parse_numeric_input("for"), 4)
        self.assertEqual(parse_numeric_input("fife"), 5)
        self.assertEqual(parse_numeric_input("sex"), 6)
        self.assertEqual(parse_numeric_input("sevan"), 7)
        self.assertEqual(parse_numeric_input("ate"), 8)
        self.assertEqual(parse_numeric_input("nyne"), 9)
        self.assertEqual(parse_numeric_input("tin"), 10)
        self.assertEqual(parse_numeric_input("number three"), 3)
        self.assertEqual(extract_steps_value("increase volume number four"), 4)

    def test_increase_volume_then_numeric(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor, follow_up=FollowUpManager(ttl_seconds=8.0))
        r1 = core.handle_step_intent("volume_up", {})
        self.assertTrue(r1["handled"])
        self.assertFalse(r1["executed"])
        self.assertEqual(r1["prompt"], "How much?")
        pending = core.follow_up.get_pending()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.pending_type, PENDING_NEED_STEPS)
        self.assertEqual(pending.base_intent, "volume_change")
        self.assertEqual(pending.direction, "UP")

        r2 = core.handle_numeric_input(3)
        self.assertTrue(r2["handled"])
        self.assertTrue(r2["executed"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "volume_change")
        self.assertEqual(calls[0][1]["direction"], "UP")
        self.assertEqual(calls[0][1]["magnitude_steps"], 3)
        self.assertIsNone(core.follow_up.get_pending())

    def test_numeric_without_pending(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor)
        r = core.handle_numeric_input(3)
        self.assertTrue(r["handled"])
        self.assertFalse(r["executed"])
        self.assertEqual(r["prompt"], "Not now.")
        self.assertEqual(len(calls), 0)

    def test_pending_timeout(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor, follow_up=FollowUpManager(ttl_seconds=0.01))
        core.handle_step_intent("brightness_down", {})
        time.sleep(0.02)
        r = core.handle_numeric_input(4)
        self.assertFalse(r["executed"])
        self.assertEqual(r["prompt"], "Not now.")
        self.assertIsNone(core.follow_up.get_pending())
        self.assertEqual(len(calls), 0)

    def test_two_invalid_responses_cancel(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor)
        core.handle_step_intent("volume_down", {})
        r1 = core.handle_non_numeric_during_pending()
        self.assertEqual(r1["prompt"], "Say a number from one to ten.")
        r2 = core.handle_non_numeric_during_pending()
        self.assertEqual(r2["prompt"], "Cancelled.")
        self.assertIsNone(core.follow_up.get_pending())
        self.assertEqual(len(calls), 0)

    def test_registry_extensible(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        custom_registry = dict(STEP_REQUIRED)
        custom_registry["scroll_up"] = {"base": "scroll_change", "direction": "UP"}
        core = DecisionCore(executor=executor, step_required=custom_registry)

        r1 = core.handle_step_intent("scroll_up", {})
        self.assertEqual(r1["prompt"], "How much?")
        r2 = core.handle_numeric_input(2)
        self.assertTrue(r2["executed"])
        self.assertEqual(calls[0][0], "scroll_change")
        self.assertEqual(calls[0][1]["direction"], "UP")
        self.assertEqual(calls[0][1]["magnitude_steps"], 2)

    def test_more_action_default_single_step(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor)
        core.handle_step_intent("volume_up", {"magnitude_steps": 3})
        r = core.handle_more_action()
        self.assertTrue(r["executed"])
        self.assertEqual(calls[-1][0], "volume_change")
        self.assertEqual(calls[-1][1]["direction"], "UP")
        self.assertEqual(calls[-1][1]["magnitude_steps"], 1)

    def test_more_action_repeat_last_steps_enabled(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor, repeat_last_steps=True)
        core.handle_step_intent("volume_up", {"magnitude_steps": 3})
        r = core.handle_more_action()
        self.assertTrue(r["executed"])
        self.assertEqual(calls[-1][0], "volume_change")
        self.assertEqual(calls[-1][1]["direction"], "UP")
        self.assertEqual(calls[-1][1]["magnitude_steps"], 3)

    def test_more_action_ttl_expired(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        manager = LastStepActionManager(ttl_seconds=12)
        core = DecisionCore(executor=executor, last_step_actions=manager)
        core.handle_step_intent("volume_up", {"magnitude_steps": 3})
        manager._last.timestamp -= 13
        r = core.handle_more_action()
        self.assertFalse(r["executed"])
        self.assertIn(r["prompt"], {"Nothing to repeat.", "Not now.", "Too late."})
        self.assertEqual(len(calls), 1)

    def test_more_action_blocked_by_pending(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor)
        core.handle_step_intent("volume_up", {})
        r = core.handle_more_action()
        self.assertFalse(r["executed"])
        self.assertEqual(r["prompt"], "Say a number.")
        self.assertEqual(len(calls), 0)

    def test_less_action_after_volume_up(self):
        calls = []

        def executor(intent: str, entities: dict) -> bool:
            calls.append((intent, entities))
            return True

        core = DecisionCore(executor=executor)
        core.handle_step_intent("volume_up", {"magnitude_steps": 3})
        r = core.handle_less_action()
        self.assertTrue(r["executed"])
        self.assertEqual(calls[-1][0], "volume_change")
        self.assertEqual(calls[-1][1]["direction"], "DOWN")
        self.assertEqual(calls[-1][1]["magnitude_steps"], 1)


if __name__ == "__main__":
    unittest.main()
