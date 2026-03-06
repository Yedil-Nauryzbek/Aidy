import time
from dataclasses import dataclass


@dataclass
class Task:
    id: int
    action_intent: str
    entities: dict
    execute_at: float


class TaskScheduler:
    def __init__(self, max_tasks: int = 5, max_delay_seconds: int = 3600):
        self.max_tasks = max_tasks
        self.max_delay_seconds = max_delay_seconds
        self._tasks: list[Task] = []
        self._next_id = 1

    def schedule(self, task: Task, delay_seconds: int) -> int | None:
        if delay_seconds <= 0 or delay_seconds > self.max_delay_seconds:
            return None
        if len(self._tasks) >= self.max_tasks:
            return None
        task.id = self._next_id
        self._next_id += 1
        task.execute_at = time.time() + delay_seconds
        self._tasks.append(task)
        return task.id

    def cancel(self, task_id: int) -> bool:
        for i, t in enumerate(self._tasks):
            if t.id == task_id:
                del self._tasks[i]
                return True
        return False

    def tick(self) -> list[Task]:
        now = time.time()
        due = [t for t in self._tasks if t.execute_at <= now]
        if due:
            self._tasks = [t for t in self._tasks if t.execute_at > now]
        return due

    def clear_all(self):
        self._tasks.clear()

    def count(self) -> int:
        return len(self._tasks)

