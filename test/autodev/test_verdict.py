"""完了の根拠を決める 完了チェック（`core/verdict.py`）。

**ここが緩むと、ステージの報告がそのまま「完了」になる。** 逆に締まりすぎると、正しく
終わったランがスタックに追加されない。どちらも人間が気づくまで分からないので、完了チェックそれぞれについて
通る例と落ちる例を置く。

**`detail` の文言も字面で確かめる。** 文言は PR とログに出る「なぜ落ちたか」の唯一の説明で、
静かに変わると落ちた理由を誰も読めなくなる。

証拠は `Evidence` を手で組んで渡す。git も claude も起動しない。
"""

from __future__ import annotations

from typing import Any

from autodevlib.core import verdict
from autodevlib.core.verdict import Evidence, Report, VerifyResult

PARENT = "main"
BRANCH = "stack/demo--task-1"
REVIEW_PATH = "/state/autodev/demo/tasks/task1/review.json"
TEST_GLOBS = ["**/test_*.py", "**/tests/**"]
#: 1 ラウンド目で解消した standard のタスク。`judge()` の `rounds` にそのまま渡す
ROUNDS: list[tuple[str, list[str]]] = [("1", ["review:normal", "review:adversarial"])]

#: 解消済みの review.json。open が 0 件なので完了チェック③が通る
SETTLED: dict[str, Any] = {
    "nextId": 3,
    "items": {
        "1": {"status": "closed", "rating": "must-fix"},
        "2": {"status": "rejected", "rating": "nit"},
    },
    "runs": [
        {"round": "1", "reviewer": "review:normal"},
        {"round": "1", "reviewer": "review:adversarial"},
    ],
}


def evidence(**over: Any) -> Evidence:
    """①〜⑤が通る証拠。落としたい完了チェックの分だけ差し替える。"""
    base: dict[str, Any] = {
        "stage_ok": True,
        "stage_detail": "",
        "parent": PARENT,
        "branch": BRANCH,
        "commits": 2,
        "tests_since": "abc1234",
        "changed_since_tests": ("src/a.py",),
        "review_path": REVIEW_PATH,
        "review": SETTLED,
        "reviewers_by_round": {"1": ("review:normal", "review:adversarial")},
        "adversarial_ran": True,
        "review_runs": 2,
        "verify": (),
    }
    return Evidence(**{**base, **over})


def judge(
    evi: Evidence, *, tier: str = "standard", verify_ran: bool = False, **over: Any
) -> Report:
    rounds: list[tuple[str, list[str]]] = over.pop("rounds", ROUNDS)
    globs: list[str] = over.pop("test_globs", TEST_GLOBS)
    assert not over, over
    return verdict.judge(evi, tier=tier, rounds=rounds, test_globs=globs, verify_ran=verify_ran)


def got(report: Report, name: str) -> tuple[bool, str]:
    """完了チェック 1 つの合否と文言。名前が変わればここで落ちる。"""
    hit = [c for c in report.checks if c.name == name]
    assert len(hit) == 1, [c.name for c in report.checks]
    return hit[0].ok, hit[0].detail


# --- ①stage-finished --------------------------------------------------------


def test_ステージが正常に終われば通る():
    ok, detail = got(judge(evidence()), "stage-finished")
    assert ok
    assert detail == "ステージ は正常に終わった"


def test_ステージの文言があればそれをそのまま出す():
    evi = evidence(stage_ok=True, stage_detail="impl は結果を返した")
    assert got(judge(evi), "stage-finished") == (True, "impl は結果を返した")


def test_ステージが落ちれば落ちる():
    evi = evidence(stage_ok=False, stage_detail="終了コード 1")
    assert got(judge(evi), "stage-finished") == (False, "終了コード 1")


# --- ②commits ---------------------------------------------------------------


def test_コミットが1件以上あれば通る():
    ok, detail = got(judge(evidence(commits=1)), "commits")
    assert ok
    assert detail == "main..stack/demo--task-1 のコミットは 1 件"


def test_コミットが0件なら落ちる():
    ok, detail = got(judge(evidence(commits=0)), "commits")
    assert not ok
    assert detail == "main..stack/demo--task-1 のコミットは 0 件"


def test_コミットを数えられなければ落ちる():
    """`-1` は「親ブランチが無い」など git が答えられなかった印。0 件と区別して出す。"""
    ok, detail = got(judge(evidence(commits=-1)), "commits")
    assert not ok
    assert detail == "main..stack/demo--task-1 を数えられなかった"


