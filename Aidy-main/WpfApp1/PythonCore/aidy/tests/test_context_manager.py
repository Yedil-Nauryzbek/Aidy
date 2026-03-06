import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.context import ContextManager, should_merge_context


class TestContextManager(unittest.TestCase):
    def test_valid_continuation(self):
        ctx = ContextManager(ttl_seconds=5)
        ctx.set_context("open app", {"app": "chrome"})
        self.assertTrue(ctx.is_valid())
        data = ctx.get_context()
        self.assertEqual(data["last_intent"], "open app")
        self.assertEqual(data["last_entities"]["app"], "chrome")
        self.assertTrue(should_merge_context("open app", "open app"))

    def test_expired_context(self):
        ctx = ContextManager(ttl_seconds=0.01)
        ctx.set_context("open app", {"app": "chrome"})
        time.sleep(0.02)
        self.assertFalse(ctx.is_valid())
        self.assertIsNone(ctx.get_context())

    def test_conflict_intents(self):
        self.assertFalse(should_merge_context("open app", "close app"))

    def test_attempt_to_change_intent(self):
        self.assertFalse(should_merge_context("open app", "volume up"))


if __name__ == "__main__":
    unittest.main()
