"""`status --json` の欄（`SECTIONS`）。

どの欄も、再生した集約の値と、ドメインの問い（`Run.phase`・`Task.step_states` など）の答えを
JSON にするだけにする。「この状態ならこう見せる」という判断が要るなら、ここで分岐を書かずに、
集約に名前の付いた問いを足して呼ぶ。集約の欄をそのまま出さないのは、集約の形を
変えても HUD と `/autodev` を壊さないためである。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from ..domain.aggregate import Aggregate
from ..domain.design import Design
from ..domain.events import Event, RunStarted, StageStarted
from ..domain.questions import Questions
from ..domain.run import Run, TaskEntry
from ..domain.stack import Stack
from ..domain.task import Execution, ExecutionStatus, Task
from ..domain.value_objects.git_job import GitJob
from ..domain.value_objects.stack_entry import StackEntry
from ..domain.value_objects.stream_id import StreamId
from ..domain.value_objects.task_id import TaskId
from .eventstore import AggregateFactory, StoredEvent

A = TypeVar("A", bound=Aggregate)


@dataclass(frozen=True)
class DriverFacts:
    """イベントには残らない、driver のプロセスの事実。"""

    #: ランディレクトリの絶対パス
    directory: str
    #: `driver.lock` をほかのプロセスが握っている
    running: bool
    #: 前の driver が起こして、まだ生きている子の pid
    live_children: tuple[int, ...] = ()


@dataclass(frozen=True)
class Replayed:
    """欄を作る材料。集約と、確定したイベントの列と、走っているステージの進み具合と、driver の事実。"""

    aggregates: Mapping[StreamId, Aggregate]
    history: Sequence[tuple[StoredEvent, Event]]
    #: `ExecutionId` の文字列 → 進み具合のファイルの中身
    progress: Mapping[str, Any]
    #: 再生に使ったもの。イベントがまだ無いストリームの集約も、これで作る
    factory: AggregateFactory
    driver: DriverFacts

    def get(self, stream: StreamId, kind: type[A]) -> A:
        """型が違えば TypeError。空の集約で置き換えると、欄が黙って空になる。"""
        found = self.aggregates.get(stream)
        if found is None:
            found = self.factory(stream)
        if not isinstance(found, kind):
            raise TypeError(
                f"{stream} の集約は {type(found).__name__} で、{kind.__name__} ではない"
            )
        return found

    @property
    def run(self) -> Run:
        return self.get(StreamId.run(), Run)

    @property
    def stack(self) -> Stack:
        return self.get(StreamId.stack(), Stack)

    def task(self, task: TaskId) -> Task:
        return self.get(StreamId.task(task), Task)

    def started_at(self) -> dict[str, str]:
        """実行 → 最後に始めた（StageStarted を確定した）時刻。続きから再開したら、再開した時刻になる。"""
        return {
            str(event.execution): stored.at
            for stored, event in self.history
            if isinstance(event, StageStarted)
        }


#: 再生した集約から、外向けの形の 1 つの欄を作る
Section = Callable[[Replayed], Any]


def _value(item: Any) -> Any:
    """値オブジェクト・列挙を JSON の値に。None はそのまま。"""
    return None if item is None else getattr(item, "value", item)


def _ids(items: Any) -> list[str]:
    return sorted(str(item) for item in items)


def run_section(view: Replayed) -> dict[str, Any]:
    run = view.run
    started = next(
        ((stored, event) for stored, event in view.history if isinstance(event, RunStarted)),
        None,
    )
    return {
        "phase": run.phase.value,
        "awaiting_answer": view.get(StreamId.questions(), Questions).awaiting_answer,
        "started_at": started[0].at if started else None,
        "repository": started[1].repository.value if started else None,
        "base": started[1].base.value if started else None,
        "limit": run.limit.value,
        "resumes": run.resumes,
        "panic_cause": run.panic_cause,
        "directory": view.driver.directory,
        "driver_running": view.driver.running,
        "live_children": list(view.driver.live_children),
    }


def _job(job: GitJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "kind": job.kind.value,
        "task": _value(job.task),
        "branch": _value(job.branch),
    }


def _execution(execution: Execution, view: Replayed, started: Mapping[str, str]) -> dict[str, Any]:
    key = str(execution.id)
    return {
        "id": key,
        "stage": execution.id.stage.value,
        "round": execution.id.round,
        "attempt": execution.id.attempt,
        "flow_version": execution.flow_version,
        "step": execution.step,
        "status": execution.status.value,
        "started_at": started.get(key),
        # 実行器が消し損ねた・止めた後に残ったファイルを、走っていない実行に見せない
        "progress": view.progress.get(key) if execution.status is ExecutionStatus.RUNNING else None,
    }


def _flow(task: Task) -> dict[str, Any] | None:
    flow = task.flow
    if flow is None:
        return None
    cursor = task.cursor
    steps = []
    for index, (step, state) in enumerate(zip(flow.steps, task.step_states(), strict=True)):
        entry: dict[str, Any] = {"stage": step.stage.value, "state": state.value}
        if index == cursor.step and cursor.inner is not None:
            entry["inner"] = cursor.inner.value
            entry["round"] = cursor.round
        steps.append(entry)
    return {
        "version": flow.version,
        "steps": steps,
        "finished": task.flow_finished,
        "halted": task.halted,
        "job": _job(flow.job),
    }


def _task(entry: TaskEntry, view: Replayed, started: Mapping[str, str]) -> dict[str, Any]:
    run = view.run
    task = view.task(entry.id)
    stacked = view.stack.entry_of(entry.id)
    spec = entry.spec or task.spec
    return {
        "id": entry.id.value,
        "kind": entry.id.kind.value,
        "title": spec.title if spec is not None else None,
        "status": entry.status.value,
        "terminal": entry.status.is_terminal,
        "blocked_by": _ids(entry.blocked_by),
        "branch": _value(task.branch),
        "pr": stacked.pr.value if stacked is not None else None,
        "superseded_by": _value(entry.superseded_by),
        "takes_over": _value(entry.takes_over),
        "integration_failed": entry.integration_failed,
        "awaiting_requeue": entry.id in run.requeue,
        "flow": _flow(task),
        "executions": [_execution(e, view, started) for e in task.current_executions()],
        "escalations": [
            {
                "id": e.id.value,
                "kind": e.kind.value,
                "origin": str(e.origin) if e.origin is not None else None,
            }
            for e in task.escalations.values()
        ],
    }


def tasks_section(view: Replayed) -> list[dict[str, Any]]:
    """計画タスク・git 管理タスク（始めていれば）、実装タスク（番号の順）。"""
    run = view.run
    started = view.started_at()
    others = [run.tasks[t] for t in (TaskId.planning(), TaskId.git()) if t in run.tasks]
    return [_task(entry, view, started) for entry in (*others, *run.implementation_tasks)]


def _entry(entry: StackEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "task": entry.task.value,
        "branch": entry.branch.value,
        "pr": entry.pr.value,
        "base": entry.base.value,
    }


def stack_section(view: Replayed) -> dict[str, Any]:
    stack = view.stack
    return {
        "overview": _entry(stack.overview),
        "entries": [_entry(entry) for entry in stack.entries],
        "top": _value(stack.top),
        "queue": [_job(job) for job in stack.queue],
        "current": _job(stack.current),
        "parked": _job(stack.parked),
        "paused": stack.stacking_paused,
        "cuts_pending": stack.cuts_pending,
    }


def questions_section(view: Replayed) -> list[dict[str, Any]]:
    return [
        {"id": q.id.value, "body": q.body, "escalation": _value(q.escalation)}
        for q in view.get(StreamId.questions(), Questions).open_questions
    ]


def escalations_section(view: Replayed) -> list[dict[str, Any]]:
    """Run の側で開いているエスカレーション（ラン統括か `/autodev` が応える）。"""
    return [
        {
            "id": e.id.value,
            "kind": e.kind.value,
            "task": _value(e.task),
            "source": _value(e.source),
            "for_user": e.for_user,
            "answer_only": e.answer_only,
            "failures": e.failures,
        }
        for e in view.run.escalations.values()
    ]


def plan_section(view: Replayed) -> dict[str, Any]:
    run = view.run
    design = view.get(StreamId.design(), Design)
    proposal = design.proposal
    return {
        "planning": run.planning,
        "planned": run.planned_once,
        "applied_design": _value(run.applied_design),
        "replans_without_stack": run.replan_streak,
        "design": {
            "versions": [version.value for version in design.versions],
            "settled": _value(design.settled.design) if design.settled is not None else None,
            "proposal": None
            if proposal is None
            else {
                "version": proposal.design.value,
                "state": _value(design.proposal_state),
                "round": design.round,
                "awaiting": _value(design.awaiting),
            },
        },
    }


SECTIONS: Mapping[str, Section] = {
    "run": run_section,
    "tasks": tasks_section,
    "stack": stack_section,
    "questions": questions_section,
    "escalations": escalations_section,
    "plan": plan_section,
}
