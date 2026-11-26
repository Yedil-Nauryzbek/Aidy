import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.last_step_action import LastStepActionManager


class TestLastStepActionManager(unittest.TestCase):
    def test_record_and_get(self):
        mgr = LastStepActionManager(ttl_seconds=12)
        mgr.record("volume_change", "UP", 3, {"device": "default"})
        last = mgr.get_if_fresh()
        self.assertIsNotNone(last)
        self.assertEqual(last.base_intent, "volume_change")
        self.assertEqual(last.direction, "UP")
        self.assertEqual(last.last_steps, 3)
        self.assertEqual(last.entities["device"], "default")

    def test_expire(self):
        mgr = LastStepActionManager(ttl_seconds=0.01)
        mgr.record("brightness_change", "DOWN", 2, {})
        time.sleep(0.02)
        self.assertIsNone(mgr.get_if_fresh())


if __name__ == "__main__":
    unittest.main()