# --- ③reviews-settled -------------------------------------------------------


def test_openが0件なら通る():
    ok, detail = got(judge(evidence()), "reviews-settled")
    assert ok
    assert detail == "未解決 0 件（must-fix 0 件） / 解決済み 1 / 却下 1"


def test_openが残っていれば落ちる():
    review = {
        "items": {
            "1": {"status": "open", "rating": "must-fix"},
            "2": {"status": "open", "rating": "nit"},
            "3": {"status": "closed", "rating": "should-fix"},
        },
        "runs": SETTLED["runs"],
    }
    ok, detail = got(judge(evidence(review=review)), "reviews-settled")
    assert not ok
    assert detail == "未解決 2 件（must-fix 1 件） / 解決済み 1 / 却下 0"


def test_reviewjsonが無ければ落ちる():
    """ファイルの実在が「レビューが走った」証拠である。無いランを通してはいけない。"""
    ok, detail = got(judge(evidence(review=None)), "reviews-settled")
    assert not ok
    assert detail == f"review.json が無い: {REVIEW_PATH}"


# --- ④reviewer-count --------------------------------------------------------


def test_期待した体数が走り終えていれば通る():
    ok, detail = got(judge(evidence()), "reviewer-count")
    assert ok
    assert detail == "走り終えたレビュー 2 回"


def test_走っていないレビューステージがいれば落ちる():
    evi = evidence(reviewers_by_round={"1": ("review:normal",)})
    ok, detail = got(judge(evi), "reviewer-count")
    assert not ok
    assert detail == "r1: review:adversarial が走っていない"


def test_standardで敵対的が1度も走っていなければ落ちる():
    """2 ラウンド目で解消したタスクはラウンド単位の体数が 1 で足りてしまう。タスク全体でも見る。"""
    evi = evidence(
        reviewers_by_round={"2": ("review:normal",)},
        adversarial_ran=False,
        review_runs=1,
    )
    ok, detail = got(judge(evi, rounds=[("2", ["review:normal"])]), "reviewer-count")
    assert not ok
    assert detail == "敵対的レビューが 1 度も走っていない（tier=standard）"


def test_lightは敵対的が走っていなくても通る():
    evi = evidence(
        reviewers_by_round={"1": ("review:normal",)},
        adversarial_ran=False,
        review_runs=1,
    )
    report = judge(evi, tier="light", rounds=[("1", ["review:normal"])])
    assert got(report, "reviewer-count") == (True, "走り終えたレビュー 1 回")


def test_体数を数える前にreviewjsonの実在を見る():
    ok, detail = got(judge(evidence(review=None)), "reviewer-count")
    assert not ok
    assert detail == "review.json が無い"


# --- ⑤tests-untouched -------------------------------------------------------


def test_テスト作成ステージの後にテストが動いていなければ通る():
    evi = evidence(changed_since_tests=("src/a.py", "docs/b.md"))
    ok, detail = got(judge(evi), "tests-untouched")
    assert ok
    assert detail == "テスト作成ステージの後の変更 2 件にテストは無い"


def test_テスト作成ステージの後にテストが動けば落ちる():
    evi = evidence(changed_since_tests=("src/a.py", "tests/test_a.py"))
    ok, detail = got(judge(evi), "tests-untouched")
    assert not ok
    assert detail == "テスト作成ステージの後にテストが動いた: tests/test_a.py"


def test_テスト作成ステージのコミットが無ければ落ちる():
    """比べる基準は `tests_since` で、`parent` ではない。

    `parent..branch` を基準にすると、テスト作成ステージが commit したテストが必ず差分に入るので、
    実装ステージがテストに触っていないランでも落ちる。基準のコミットが無いときは判定できないので落とす。
    """
    ok, detail = got(judge(evidence(tests_since=None)), "tests-untouched")
    assert not ok
    assert detail == "テスト作成ステージのコミットが記録されていない"


def test_基準のコミットが無ければ差分を見ずに落ちる():
    """`tests_since` が無いランは、差分にテストが入っていなくても通さない。"""
    evi = evidence(tests_since=None, changed_since_tests=())
    assert got(judge(evi), "tests-untouched")[0] is False


# --- ⑥verify と 2 ステージ呼び出し ------------------------------------------------


def test_1度目の判定では完了チェック6をまだ流していない():
    report = judge(evidence())
    assert verdict.needs_verify(report) is True
    assert report.ok is False
    assert got(report, "verify") == (False, "⑥はまだ流していない")


