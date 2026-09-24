"""完了の根拠を実物で確かめる 完了チェック。

**ステージの報告を完了の根拠にしない。** 実行中のランに「完了」通知が誤って発火し、PR 番号・
マージ・失敗談まで含む精巧な捏造レポートが届いたことがある。だから driver は毎回ここを通す。

    ①stage-finished   ステージが正常終了した（終了コードと result イベントの subtype）
    ②commits          親ブランチからのコミットが 1 件以上ある
    ③reviews-settled  review.json が実在し、open が 0 件
    ④reviewer-count   走り終えたレビューステージが、ステージの一覧から期待される体数に届いている
    ⑤tests-untouched  テスト作成ステージの後、テストファイルの差分が空
    ⑥verify           検証コマンド一式が緑（**driver が自分で流す**）

**ここは証拠を受け取って合否を決めるだけである。** git もコマンドも呼ばない——集めるのは
`ports/evidence.py` で、⑥を流すのもそちらである。

**証拠の形（`Evidence` と `VerifyResult`）もここに置く。** 判断する側が形の出所を持つので、
`judge()` がフィールド名の打ち間違いを型検査で拾える。組み立てるのは `ports/evidence.py` で、
`ports → core` の向きは層の規則が認めている。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import globs, review_policy


@dataclass(frozen=True)
class VerifyResult:
    """検証コマンド 1 本の結果。落ちた理由の文面を組むのは `check_verify()`。"""

    command: str
    ok: bool
    code: int
    out: str
    err: str


@dataclass(frozen=True)
class Evidence:
    """完了チェックが見る事実。"""

    stage_ok: bool
    stage_detail: str
    #: 完了チェック②が数えた区間。合否の文言にそのまま出る
    parent: str
    branch: str
    #: 親ブランチからのコミット数。数えられなければ -1
    commits: int
    #: テスト作成ステージのコミット
    tests_since: str | None
    changed_since_tests: tuple[str, ...]
    review_path: str
    #: review.json の中身。無ければ None
    review: dict[str, Any] | None
    reviewers_by_round: dict[str, tuple[str, ...]]
    adversarial_ran: bool
    review_runs: int
    #: 流したコマンドと結果。流していなければ空
    verify: tuple[VerifyResult, ...] = ()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok

    def lines(self) -> list[str]:
        return [f"{'ok  ' if c.ok else 'NG  '}{c.name}: {c.detail}" for c in self.checks]


def check_stage(report: Report, stage_name: str, ok: bool, detail: str) -> bool:
    return report.add("stage-finished", ok, detail or f"{stage_name} は正常に終わった")


def check_commits(report: Report, evidence: Evidence) -> bool:
    count = evidence.commits
    if count < 0:
        return report.add(
            "commits", False, f"{evidence.parent}..{evidence.branch} を数えられなかった"
        )
    return report.add(
        "commits", count >= 1, f"{evidence.parent}..{evidence.branch} のコミットは {count} 件"
    )


def check_reviews(report: Report, evidence: Evidence) -> bool:
    if evidence.review is None:
        # ファイルの実在が「レビューが走った」証拠である。無いのは、レビューステージが起動
        # していないか、渡したパスが違っている
        return report.add("reviews-settled", False, f"review.json が無い: {evidence.review_path}")
    tally = review_policy.tally(evidence.review)
    return report.add(
        "reviews-settled",
        tally["open"] == 0,
        f"未解決 {tally['open']} 件（must-fix {tally['openMustFix']} 件）"
        f" / 解決済み {tally['closed']} / 却下 {tally['rejected']}",
    )


def check_reviewer_count(
    report: Report,
    evidence: Evidence,
    tier: str,
    rounds: list[tuple[str, list[str]]],
) -> bool:
    """各ラウンドで期待した体数が走り終えたか、と、敵対的が 1 度でも走ったかを見る。

    ラウンド単位の体数だけでは「standard なのに敵対的が 1 度も走っていない」を表せない
    （2 ラウンド目で解消したタスクは期待も実測も 1 になる）。タスク全体でも見る。
    """
    if evidence.review is None:
        return report.add("reviewer-count", False, "review.json が無い")

    short: list[str] = []
    for label, expected in rounds:
        seen = evidence.reviewers_by_round.get(label, ())
        missing = [r for r in expected if r not in seen]
        if missing:
            short.append(f"r{label}: {', '.join(missing)} が走っていない")

    if tier == "standard" and not evidence.adversarial_ran:
        short.append("敵対的レビューが 1 度も走っていない（tier=standard）")

    return report.add(
        "reviewer-count",
        not short,
        "; ".join(short) if short else f"走り終えたレビュー {evidence.review_runs} 回",
    )


def check_tests_untouched(report: Report, evidence: Evidence, *, test_globs: list[str]) -> bool:
    """**テスト作成ステージが commit した後、テストが動いていないこと。**

    比べる基準は `tests_since`（テスト作成ステージのコミット）で、`parent` ではない。テスト作成ステージは
    タスクのブランチに commit するので、`parent..branch` の差分にはテストが必ず含まれる
    ——そこを基準にすると、実装ステージがテストに触っていないランでも落ちる。

    `tests_since` が無いときは判定できないので落とす（テスト作成ステージが走っていないランである）。
    """
    if not evidence.tests_since:
        return report.add(
            "tests-untouched", False, "テスト作成ステージのコミットが記録されていない"
        )
    after_tests = list(evidence.changed_since_tests)
    touched = globs.pick(after_tests, test_globs)
    return report.add(
        "tests-untouched",
        not touched,
        "テスト作成ステージの後にテストが動いた: " + ", ".join(touched[:5])
        if touched
        else f"テスト作成ステージの後の変更 {len(after_tests)} 件にテストは無い",
    )


def check_verify(report: Report, evidence: Evidence) -> bool:
    """検証コマンド一式の結果を読む。**流すのは `ports/evidence.py` の `run_verify()`。**

    流した結果が空なのは、検証コマンドが 1 本も無いときである（落ちたコマンドがあれば
    そこで打ち切るので、結果は 1 件以上ある）。
    """
    if not evidence.verify:
        return report.add("verify", False, "検証コマンドが 1 つも設定されていない")
    for got in evidence.verify:
        if not got.ok:
            tail = (got.err or got.out).strip().splitlines()[-12:]
            return report.add(
                "verify",
                False,
                f"落ちた: {got.command}（終了コード {got.code}）\n    " + "\n    ".join(tail),
            )
    return report.add("verify", True, f"{len(evidence.verify)} 本すべて緑")


#: ⑥をまだ流していない印。**`needs_verify()` でしか読まない。** これが残った Report で
#: タスクをスタックに追加してはいけない
VERIFY_PENDING = "⑥はまだ流していない"
#: ①〜⑤のどれかが落ちたので⑥を流さなかった印。⑥そのものの失敗ではない
VERIFY_SKIPPED = "①〜⑤が通っていないので流していない"


def judge(
    evidence: Evidence,
    *,
    tier: str,
    rounds: list[tuple[str, list[str]]],
    test_globs: list[str],
    verify_ran: bool = False,
) -> Report:
    """完了チェックをまとめて通す。**引数だけで動く。**

    ⑥は時間がかかるので①〜⑤が通ってから流す。流すのは呼び出し側なので、2 度呼ばれる。

    1. `verify_ran=False`（既定）——①〜⑤だけを見る。⑥は `VERIFY_PENDING` になる
    2. `needs_verify()` が真なら呼び出し側が `run_verify()` を流し、`verify_ran=True` で呼び直す

    **`verify_ran` の既定を False にしてあるので、2 度目を忘れた呼び出し側は
    `report.ok` が False のままになる。** 「検証コマンドが 1 つも設定されていない」という
    別の理由に化けない。
    """
    report = Report()
    check_stage(report, "ステージ", evidence.stage_ok, evidence.stage_detail)
    check_commits(report, evidence)
    check_reviews(report, evidence)
    check_reviewer_count(report, evidence, tier, rounds)
    check_tests_untouched(report, evidence, test_globs=test_globs)
    if not report.ok:
        report.add("verify", False, VERIFY_SKIPPED)
    elif verify_ran:
        check_verify(report, evidence)
    else:
        report.add("verify", False, VERIFY_PENDING)
    return report


def needs_verify(report: Report) -> bool:
    """①〜⑤が通って⑥だけが未評価か。**`run_verify()` を流すのはこれが真のときだけ。**"""
    return [c.detail for c in report.checks if c.name == "verify"] == [VERIFY_PENDING]


def as_dict(report: Report) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks],
    }
