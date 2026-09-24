"""コードとテストを書くステージ（テスト作成・実装・修正）を呼ぶ。

**テストを直せるのはテスト作成ステージだけである。** 実装・修正ステージに直させると、テストを
通すためにテストを緩める経路ができる。実装・修正ステージがテストの矛盾を報告したら、どの
ラウンドでもテスト作成ステージに受入条件と照らして確かめさせ、正しければ直させる。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import stages
from ..ports import repo, run_store
from .context import Ctx, Waiting
from .stage_call import call_or_wait, record_judgements


def questions_of(
    task: dict[str, Any], stage_code: str, result: dict[str, Any]
) -> list[dict[str, str]]:
    """ステージが「受入条件が曖昧」と返したときの、人への質問。"""
    asked = [str(q) for q in result.get("questions") or []] or [
        "受入条件が一意に定まらない（ステージが疑問点を書かなかった）"
    ]
    return [
        {"id": f"{task['id']}-{stage_code}-q{index}", "question": question}
        for index, question in enumerate(asked, start=1)
    ]


def make_tests(ctx: Ctx, task: dict[str, Any], label: str, extra: str = "") -> bool:
    """テスト作成ステージ。**このステージだけテストへ書ける**（driver が `AUTODEV_ALLOW_TESTS` を渡す）。

    戻り値はテストのコミットが増えたか。テストの矛盾の報告を誤りと判断したステージは commit しない。
    """
    before = repo.head_sha(ctx.run.tree)
    got = call_or_wait(ctx, stages.TABLE["testgen"], task, label, extra=extra)
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, "testgen", result))
    after = repo.head_sha(ctx.run.tree)
    # **テストを書いた時点のコミットを控える。** 完了チェック⑤はここから先でテストが動いていないかを
    # 見る（テスト作成ステージはタスクのブランチに commit するので、parent から見ると必ず差分が出る）
    run_store.set_task(ctx.st, task["id"], testsAt=after)
    task["testNotes"] = str(result.get("notes") or "")
    return before != after


def write_code(
    ctx: Ctx, task: dict[str, Any], stage_code: str, label: str, extra: str = ""
) -> dict[str, Any]:
    """実装（`impl`）か修正（`fix`）を呼ぶ。テストファイルは read-only にして走らせ、終わったら必ず戻す。

    フックはランの頭で書いた `guard.json` を全ステージに渡してあるので、ここで出し入れするのは
    ファイルの書き込み権だけである（フックの裏をかかれても書けないようにする二重の栓）。
    """
    repo.lock_tests(ctx.run.tree, ctx.st["testGlobs"])
    try:
        got = call_or_wait(ctx, stages.TABLE[stage_code], task, label, extra=extra)
    finally:
        repo.unlock_tests(ctx.run.tree, ctx.st["testGlobs"])
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, stage_code, result))
    record_judgements(ctx.st, result)
    return result


def test_conflict(result: dict[str, Any]) -> str | None:
    conflict = result.get("testConflict")
    # 無いときに `null` ではなく文字列の "null" を返すステージがある。報告として扱うとテスト作成ステージを無駄に呼び直す
    if not isinstance(conflict, str) or conflict.strip().lower() in ("", "null", "none"):
        return None
    return conflict.strip()


def settle_conflicts(
    ctx: Ctx,
    task: dict[str, Any],
    result: dict[str, Any],
    label: str,
    redo: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """実装・修正ステージの「テストが仕様と矛盾している」という報告を片付ける。

    テスト作成ステージが報告を正しいと判断してテストを直したら、`redo` で同じ手を呼び直す。
    誤りと判断してテストを変えなかったら、その判断を渡して 1 回だけ呼び直し、以後の報告は
    レビューとジャッジに任せる（同じ報告でテスト作成ステージを呼び続けない）。
    """
    conflict = test_conflict(result)
    while conflict:
        extra = (
            "## 実装・修正ステージからの報告\n\n"
            f"{conflict}\n\n"
            "この報告を受入条件と照らして検証し、**正しければテストを直して commit する**。"
            "誤っていれば直さず、理由を結果の `notes` に書く。"
        )
        if not make_tests(ctx, task, label, extra=extra):
            return redo(
                "## テスト作成ステージの判断\n\n"
                "報告したテストの矛盾を、テスト作成ステージは誤りと判断してテストを変えなかった。"
                f"理由: {task.get('testNotes') or '（記録なし）'}\n\nテストに合わせて実装する。"
            )
        result = redo(
            "## テストを直した\n\n報告を受けてテスト作成ステージがテストを直した。直したテストが通るようにする。"
        )
        conflict = test_conflict(result)
    return result


def build(ctx: Ctx, task: dict[str, Any]) -> None:
    """最初の実装。テスト作成と実装はレビュー前なのでラウンド 0 で記録する。"""
    result = write_code(ctx, task, "impl", "0")
    settle_conflicts(
        ctx, task, result, "0", lambda extra: write_code(ctx, task, "impl", "0", extra)
    )
