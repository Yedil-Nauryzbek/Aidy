import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.action_history import ActionHistory, ActionRecord


class TestActionHistory(unittest.TestCase):
    def test_undo_single(self):
        h = ActionHistory(max_actions=20, chain_gap_seconds=5)
        rec = ActionRecord(id=0, action_intent="open app", entities={"app": "chrome"}, inverse_action={"intent": "close app", "entities": {"app": "chrome"}}, timestamp=0, chain_id=0)
        h.push(rec)
        last = h.get_last()
        self.assertIsNotNone(last)
        self.assertEqual(last.action_intent, "open app")

    def test_chain_undo(self):
        h = ActionHistory(max_actions=20, chain_gap_seconds=5)
        r1 = ActionRecord(id=0, action_intent="volume up", entities={"steps": 3}, inverse_action={"intent": "volume down", "entities": {"steps": 3}}, timestamp=0, chain_id=0)
        h.push(r1)
        r2 = ActionRecord(id=0, action_intent="brightness up", entities={}, inverse_action={"intent": "brightness down", "entities": {}}, timestamp=0, chain_id=0)
        h.push(r2)
        chain = h.get_chain(h.get_last().chain_id)
        self.assertEqual(len(chain), 2)

    def test_no_history(self):
        h = ActionHistory(max_actions=20, chain_gap_seconds=5)
        self.assertIsNone(h.get_last())

    def test_action_without_inverse(self):
        h = ActionHistory(max_actions=20, chain_gap_seconds=5)
        rec = ActionRecord(id=0, action_intent="screenshot", entities={}, inverse_action=None, timestamp=0, chain_id=0)
        h.push(rec)
        self.assertIsNone(h.get_last().inverse_action)

    def test_chain_break_on_timeout(self):
        h = ActionHistory(max_actions=20, chain_gap_seconds=0.01)
        r1 = ActionRecord(id=0, action_intent="volume up", entities={"steps": 1}, inverse_action={"intent": "volume down", "entities": {"steps": 1}}, timestamp=0, chain_id=0)
        h.push(r1)
        time.sleep(0.02)
        r2 = ActionRecord(id=0, action_intent="volume up", entities={"steps": 1}, inverse_action={"intent": "volume down", "entities": {"steps": 1}}, timestamp=0, chain_id=0)
        h.push(r2)
        self.assertNotEqual(h.get_chain(1), h.get_chain(2))


if __name__ == "__main__":
    unittest.main()
