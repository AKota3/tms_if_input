"""Topic acknowledgements for the independent construction-system interface."""
from __future__ import annotations

from .system_topics_data import TopicScenario


class TopicProtocol:
    """Graph/task acknowledgements, then state reports until each task finishes."""

    def __init__(self, scenario: TopicScenario):
        if not scenario.leveling_tasks:
            raise ValueError("The topic interface needs at least one leveling task")
        self._tasks = scenario.leveling_tasks
        self._graph_pending = {task.machinery_id for task in self._tasks}
        self._task_pending = {(task.task_id, task.machinery_id) for task in self._tasks}
        if len(self._task_pending) != len(self._tasks):
            raise ValueError("Duplicate leveling task/machinery identities")
        self._reporting_pending = self._task_pending.copy()

    @property
    def phase(self) -> str:
        if self._graph_pending:
            return "graph"
        if self._task_pending:
            return "parameters"
        return "state" if self._reporting_pending else "finished"

    @property
    def pending_tasks(self):
        if self.phase != "parameters":
            return ()
        return tuple(task for task in self._tasks
                     if (task.task_id, task.machinery_id) in self._task_pending)

    @property
    def reporting_tasks(self):
        if self.phase != "state":
            return ()
        return tuple(task for task in self._tasks
                     if (task.task_id, task.machinery_id) in self._reporting_pending)

    def accept_report(self, message_id: str, task_id: str, machine_id: str) -> bool:
        if (self.phase == "graph" and message_id == "FirstSetGraph" and task_id == ""
                and machine_id in self._graph_pending):
            self._graph_pending.remove(machine_id)
            return True
        identity = (task_id, machine_id)
        if (self.phase == "parameters" and message_id == "TaskGet"
                and identity in self._task_pending):
            self._task_pending.remove(identity)
            return True
        if (self.phase == "state" and message_id == "TaskFinish"
                and identity in self._reporting_pending):
            self._reporting_pending.remove(identity)
            return True
        return False
