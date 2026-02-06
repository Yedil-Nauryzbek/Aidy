import time
import unittest
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.scheduler import TaskScheduler, Task
from aidy.delay import parse_delay_request


class TestScheduler(unittest.TestCase):
    def test_schedule_and_execute(self):
        sched = TaskScheduler(max_tasks=5, max_delay_seconds=3600)
        task = Task(id=0, action_intent="open app", entities={"app": "chrome"}, execute_at=0)
        task_id = sched.schedule(task, 1)
        self.assertIsNotNone(task_id)
        time.sleep(1.1)
        due = sched.tick()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].action_intent, "open app")

    def test_cancel(self):
        sched = TaskScheduler()
        task = Task(id=0, action_intent="open app", entities={"app": "chrome"}, execute_at=0)
        task_id = sched.schedule(task, 5)
        self.assertTrue(sched.cancel(task_id))
        self.assertEqual(len(sched.tick()), 0)

    def test_expired_time_limit(self):
        sched = TaskScheduler(max_tasks=5, max_delay_seconds=60)
        task = Task(id=0, action_intent="open app", entities={"app": "chrome"}, execute_at=0)
        self.assertIsNone(sched.schedule(task, 61))

    def test_invalid_time_format(self):
        self.assertIsNone(parse_delay_request("close browser later"))


if __name__ == "__main__":
    unittest.main()