def test_2度目の判定で完了チェック6が緑なら全部通る():
    evi = evidence(
        verify=(
            VerifyResult(command="uv run ruff check .", ok=True, code=0, out="", err=""),
            VerifyResult(command="uv run pytest -q", ok=True, code=0, out="", err=""),
        )
    )
    report = judge(evi, verify_ran=True)
    assert report.ok is True
    assert got(report, "verify") == (True, "2 本すべて緑")
    assert verdict.needs_verify(report) is False


def test_完了チェック6が落ちれば落ちたコマンドと末尾を出す():
    evi = evidence(
        verify=(
            VerifyResult(command="uv run ruff check .", ok=True, code=0, out="", err=""),
            VerifyResult(
                command="uv run pytest -q",
                ok=False,
                code=1,
                out="",
                err="E   assert 1 == 2\n1 failed",
            ),
        )
    )
    ok, detail = got(judge(evi, verify_ran=True), "verify")
    assert not ok
    assert detail == (
        "落ちた: uv run pytest -q（終了コード 1）\n    E   assert 1 == 2\n    1 failed"
    )


def test_完了チェック6が落ちれば標準エラーの末尾12行だけを出す():
    """落ちた理由を人間が読むのはこの 12 行である。`out` ではなく `err` を出す。"""
    evi = evidence(
        verify=(
            VerifyResult(
                command="uv run pytest -q",
                ok=False,
                code=1,
                out="stdout は出さない",
                err="\n".join(f"line{i}" for i in range(1, 21)),
            ),
        )
    )
    ok, detail = got(judge(evi, verify_ran=True), "verify")
    assert not ok
    assert "line9" in detail
    assert "line8" not in detail
    assert "stdout は出さない" not in detail


def test_完了チェック1から5が落ちていれば完了チェック6を流さない():
    """⑥は時間がかかる。①〜⑤が落ちたランで流すのは無駄である。"""
    report = judge(evidence(commits=0))
    assert verdict.needs_verify(report) is False
    assert got(report, "verify") == (False, "①〜⑤が通っていないので流していない")
    assert got(judge(evidence(commits=0), verify_ran=True), "verify") == (
        False,
        "①〜⑤が通っていないので流していない",
    )


def test_2度目を忘れると落ちたままになる():
    """`verify_ran` の既定が False なので、呼び忘れた側は `report.ok` が False で止まる。

    ここが「検証コマンドが 1 つも設定されていない」に化けると、コマンドを設定したランで
    設定漏れを疑わせる。流し忘れと設定漏れは別の文言で出す。
    """
    evi = evidence(
        verify=(VerifyResult(command="uv run pytest -q", ok=True, code=0, out="", err=""),)
    )
    # ここだけヘルパを通さない。`verify_ran` を明示で渡すと既定の値を確かめられない
    report = verdict.judge(evi, tier="standard", rounds=ROUNDS, test_globs=TEST_GLOBS)
    assert report.ok is False
    ok, detail = got(report, "verify")
    assert not ok
    assert detail == "⑥はまだ流していない"


def test_検証コマンドが0本なら落ちる():
    report = judge(evidence(verify=()), verify_ran=True)
    assert got(report, "verify") == (False, "検証コマンドが 1 つも設定されていない")


# --- 報告の形 ----------------------------------------------------------------


def test_完了チェックが決まった順で並ぶ():
    """名前と順番は state.json とログに出る。増減したらここで落ちる。"""
    report = judge(evidence(), verify_ran=True)
    assert [c.name for c in report.checks] == [
        "stage-finished",
        "commits",
        "reviews-settled",
        "reviewer-count",
        "tests-untouched",
        "verify",
    ]


def test_落ちた完了チェックだけを取り出せる():
    report = judge(evidence(commits=0, stage_ok=False, stage_detail="終了コード 1"))
    assert [c.name for c in report.failed] == ["stage-finished", "commits", "verify"]


def test_辞書にするとokと全件が入る():
    data = verdict.as_dict(judge(evidence()))
    assert data["ok"] is False  # ⑥をまだ流していない
    assert data["checks"][1] == {
        "name": "commits",
        "ok": True,
        "detail": "main..stack/demo--task-1 のコミットは 2 件",
    }


def test_行にするとok印が付く():
    lines = judge(evidence(commits=0)).lines()
    assert lines[1] == "NG  commits: main..stack/demo--task-1 のコミットは 0 件"
    assert lines[0].startswith("ok  stage-finished: ")
