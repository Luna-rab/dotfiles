"""フロー（タスク統括が組むステージの並び）と、フローの検査。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .stages import GIT_JOB_STAGES, REVIEWER_STAGES, STAGE_SPECS, SessionScope, StepArgument
from .values import ArtifactKind, GitJob, GitJobKind, InvalidValue, StageKind, TaskKind


@dataclass(frozen=True)
class Reviewers:
    """ReviewLoop の引数。1 ラウンド目に並列で走らせるレビューと、2 ラウンド目からのレビュー。

    顔ぶれが規則に合うか（1 つ以上・レビューのステージだけ・重複なし）は、拒んだ理由を統括に返せる
    ように、作るときではなく FlowValidator が確かめる。
    """

    first: tuple[StageKind, ...]
    #: 省くと first と同じ
    later: tuple[StageKind, ...] | None = None

    def for_round(self, round: int) -> tuple[StageKind, ...]:
        if round <= 1 or self.later is None:
            return self.first
        return self.later


@dataclass(frozen=True)
class FlowStep:
    stage: StageKind
    #: 書けるのは StageSpec.arguments に REVIEWERS を持つステージ（ReviewLoop）だけ
    reviewers: Reviewers | None = None
    #: タスクの間続くセッションのステージ（Impl）を、新しいセッションでやり直す
    #: （堂々巡りのときなど。続けたセッションで落ち続けることがある）
    fresh_session: bool = False
    #: 統括からステージへの言葉（確かめてほしいこと・直し方の方針など）。実行器がプロンプトに
    #: 埋めるだけで、ドメインは中身を解釈しない
    instruction: str | None = None

    @property
    def arguments(self) -> frozenset[StepArgument]:
        """この段に書いてある引数。"""
        return frozenset({StepArgument.REVIEWERS} if self.reviewers is not None else ())


@dataclass(frozen=True)
class Flow:
    """検査を通ったフロー。版の番号は、Task が受け入れるたびに 1 つ上げる。

    git 管理タスクのフローは、Stack から取り出した仕事 1 つ（`job`）を処理する。仕事の相手（タスク・
    ブランチ・base・閉じる所）は、実行器もポリシーもここから読む。
    """

    steps: tuple[FlowStep, ...]
    version: int
    job: GitJob | None = None

    def __post_init__(self) -> None:
        if not self.steps:
            raise InvalidValue("フローが空")
        if self.version < 1:
            raise InvalidValue(f"フローの版は 1 以上: {self.version}")

    def at(self, cursor: Cursor) -> FlowStep | None:
        """cursor が指す段。フローを終えていれば None。"""
        if cursor.step < len(self.steps):
            return self.steps[cursor.step]
        return None

    def last_index_of(self, stage: StageKind, before: int) -> int | None:
        """`before` より前で、最後に `stage` を置いた段の添字（Gate の不合格で戻る ReviewLoop を探す）。"""
        for index in range(min(before, len(self.steps)) - 1, -1, -1):
            if self.steps[index].stage is stage:
                return index
        return None


@dataclass(frozen=True)
class Cursor:
    """フローのどこまで進んだか。合成ステージの中なら、中のステージとラウンドも持つ。

    合成ステージの中の次のステージを決めるのは Task で、Cursor は位置を持つだけである。
    """

    #: Flow.steps の添字。len(steps) ならフローを終えた
    step: int = 0
    #: 合成ステージの中のステージ。合成ステージの外なら None
    inner: StageKind | None = None
    #: 合成ステージの中のラウンド（1 から）。外なら 0
    round: int = 0

    def __post_init__(self) -> None:
        if self.step < 0:
            raise InvalidValue(f"cursor の段は 0 以上: {self.step}")
        if (self.inner is None) != (self.round == 0):
            raise InvalidValue("合成ステージの中にいるときだけ、ラウンドを 1 から数える")

    def next_step(self) -> Cursor:
        return Cursor(self.step + 1)

    def enter(self, inner: StageKind, round: int) -> Cursor:
        return Cursor(self.step, inner, round)

    def is_done(self, flow: Flow) -> bool:
        return self.step >= len(flow.steps)


#: タスクの種類ごとに、フローの終わりまでに作っていなければならない成果物。実装タスクだけが
#: 持つ（計画タスクと git 管理タスクはランが終わるまで続く）
REQUIRED_AT_END: Mapping[TaskKind, frozenset[ArtifactKind]] = {
    TaskKind.IMPLEMENTATION: frozenset({ArtifactKind.GATED, ArtifactKind.PR_BODY}),
}


class FlowValidator:
    """統括が返したフローを、実行の前に検査する。

    落ちた理由をすべて集めて返す。統括に 1 つずつ差し戻すと、直すたびに次の理由で落ちて往復が増える。
    """

    @staticmethod
    def check(
        steps: Iterable[FlowStep],
        task_kind: TaskKind,
        available: Iterable[ArtifactKind] = (),
        job: GitJob | None = None,
    ) -> tuple[str, ...]:
        """空なら通る。`available` はタスクがすでに持っている成果物（フローを書き直しても残る）。

        git 管理タスクのフローは、取り出した仕事を 1 つ持ち、その仕事の決まった並び（`git_job_flow`）
        だけを受ける。ほかのタスクのフローは仕事を持たない。
        """
        steps = tuple(steps)
        if not steps:
            return ("フローが空",)
        reasons: list[str] = []
        for step in steps:
            reasons += _check_placement(step, task_kind)
            reasons += _check_arguments(step)
        reasons += _check_needs(steps, set(available), task_kind)
        reasons += _check_order(steps)
        reasons += _check_job(steps, task_kind, job)
        return tuple(dict.fromkeys(reasons))


def git_job_flow(job: GitJob) -> tuple[FlowStep, ...]:
    """git 管理タスクの統括（プログラム）が、取り出した仕事から組む決まった並び。

    積み直す仕事（前に積んだブランチがある）は、新しい名前のブランチを切り直してから rebase する。
    仕上げは、概要 PR を draft から外すと決めたときだけ ReadyOverview を置く。
    """
    stages = list(GIT_JOB_STAGES[job.kind])
    if job.kind is GitJobKind.STACK and job.previous is not None:
        stages.insert(0, StageKind.CUT_BRANCH)
    if job.kind is GitJobKind.FINISH and not job.ready_overview:
        stages.remove(StageKind.READY_OVERVIEW)
    return tuple(FlowStep(stage) for stage in stages)


def planning_flow(settled_before: bool) -> tuple[FlowStep, ...]:
    """計画タスクの統括（プログラム）が組む決まった並び。

    設計が一度も確定していなければ（初回と、確定する前の再計画）Prepare → Plan から、確定した設計が
    あれば Replan から。
    """
    head = (StageKind.REPLAN,) if settled_before else (StageKind.PREPARE, StageKind.PLAN)
    return tuple(FlowStep(stage) for stage in (*head, StageKind.DESIGN_LOOP))


def _check_job(steps: tuple[FlowStep, ...], task_kind: TaskKind, job: GitJob | None) -> list[str]:
    if task_kind is not TaskKind.GIT:
        return [] if job is None else ["仕事を持つのは git 管理タスクのフローだけ"]
    if job is None:
        return ["git 管理タスクのフローは、取り出した仕事を 1 つ持つ"]
    if steps != git_job_flow(job):
        return [f"{job.kind.value} の仕事の並びではない"]
    return []


def _check_placement(step: FlowStep, task_kind: TaskKind) -> list[str]:
    spec = STAGE_SPECS[step.stage]
    if spec.parent is not None:
        return [f"{step.stage.value} は {spec.parent.value} の中のステージで、フローに直に置けない"]
    if task_kind not in spec.task_kinds:
        return [f"{step.stage.value} は {task_kind.value} のタスクに置けない"]
    return []


def _check_arguments(step: FlowStep) -> list[str]:
    reasons: list[str] = []
    spec = STAGE_SPECS[step.stage]
    name = step.stage.value
    for argument in sorted(spec.required_arguments - step.arguments, key=lambda a: a.value):
        reasons.append(f"{name} に {argument.value} が無い")
    for argument in sorted(step.arguments - spec.arguments, key=lambda a: a.value):
        accepting = ", ".join(
            sorted(kind.value for kind, other in STAGE_SPECS.items() if argument in other.arguments)
        )
        reasons.append(f"{argument.value} を書けるのは {accepting} だけ（{name} に書いてある）")
    if step.reviewers is not None and StepArgument.REVIEWERS in spec.arguments:
        reasons += _check_reviewers("first", step.reviewers.first)
        if step.reviewers.later is not None:
            reasons += _check_reviewers("later", step.reviewers.later)
    if step.fresh_session and STAGE_SPECS[step.stage].session is not SessionScope.TASK:
        reasons.append(f"{name} はタスクの間続くセッションを持たないので、fresh_session を書けない")
    if step.instruction is not None and not step.instruction.strip():
        reasons.append(f"{name} の instruction が空。渡す言葉が無ければ書かない")
    return reasons


def _check_reviewers(which: str, reviewers: tuple[StageKind, ...]) -> list[str]:
    reasons: list[str] = []
    if not reviewers:
        reasons.append(f"reviewers の {which} が空")
    allowed = ", ".join(sorted(kind.value for kind in REVIEWER_STAGES))
    for kind in reviewers:
        if kind not in REVIEWER_STAGES:
            reasons.append(f"reviewers の {which} に書けるのは {allowed} だけ（{kind.value}）")
    if len(set(reviewers)) != len(reviewers):
        reasons.append(f"reviewers の {which} に同じステージが 2 つある")
    return reasons


def _check_needs(
    steps: tuple[FlowStep, ...], have: set[ArtifactKind], task_kind: TaskKind
) -> list[str]:
    reasons: list[str] = []
    produced: set[ArtifactKind] = set()
    for step in steps:
        spec = STAGE_SPECS[step.stage]
        if missing := spec.needs - have:
            names = ", ".join(sorted(kind.value for kind in missing))
            reasons.append(f"{step.stage.value} に要る {names} が、それより前に作られていない")
        have |= spec.produces
        produced |= spec.produces
    if missing := REQUIRED_AT_END.get(task_kind, frozenset()) - produced:
        names = ", ".join(sorted(kind.value for kind in missing))
        reasons.append(f"フローの終わりまでに {names} が作られない")
    return reasons


def _check_order(steps: tuple[FlowStep, ...]) -> list[str]:
    reasons: list[str] = []
    for index, step in enumerate(steps):
        spec = STAGE_SPECS[step.stage]
        earlier_stages = {earlier.stage for earlier in steps[:index]}
        for earlier in steps[:index]:
            if earlier.stage in spec.before:
                reasons.append(f"{step.stage.value} は {earlier.stage.value} より前に置く")
        if spec.returns_to is not None and spec.returns_to not in earlier_stages:
            reasons.append(
                f"{step.stage.value} は落ちたときに {spec.returns_to.value} へ戻るので、"
                f"{spec.returns_to.value} をそれより前に置く"
            )
    return reasons
