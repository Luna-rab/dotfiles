"""決定的なステージの中身（DOMAIN_MODEL §11.1・§11.2・§11.4）。

どれも、ドメインが決めたこと（フローの段・git 管理タスクの仕事の相手）をアダプタで実行し、起きた事実
（作った成果物・検証の結果・衝突したファイル・作った PR の番号）を `ProgramOutcome` で返すだけである。
合否と次の一手は、実行器が渡した証拠を見て Task が決める（IMPLEMENTING §1）。落ちたら例外を投げ、
実行器がエラーの証拠にする。

2 回流しても同じ結果になるように作る（DOMAIN_MODEL §9.2「決定的なステージにセッションは無い。再開とは
もう一度流すこと」）。作り済みのブランチ・worktree・PR はそのまま使う。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..adapters import globs
from ..adapters.forge import Forge
from ..adapters.git import Git
from ..domain.services.gate import ChangedFile, GateEvaluator, GateEvidence
from ..domain.services.union import UnionChecker
from ..domain.services.verify import VerifySelector
from ..domain.values import (
    ArtifactKind,
    ArtifactRef,
    BranchName,
    CutPoint,
    DecisionOrigin,
    GateReport,
    GitJob,
    PrNumber,
    StageKind,
    TaskStatus,
    UnionFileVerdict,
    UnionVerdict,
    VerifyCommand,
    VerifyResult,
)
from ..infra.files import write_atomic
from . import markers
from .stage_context import RunSetting, StageContext

_S = StageKind
_A = ArtifactKind


class VerifyRunner(Protocol):
    """ProcessRunner の受け口（検査で差し替える）。"""

    def run(self, command: VerifyCommand, cwd: str | Path) -> VerifyResult: ...


@dataclass(frozen=True)
class Tools:
    setting: RunSetting
    git: Git
    forge: Forge
    runner: VerifyRunner
    clock: Callable[[], str]


@dataclass(frozen=True)
class ProgramOutcome:
    #: 結果の JSON（StageSpec.result が宣言した欄。PR の番号・切った worktree）
    result: dict[str, Any] | None = None
    products: tuple[ArtifactRef, ...] = ()
    verify: tuple[VerifyResult, ...] = ()
    gate: GateReport | None = None
    union: UnionVerdict | None = None
    conflicts: tuple[str, ...] = ()
    #: そのブランチにだけあるコミットの数（数えたステージだけ）
    commits: int | None = None


Program = Callable[[StageContext, Tools], ProgramOutcome]


# --- 共通 ---


def own_commits(ctx: StageContext, tools: Tools, tree: Path) -> list[str]:
    """そのブランチにだけあるコミット（古い順）。タスクのブランチの根元（`Task.base_commit`）から HEAD まで。

    ほかのブランチから辿れるかでは決めない。上のタスクを切ったブランチは下のタスクのコミットを含むので、
    それを除くと 0 件になる。
    """
    if ctx.base_commit is None:
        raise RuntimeError(f"{ctx.execution} のタスクのブランチの根元（base_commit）が無い")
    return [str(c) for c in tools.git.commits_since(tree, str(ctx.base_commit))]


def _changed(tools: Tools, tree: Path, since: str) -> tuple[ChangedFile, ...]:
    """`since` から変わったファイルと、glob との照合の答え（照合の規則はアダプタ。LEDGER HK-15）。"""
    setting = tools.setting
    return tuple(
        ChangedFile(
            path,
            is_test=globs.matches_any(path, setting.test_globs),
            untested_ok=globs.matches_any(path, setting.untested_globs),
        )
        for path in tools.git.changed_files(tree, since)
    )


def _verify(ctx: StageContext, tools: Tools, tree: Path) -> tuple[VerifyResult, ...]:
    task_verify = ctx.task_spec.verify if ctx.task_spec is not None else ()
    commands = VerifySelector.select(ctx.execution.stage, task_verify, ctx.run_verify)
    return tuple(tools.runner.run(command, tree) for command in commands)


def _head(tools: Tools, tree: Path) -> str:
    return str(tools.git.head(tree))


def _start_of(tools: Tools, rev: str) -> str:
    """origin にあればそちら（手元の base は古いことがある）。無ければ手元の名前。"""
    git = tools.git
    remote = f"origin/{rev}"
    return remote if git.rev_parse(git.repo, remote) is not None else rev


def _job(ctx: StageContext) -> GitJob:
    if ctx.job is None:
        raise RuntimeError(f"{ctx.execution} は git 管理タスクの仕事の中で走っていない")
    return ctx.job


def _branch(job: GitJob) -> BranchName:
    if job.branch is None:
        raise RuntimeError(f"仕事 {job.id}（{job.kind.value}）にブランチが無い")
    return job.branch


def _overview_pr(ctx: StageContext) -> PrNumber:
    if ctx.stack.overview is None:
        raise RuntimeError("概要 PR がまだ無い")
    return ctx.stack.overview.pr


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "（なし）"


# --- 計画タスク ---


def prepare(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """起動時の指示とリポジトリごとの設定から、ブリーフを組む（コードは読まない）。"""
    setting = tools.setting
    body = markers.fill(
        markers.template("brief"),
        {
            "instruction": setting.instruction,
            "verify": _bullets([f"`{c}`" for c in setting.verify]),
            "test-paths": _bullets([f"`{g}`" for g in setting.test_globs]),
            "protected-paths": _bullets([f"`{g}`" for g in setting.protected_globs]),
            "untested-paths": _bullets([f"`{g}`" for g in setting.untested_globs]),
        },
    )
    path = setting.paths.brief
    write_atomic(path, body)
    return ProgramOutcome(products=(ArtifactRef(_A.BRIEF, setting.paths.relative(path)),))


# --- 実装タスク ---


def confirm_red(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """テストが実装の前に落ちるかを、そのタスクの verify で確かめる。"""
    head = _head(tools, ctx.tree)
    return ProgramOutcome(
        products=(ArtifactRef(_A.RED_TESTS, head),), verify=_verify(ctx, tools, ctx.tree)
    )


def gate(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """完了チェックの証拠を集め、項目ごとの合否は GateEvaluator（ドメイン）に任せる。"""
    if ctx.gate is None:
        raise RuntimeError("Gate の事実が写されていない")
    tree = ctx.tree
    head = _head(tools, tree)
    commits = own_commits(ctx, tools, tree)
    # どちらの一覧を見るか（TestGen の有無）は GateEvaluator が決めるので、在る分は両方集める
    tests = ctx.artifacts.get(_A.TESTS)
    since_tests = _changed(tools, tree, tests.at) if tests is not None else ()
    changed = _changed(tools, tree, str(ctx.base_commit)) if commits else ()
    verify = _verify(ctx, tools, tree)
    evidence = GateEvidence(
        commits=len(commits),
        open_findings=ctx.gate.open_findings,
        reviewers_expected=ctx.gate.reviewers_expected,
        reviewers_completed=ctx.gate.reviewers_completed,
        has_test_gen=ctx.gate.has_test_gen,
        changed_since_tests=since_tests,
        changed=changed,
        verify=verify,
    )
    report = GateEvaluator.evaluate(evidence).report
    return ProgramOutcome(
        products=(ArtifactRef(_A.GATED, head),),
        verify=verify,
        gate=report,
        commits=len(commits),
    )


# --- git 管理タスク: 切る・積む ---


def cut_point(tools: Tools, point: CutPoint) -> str:
    """切る元の名前。ランの base は origin にあればそちらを使う（手元の base は古いことがある）。"""
    return _start_of(tools, str(point.start)) if point.run_base else str(point.start)


def cut_branch(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """仕事が決めた切る元（`GitJob.cut_point`）から、ブランチと worktree を切る。"""
    job = _job(ctx)
    point = job.cut_point
    if point is None:
        raise RuntimeError(f"仕事 {job.id}（{job.kind.value}）に切る元が無い")
    git, paths = tools.git, tools.setting.paths
    git.prune_worktrees()
    start = cut_point(tools, point)
    branch: BranchName | None
    if point.detached:
        top = git.rev_parse(git.repo, start)
        if top is None:
            raise RuntimeError(f"切る元の {start} が無い")
        tree = paths.stack_top_tree
        git.add_detached_worktree(tree, str(top), run_dir=paths.root)
        branch = None
        base = top
    else:
        branch = _branch(job)
        tree = ctx.tree
        found = git.rev_parse(git.repo, start)
        if found is None:
            raise RuntimeError(f"切る元の {start} が無い")
        base = found
        git.create_branch(branch, start)
        git.switch_worktree(tree, branch)
    task = job.task
    if task is None:
        raise RuntimeError(f"仕事 {job.id} に worktree を使うタスクが無い")
    result: dict[str, Any] = {
        "task": task.value,
        "tree": paths.relative(tree),
        "branch": str(branch) if branch is not None else None,
        "base": str(base),
    }
    return ProgramOutcome(result=result)


def rebase(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """タスクのブランチの根元から上を、取り出したときのスタックの一番上へ載せ直す。衝突したら止めたまま返す。

    載せ直すのは、根元（`Task.base_commit`）から HEAD までのコミットだけ（`git rebase --onto <一番上>
    <根元>`）。積み直すブランチは前に積んだブランチから切るので、破棄した下のタスクのコミットも含むが、
    それは根元より下にあるので載せない（ADDENDUM §10）。載せ直すコミットが無ければ、ブランチを一番上へ
    動かさずに落ちる（タスクの仕事を黙って消さない）。途中の rebase は、流す前に実行器が取りやめている
    （`StageSpec.abandons_rebase`）。
    """
    job = _job(ctx)
    git, tree = tools.git, ctx.tree
    if job.base is None:
        raise RuntimeError("積む仕事に一番上が無い")
    onto = git.rev_parse(git.repo, str(job.base))
    if onto is None:
        raise RuntimeError(f"一番上の {job.base} が無い")
    if not own_commits(ctx, tools, tree):
        raise RuntimeError(
            f"載せ直すコミットが無い（根元 {ctx.base_commit} から HEAD まで 0 件）。"
            "ブランチは動かしていない"
        )
    # 載せ直した先は、タスクのブランチの新しい根元になる（Task.base_commit）
    result = {"onto": str(onto)}
    outcome = git.rebase(tree, str(onto), str(ctx.base_commit))
    return ProgramOutcome(result=result, conflicts=outcome.conflicts)


def check_union(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """衝突したファイルで両側の変更を残したかを UnionChecker（ドメイン）に照らし、残していれば rebase を続ける。

    続けた先のコミットでまた衝突したら、そのファイルは解いていないので、残していないものとして返す。
    rebase は止めたまま残す（取りやめるのは IntegrationFailed を受けた側）。
    """
    git, tree = tools.git, ctx.tree
    verdicts: list[UnionFileVerdict] = []
    for path in ctx.conflicts:
        sides = git.conflict_sides(tree, path)
        resolved = git.read_file(tree, path)
        verdicts.append(UnionChecker.check(path, sides.base, sides.ours, sides.theirs, resolved))
    checked = UnionVerdict(tuple(verdicts))
    if not checked.passed or not git.rebase_in_progress(tree):
        return ProgramOutcome(union=checked)
    # 続けるのは、このステージの中身（ADDENDUM §12）。両側を残したかの答えは UnionVerdict が出す
    outcome = git.rebase_continue(tree)
    verdicts += [UnionChecker.still_conflicted(p) for p in outcome.conflicts]
    return ProgramOutcome(union=UnionVerdict(tuple(verdicts)))


def verify(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """積む直前の回帰を、ラン共通の verify で確かめる。"""
    return ProgramOutcome(verify=_verify(ctx, tools, ctx.tree))


def push(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.git.push(ctx.tree, _branch(_job(ctx)))
    return ProgramOutcome()


def _read(tools: Tools, ref: ArtifactRef | None, what: str) -> str:
    if ref is None:
        raise RuntimeError(f"{what} が無い")
    return (tools.setting.paths.root / ref.at).read_text(encoding="utf-8")


def create_pr(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """タスク PR を、実装タスクが書いた本文で作る。同じブランチの PR があればそれを使う（GH-09）。"""
    job = _job(ctx)
    branch = _branch(job)
    found = tools.forge.find_pr(ctx.tree, branch)
    if found is not None:
        return ProgramOutcome(result={"pr": int(found.number.value)})
    if job.base is None or ctx.target.title is None:
        raise RuntimeError("積む仕事に一番上か、タスクの件名が無い")
    body = markers.fill(
        markers.template("task-pr-body"),
        {
            "overview-pr": f"概要 PR: #{_overview_pr(ctx)}",
            "body": _read(tools, ctx.target.pr_body, "タスク PR の本文（pr-body）"),
        },
    )
    number = tools.forge.create_pr(
        ctx.tree, base=job.base, head=branch, title=ctx.target.title, body=body
    )
    return ProgramOutcome(result={"pr": int(number.value)})


def _link(ctx: StageContext, tools: Tools, prs: Sequence[PrNumber]) -> None:
    """概要 PR から下から順に渡して、つないだ後に概要 PR の base がランの base のままか確かめる（GH-07）。"""
    paths, base = tools.setting.paths, tools.setting.base
    tools.forge.stack_link(paths.overview_tree, base, prs)
    seen = tools.forge.view_pr(paths.overview_tree, prs[0])
    if seen.base != str(base):
        raise RuntimeError(f"つないだ後の概要 PR の base が {seen.base}（ランの base は {base}）")


def stack_link(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """概要 PR から一番上（このタスクの PR）まで、全部を下から順につなぐ（link は足すだけ。GH-08）。"""
    branch = _branch(_job(ctx))
    found = tools.forge.find_pr(ctx.tree, branch)
    if found is None:
        raise RuntimeError(f"{branch} の PR が無い")
    prs = [_overview_pr(ctx), *(entry.pr for entry in ctx.stack.entries), found.number]
    _link(ctx, tools, prs)
    return ProgramOutcome(result={"pr": int(found.number.value)})


# --- git 管理タスク: 概要 PR ---

_STATUS_LABELS: Mapping[TaskStatus, str] = {
    TaskStatus.PENDING: "未着手",
    TaskStatus.RUNNING: "作業中",
    TaskStatus.ESCALATED: "回答待ち",
    TaskStatus.GATED: "積む順番待ち",
    TaskStatus.STACKING: "積んでいる",
    TaskStatus.STACKED: "積んだ",
    TaskStatus.DROPPED: "止めた",
    TaskStatus.SUPERSEDED: "引き継がれた",
    TaskStatus.DISCARDED: "破棄した",
}
_ORIGIN_LABELS: Mapping[DecisionOrigin, str] = {
    DecisionOrigin.USER: "ユーザーの回答",
    DecisionOrigin.RUN_SUPERVISOR: "ラン統括の判断",
}


def overview_values(ctx: StageContext, tools: Tools) -> dict[str, str]:
    """概要 PR のマーカーの中身。表のラベルに無い状態は、隠さずに値のまま出す（LEDGER TX-07）。"""
    facts = ctx.overview
    rows = [
        f"{row.task} {row.title} — {_STATUS_LABELS.get(row.status, row.status.value)}"
        + (f"（#{row.pr}）" if row.pr is not None else "")
        for row in facts.tasks
    ]
    waiting = [f"{task or 'ラン'}: {kind}" for task, kind in facts.waiting]
    decisions = [*(f"{d}（計画）" for d in facts.decisions)]
    decisions += [f"{note.text}（{_ORIGIN_LABELS[note.origin]}）" for note in facts.notes]
    return {
        "tasks": _bullets(rows),
        "waiting": _bullets(waiting),
        "decisions": _bullets(decisions),
        "deferrals": _bullets(list(facts.deferrals)),
        "instruction": tools.setting.instruction,
        "signature": f"autodev のラン `{tools.setting.paths.name}` が {tools.clock()} に更新した",
    }


def _overview_body(ctx: StageContext, tools: Tools) -> str:
    """保存したマーカー入りの本文から、毎回埋め直す（LEDGER TX-06）。"""
    path = tools.setting.paths.overview_body
    if not path.is_file():
        raise RuntimeError("概要 PR の本文（WriteOverview の結果）が無い")
    return markers.fill(path.read_text(encoding="utf-8"), overview_values(ctx, tools))


def _overview_title(tools: Tools) -> str:
    first = next(line.strip() for line in tools.setting.instruction.splitlines() if line.strip())
    return f"[autodev] {first}"[:120]


def create_overview_pr(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """概要ブランチを push し、概要 PR を draft で作る。base との差分が 0 だと作れないので、空のコミットを
    1 つ載せる（GH-01）。同じブランチの PR があればそれを使う。"""
    job = _job(ctx)
    branch = _branch(job)
    git, tree = tools.git, tools.setting.paths.overview_tree
    base = job.base or tools.setting.base
    if git.commit_count(tree, _start_of(tools, str(base))) == 0:
        git.commit_empty(tree, f"autodev: ラン {tools.setting.paths.name} の概要")
    git.push(tree, branch)
    found = tools.forge.find_pr(tree, branch)
    if found is not None:
        return ProgramOutcome(result={"pr": int(found.number.value)})
    number = tools.forge.create_pr(
        tree,
        base=base,
        head=branch,
        title=_overview_title(tools),
        body=_overview_body(ctx, tools),
        draft=True,
    )
    return ProgramOutcome(result={"pr": int(number.value)})


def refresh_overview(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.forge.edit_pr(
        tools.setting.paths.overview_tree, _overview_pr(ctx), body=_overview_body(ctx, tools)
    )
    return ProgramOutcome()


def ready_overview(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.forge.ready_pr(tools.setting.paths.overview_tree, _overview_pr(ctx))
    return ProgramOutcome()


# --- git 管理タスク: 破棄する ---


def _cut_index(ctx: StageContext) -> int:
    """閉じる中で一番下の、積んだ列の中の位置。閉じるものが無ければ列の長さ。"""
    cut = _job(ctx).cut_from
    entries = list(ctx.stack.entries)
    return entries.index(cut) if cut is not None and cut in entries else len(entries)


def close_prs(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """破棄したタスクの中で一番下の PR から上を、すべて閉じる（上から閉じる）。"""
    tree = tools.setting.paths.overview_tree
    for entry in reversed(ctx.stack.entries[_cut_index(ctx) :]):
        tools.forge.close_pr(tree, entry.pr)
    return ProgramOutcome()


def unstack(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """スタックを GitHub の上で解く。閉じた PR はスタックに残り、上の PR をマージできなくするため。"""
    tree = tools.setting.paths.overview_tree
    number = tools.forge.stack_number(tree, _overview_pr(ctx))
    if number is not None:
        tools.forge.unstack(tree, number)
    return ProgramOutcome()


def relink(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """残した PR（概要 PR と、閉じた所より下）でスタックを作り直す。概要 PR だけなら、次に積むタスクの
    StackLink が作り直す（スタックは 2 本以上の PR から成る）。"""
    kept = [_overview_pr(ctx), *(e.pr for e in ctx.stack.entries[: _cut_index(ctx)])]
    if len(kept) >= 2:
        _link(ctx, tools, kept)
    return ProgramOutcome()


#: 決定的なステージの種類 → 中身
def run_program(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """ステージの中身を流す。宣言（`StageSpec.abandons_rebase`）があれば、途中の rebase を先に取りやめる。"""
    if ctx.spec.abandons_rebase and (ctx.tree / ".git").exists():
        tools.git.rebase_abort(ctx.tree)
    return PROGRAMS[ctx.execution.stage](ctx, tools)


PROGRAMS: Mapping[StageKind, Program] = {
    _S.PREPARE: prepare,
    _S.CONFIRM_RED: confirm_red,
    _S.GATE: gate,
    _S.CUT_BRANCH: cut_branch,
    _S.REBASE: rebase,
    _S.CHECK_UNION: check_union,
    _S.VERIFY: verify,
    _S.PUSH: push,
    _S.CREATE_PR: create_pr,
    _S.STACK_LINK: stack_link,
    _S.REFRESH_OVERVIEW: refresh_overview,
    _S.CREATE_OVERVIEW_PR: create_overview_pr,
    _S.READY_OVERVIEW: ready_overview,
    _S.CLOSE_PRS: close_prs,
    _S.UNSTACK: unstack,
    _S.RELINK: relink,
}
