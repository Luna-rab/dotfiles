"""ステージと統括に渡すプロンプトの組み立て。

- **指示書の本文はプロンプトに入れない。** 指示書のパスと、指示書の `<名前>`（プレースホルダ）が
  このランで何を指すかの表だけを渡し、指示書はモデルが自分で読む
- **何を渡すかは指示書の「入力」の表が決める。** ここは指示書が挙げたプレースホルダを、下の
  `SOURCES` の出どころから埋めるだけで、どれを渡すかを自分で選ばない。指示書に足したプレースホルダが
  `SOURCES` に無ければ、組み立ての時点で落ちる（検査でも確かめる）
- 埋める値は、イベントストア（再生した集約と、確定したイベントの列）と成果物の在りか
  （`ArtifactRef`）から読む。実行のときにしか分からない値（ステージの cwd）は、呼ぶ側が渡す
- 1 行で書ける値は表に、複数行の値（タスクの中身・指摘の台帳など）は表の後ろの節に置く
- 破ると取り返しがつかない決まりだけを `--append-system-prompt` に置く。指示書の要約は
  置かない（出どころが 2 つになる）
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from ..domain import codec
from ..domain.aggregate import Aggregate
from ..domain.design import Design
from ..domain.events import (
    AnswerRecorded,
    DesignRevisionStarted,
    EscalationAnswered,
    EscalationRaised,
    Event,
    ReplanRequested,
)
from ..domain.flow import FlowStep
from ..domain.review import ReviewLedger
from ..domain.run import Run
from ..domain.stack import Stack
from ..domain.stages import STAGE_SPECS
from ..domain.supervision import Notice, Supervisor, Wake, wake_for
from ..domain.task import Task
from ..domain.value_objects.artifact_kind import ArtifactKind
from ..domain.value_objects.artifact_ref import ArtifactRef
from ..domain.value_objects.design_version import DesignVersion
from ..domain.value_objects.event_id import EventId
from ..domain.value_objects.finding_status import FindingStatus
from ..domain.value_objects.stage_kind import StageKind
from ..domain.value_objects.stream_id import StreamId
from ..domain.value_objects.task_id import TaskId
from ..infra.paths import RunPaths
from .mainloop import Delivery
from .stage_context import StageContext, StagePrompt

_A = ArtifactKind


@cache
def skill_root() -> Path:
    """スキルの根。階層を数えて上らず、`SKILL.md` を探して決める。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "SKILL.md").is_file():
            return parent
    raise FileNotFoundError(f"SKILL.md が見つからない（{here} から上）")


def asset_name(kind: StageKind) -> str:
    """ステージの種類の名前から、指示書とスキーマのファイル名（拡張子を除く）。`TestGen` → `test-gen`。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", kind.value).lower()


def contract_path(name: str) -> Path:
    return skill_root() / "contracts" / f"{name}.md"


def schema_text(name: str) -> str:
    """`claude --json-schema` に渡す本文（パスではない）。"""
    return (skill_root() / "schemas" / f"{name}.json").read_text(encoding="utf-8")


#: 指示書のプレースホルダ。`<!--`・`<<<<<<<` は含めない（test_contracts と同じ形）
_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9])<([^<>\s!/`\-=][^<>\n`]*)>")


@cache
def contract_inputs(name: str) -> tuple[str, ...]:
    """指示書の `## 入力` の表の 1 列目に書いたプレースホルダ（表の順）。"""
    text = contract_path(name).read_text(encoding="utf-8")
    section = text.split("## 入力", 1)[1].split("\n## ", 1)[0]
    found: list[str] = []
    for line in section.splitlines():
        if line.startswith("| `<"):
            found += [m for m in _PLACEHOLDER.findall(line.split("|")[1]) if m not in found]
    return tuple(found)


# =====================================================================================
# 埋める値の出どころ
# =====================================================================================


@dataclass(frozen=True)
class Block:
    """複数行の値。表の後ろの節に置く。"""

    text: str


Value = str | Block


@dataclass(frozen=True)
class Sources:
    """プレースホルダを埋めるのに読むもの。メインループのスレッドで作り、その場で読み切る。"""

    paths: RunPaths
    aggregates: Mapping[StreamId, Aggregate]
    history: Sequence[Delivery]
    #: 誰のための値か（ステージのタスク・タスク統括のタスク。ラン統括なら計画タスク）
    task: TaskId
    stage: StageKind | None = None
    step: FlowStep | None = None
    #: ステージの cwd（実行器が決める）
    tree: Path | None = None
    #: 統括を起こした知らせ（`render_notice`）
    notice: str | None = None
    status_command: str = ""

    # --- 集約 ---

    def get(self, stream: StreamId, cls: type[Any]) -> Any:
        found = self.aggregates.get(stream)
        return found if isinstance(found, cls) else cls(stream)

    @property
    def run(self) -> Run:
        return self.get(StreamId.run(), Run)

    def task_of(self, task: TaskId) -> Task:
        return self.get(StreamId.task(task), Task)

    @property
    def subject(self) -> Task:
        return self.task_of(self.task)

    @property
    def target(self) -> TaskId:
        """ステージが扱うタスク（`Task.working_on`）。"""
        return self.subject.working_on

    @property
    def stack(self) -> Stack:
        return self.get(StreamId.stack(), Stack)

    @property
    def design(self) -> Design:
        return self.get(StreamId.design(), Design)

    def ledger(self, stream: StreamId) -> ReviewLedger:
        return self.get(stream, ReviewLedger)

    def events(self, cls: type[Event]) -> list[tuple[EventId, Any]]:
        return [(d.event_id, d.event) for d in self.history if isinstance(d.event, cls)]

    # --- 成果物 ---

    def artifact(self, kind: ArtifactKind) -> ArtifactRef | None:
        return self.subject.artifacts.get(kind)

    def file(self, ref: ArtifactRef | None) -> str:
        """ランディレクトリからのパスの成果物の、絶対パス。無ければ空。"""
        return "" if ref is None else str(self.paths.root / ref.at)


def _json(value: Any) -> Block:
    return Block("```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _camel_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_camel(str(k)): _camel_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camel_keys(v) for v in value]
    return value


def _required(value: Path | None, name: str) -> str:
    if value is None:
        raise ValueError(f"<{name}> を埋める値を、呼ぶ側が渡していない")
    return str(value)


def _design(s: Sources) -> str:
    """いま扱う設計の版のファイル。提案を読むステージ（`StageSpec.reads_proposal`）には、確定前の提案の版。"""
    reads_proposal = s.stage is not None and STAGE_SPECS[s.stage].reads_proposal
    ref = s.artifact(_A.PROPOSAL if reads_proposal else _A.DESIGN)
    return "" if ref is None else str(s.paths.design(DesignVersion(int(ref.at))))


def _tests_commit(s: Sources) -> str:
    ref = s.artifact(_A.TESTS)
    return "" if ref is None else ref.at


def _task_spec(s: Sources, task: TaskId) -> Block:
    entry = s.run.tasks.get(task)
    spec = s.task_of(task).spec or (entry.spec if entry is not None else None)
    body: dict[str, Any] = {"id": task.value}
    if spec is not None:
        body |= _camel_keys(codec.to_json(spec))
    if entry is not None:
        body["blockedBy"] = sorted(t.value for t in entry.blocked_by)
    return _json(body)


def _notes(s: Sources, task: TaskId) -> Value:
    notes = s.task_of(task).notes
    if not notes:
        return ""
    return _json([_camel_keys(codec.to_json(note)) for note in notes])


def _findings(ledger: ReviewLedger, keep: Callable[[Any], bool] = lambda _: True) -> Value:
    found = [
        {
            "id": f.id.value,
            "rating": f.rating.value,
            "body": f.body,
            "location": f.location.value if f.location is not None else None,
            "status": f.status.value,
            "fixes": f.fixes,
            "comments": list(f.comments),
        }
        for f in ledger.findings.values()
        if keep(f)
    ]
    return _json(found) if found else ""


def _current_design_finding(ledger: ReviewLedger) -> Callable[[Any], bool]:
    """設計の台帳で、今の提案に付いた指摘（台帳が数えるもの）。"""
    since = ledger.since

    def keep(finding: Any) -> bool:
        return (
            since is not None and finding.design is not None and finding.design.value >= since.value
        )

    return keep


def _base_commit(s: Sources) -> str:
    """タスクのブランチの根元（`Task.base_commit`）。"""
    base = s.task_of(s.target).base_commit
    return "" if base is None else base.value


def _code_tree(s: Sources) -> str:
    """コードを読む所（`Task.code_tree`。計画タスクなら、初回は概要、再計画は stack-top）。"""
    tree = s.subject.code_tree
    if tree is None:
        raise ValueError(f"{s.task} のために切った worktree がまだ無い（<コードの置き場>）")
    return str(s.paths.root / tree)


def _conflicts(s: Sources) -> Value:
    task = s.subject
    files = [*task.conflict_files, *(task.conflicts or ())]
    return _json(files) if files else ""


def _predecessor(s: Sources) -> str:
    ref = s.artifact(_A.CONFLICTS)
    if ref is None:
        return ""
    before = TaskId(ref.at)
    branch = s.task_of(before).branch
    return f"{before}（ブランチ {branch}）" if branch is not None else before.value


def _replan_reason(s: Sources) -> Value:
    found = s.events(ReplanRequested)
    if not found:
        return ""
    _, last = found[-1]
    body: dict[str, Any] = {"reason": last.reason}
    if last.trigger is not None:
        raised = dict(s.events(EscalationRaised)).get(last.trigger)
        body["trigger"] = last.trigger.value
        if raised is not None:
            body["triggerPointers"] = _camel_keys(codec.to_json(raised.pointers))
            body["triggerReason"] = raised.reason
    return _json(body)


def _task_list(s: Sources) -> Value:
    prs = {entry.task: entry.pr.value for entry in s.stack.entries}
    listed = []
    for entry in s.run.implementation_tasks:
        task = entry.id
        ledger = s.ledger(StreamId.review(task))
        listed.append(
            {
                "id": task.value,
                "title": entry.spec.title if entry.spec is not None else "",
                "status": entry.status.value,
                "blockedBy": sorted(t.value for t in entry.blocked_by),
                "pr": prs.get(task),
                "openFindings": [f.id.value for f in ledger.open_findings],
                "spec": _camel_keys(codec.to_json(entry.spec)) if entry.spec else None,
            }
        )
    return _json(listed)


def _pr_bodies(s: Sources) -> Value:
    paths = [s.file(s.task_of(entry.task).artifacts.get(_A.PR_BODY)) for entry in s.stack.entries]
    paths = [p for p in paths if p]
    return _json(paths) if paths else ""


def _stack_top(s: Sources) -> str:
    stack = s.stack
    if stack.entries:
        return stack.entries[-1].branch.value
    return stack.overview_branch.value if stack.overview_branch is not None else ""


def _revise_answer(s: Sources) -> str:
    found = s.events(DesignRevisionStarted)
    return (found[-1][1].answer or "") if found else ""


def _answer_log(s: Sources) -> Value:
    """ランの中の回答の全部。ユーザーの回答（質問の id 付き）と、ラン統括が自分で答えたもの。"""
    found: list[dict[str, Any]] = []
    for eid, event in s.events(AnswerRecorded):
        entry: dict[str, Any] = {
            "origin": "user",
            "question": event.question.value,
            "escalation": event.escalation.value if event.escalation else None,
            "answer": event.answer,
            "event": eid.value,
        }
        if event.escalation_closed:
            entry["escalationClosed"] = True
        found.append(entry)
    for eid, event in s.events(EscalationAnswered):
        if event.question is None:
            found.append(
                {
                    "origin": "run-supervisor",
                    "escalation": event.escalation.value,
                    "answer": event.answer,
                    "event": eid.value,
                }
            )
    return _json(found) if found else ""


#: プレースホルダ → 埋める値の出どころ。指示書の「入力」の表に足したら、ここにも足す
SOURCES: Mapping[str, Callable[[Sources], Value]] = {
    # 場所
    "worktree": lambda s: _required(s.tree, "worktree"),
    "コードの置き場": _code_tree,
    "ランディレクトリ": lambda s: str(s.paths.root),
    "autodev": lambda s: str(skill_root() / "scripts" / "autodev.py"),
    "これまでの結果": lambda s: str(s.paths.task_results(s.target)),
    "状態を読むコマンド": lambda s: s.status_command,
    # ラン共通の成果物
    "ブリーフ": lambda s: s.file(s.artifact(_A.BRIEF)),
    "コードマップ": lambda s: s.file(s.artifact(_A.CODEMAP)),
    "設計": _design,
    "設計の置き場": lambda s: str(s.paths.designs),
    # タスク
    "タスク": lambda s: _task_spec(s, s.target),
    "決めたこと": lambda s: _notes(s, s.task),
    "基準のコミット": _base_commit,
    "テストのコミット": _tests_commit,
    "期待値待ちのテスト": lambda s: s.file(s.artifact(_A.AWAITING_EXPECTATIONS)),
    "衝突したファイル": _conflicts,
    "引き継ぎ元": _predecessor,
    "統括からの言葉": lambda s: (s.step.instruction or "") if s.step is not None else "",
    "指摘": lambda s: _findings(s.ledger(StreamId.review(s.target))),
    # 設計の段
    "設計の指摘": lambda s: _findings(
        s.ledger(StreamId.design_review()),
        _current_design_finding(s.ledger(StreamId.design_review())),
    ),
    "却下した論点": lambda s: _findings(
        s.ledger(StreamId.design_review()), lambda f: f.status is FindingStatus.REJECTED
    ),
    "提案のタスク": lambda s: _json(
        [_camel_keys(codec.to_json(t)) for t in s.design.proposal.tasks]
        if s.design.proposal is not None
        else []
    ),
    "回答": _revise_answer,
    "再計画の理由": _replan_reason,
    # ランとスタック
    "タスクの一覧": _task_list,
    "タスク PR の本文": _pr_bodies,
    "ランの base": lambda s: s.stack.run_base.value if s.stack.run_base is not None else "",
    "スタックの一番上": _stack_top,
    "概要 PR の雛形": lambda s: str(skill_root() / "templates" / "overview-pr-body.md"),
    # 統括
    "通知": lambda s: Block(s.notice or ""),
    "回答の記録": _answer_log,
}


# =====================================================================================
# 組み立て
# =====================================================================================

_INLINE_UNSAFE = re.compile(r"[\n|`]")


def render(name: str, sources: Sources, role: str) -> str:
    """指示書 `name` の「入力」の表のプレースホルダを全部埋めた、利用者のメッセージ。"""
    rows: list[str] = []
    sections: list[str] = []
    for placeholder in contract_inputs(name):
        source = SOURCES.get(placeholder)
        if source is None:
            raise KeyError(f"指示書 {name} の <{placeholder}> を埋める出どころが無い（SOURCES）")
        value = source(sources)
        if isinstance(value, str) and not _INLINE_UNSAFE.search(value):
            rows.append(f"| `<{placeholder}>` | {f'`{value}`' if value else '（空）'} |")
            continue
        text = value.text if isinstance(value, Block) else value
        rows.append(f"| `<{placeholder}>` | 下の節「`<{placeholder}>`」 |")
        sections.append(f"## `<{placeholder}>`\n\n{text.strip() or '（空）'}")
    head = [
        f"あなたは autodev の {role} である。",
        "",
        f"指示書 `{contract_path(name)}` を最初に読み、そのとおりに務める。"
        "指示書の中の `<名前>` は、下の表の値を指す。",
        "",
        "| 表記 | 値 |",
        "| --- | --- |",
        *rows,
    ]
    return "\n".join(head) + ("\n\n" + "\n\n".join(sections) if sections else "") + "\n"


def system_rules(role: str) -> str:
    """`--append-system-prompt` に置く、破ると取り返しがつかない決まり。"""
    return "\n".join(
        [
            f"あなたは autodev の {role} である。進め方を決めるのは driver で、あなたは自分の役だけを"
            "務める。次のステージを自分で呼ばない。",
            "",
            "## 何があっても守ること",
            "",
            "- `gh` を呼ばない。`git push` をしない（GitHub を触るのは driver だけ）。",
            "- 指示書が書いてよいとした所の外へ書かない（フックが止める）。",
            "- 終わりに結果を StructuredOutput ツールで 1 つだけ返す。形はそのツールの入力スキーマにある。",
        ]
    )


def _notice_body(
    notice: Notice | None,
    replay: EventId | None,
    delivery: Delivery,
    find: Callable[[EventId], Delivery | None],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "notice": notice.value if notice is not None else None,
        "id": delivery.event_id.value,
        "event": type(delivery.event).__name__,
        **_camel_keys(codec.to_json(delivery.event)),
    }
    if replay is not None and (original := find(replay)) is not None:
        # 起こし直す元の知らせを、そのときと同じ形で入れ子にする。元も起こし直しなら、最初の知らせ
        # までたどって 1 段だけ載せる（起こし直すたびに入れ子が深くならない。途中の回答は載せない）
        inner = wake_for(original.event, original.event_id)
        seen = {original.event_id}
        while (
            inner is not None
            and inner.replay is not None
            and inner.replay not in seen
            and (earlier := find(inner.replay)) is not None
        ):
            original, seen = earlier, seen | {earlier.event_id}
            inner = wake_for(original.event, original.event_id)
        body["retry"] = _notice_body(
            inner.notice if inner is not None else None, None, original, find
        )
    return body


def render_notice(
    wake: Wake,
    delivery: Delivery,
    find: Callable[[EventId], Delivery | None] = lambda _: None,
) -> str:
    """統括を起こしたイベントを、知らせの JSON にする。イベントの欄をそのまま載せる。

    起こし直す（`Wake.replay`）なら、元の知らせを `find` で確定したイベントの列から引いて `retry` に載せる。
    """
    return _json(_notice_body(wake.notice, wake.replay, delivery, find)).text


def render_rejection(reason: str) -> str:
    """返した判断が拒まれたときの知らせ（同じセッションに差し戻す）。"""
    body = {"notice": Notice.DECISION_REJECTED.value, "reason": reason}
    return (
        "## `<通知>`\n\n"
        + _json(body).text
        + "\n\n返した判断は実行していない。理由を読んで、直した判断を 1 つ返す。\n"
    )


class Prompts:
    """ステージ（実行器が呼ぶ `StagePrompts`）と統括のプロンプト。メインループのスレッドで呼ぶ。"""

    def __init__(
        self,
        paths: RunPaths,
        history: Callable[[], Sequence[Delivery]],
        status_command: str = "",
    ) -> None:
        self._paths = paths
        self._history = history
        self._status = status_command

    def _sources(self, aggregates: Mapping[StreamId, Aggregate], task: TaskId) -> Sources:
        return Sources(self._paths, aggregates, self._history(), task, status_command=self._status)

    def prompt(
        self, context: StageContext, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        execution = context.execution
        role = f"{execution.stage.value} のステージ"
        sources = replace(
            self._sources(aggregates, execution.task),
            stage=execution.stage,
            step=context.step,
            tree=context.tree,
        )
        text = render(asset_name(execution.stage), sources, role)
        return StagePrompt(text, system_rules(role))

    def continuation(self, context: StageContext) -> str:
        """interrupt で止めた実行を `--resume` で続けるときの、短い続きの指示（実測が無い）。"""
        name = asset_name(context.execution.stage)
        return (
            "driver の都合で、このステージを途中で止めた。止まったところから、指示書 "
            f"`{contract_path(name)}` のとおりに続け、終わりに結果を StructuredOutput ツールで返す。"
        )

    def wake(
        self, wake: Wake, delivery: Delivery, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        """`wake` で統括を起こすプロンプト（知らせは `delivery`）。"""
        history = self._history()

        def find(event: EventId) -> Delivery | None:
            return next((d for d in history if d.event_id == event), None)

        return self.supervisor(wake.supervisor, render_notice(wake, delivery, find), aggregates)

    def supervisor(
        self, supervisor: Supervisor, notice: str, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        if supervisor.task is None:
            name, role, task = "supervisor-run", "ラン統括", TaskId.planning()
        else:
            name, role, task = (
                "supervisor-task",
                f"タスク統括（{supervisor.task}）",
                supervisor.task,
            )
        sources = replace(self._sources(aggregates, task), notice=notice)
        return StagePrompt(render(name, sources, role), system_rules(role))
