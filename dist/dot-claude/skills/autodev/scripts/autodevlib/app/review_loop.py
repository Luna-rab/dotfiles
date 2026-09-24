"""レビュー → ジャッジ → 修正を解消するまで回す。

上限と打ち切りの条件は `core/review_policy.py` にある。
"""

from __future__ import annotations

import concurrent.futures
from typing import Any

from ..config import stages
from ..core import review_policy
from ..ports import console, review_store, run_store
from .context import Ctx
from .stage_call import call, record_judgements


def review_round(ctx: Ctx, task: dict[str, Any], index: int, change_kind: str) -> list[str]:
    """そのラウンドのレビューステージを起こす。1 ラウンド目の 2 体は同時に走らせる。"""
    expected = review_policy.expected_reviewers(task["tier"], change_kind, index)
    label = str(index)
    review_store.init(ctx.run.review(task["id"]))

    if len(expected) == 1:
        for name in expected:
            call(ctx, stages.TABLE[name], task, label)
        return expected

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(expected)) as pool:
        futures = [pool.submit(call, ctx, stages.TABLE[name], task, label) for name in expected]
        for future in futures:
            future.result()
    return expected


def review_fix_loop(
    ctx: Ctx, task: dict[str, Any]
) -> tuple[bool, list[tuple[str, list[str]]], str]:
    """指摘が解消するまで回す。打ち切るのは 2 つの場合——ラウンドの上限に達したときと、
    **前のラウンドより未解決の指摘の総数も must-fix の数も減っていない**とき。

    must-fix だけで見ると should-fix を残したまま打ち切り、総数だけで見るとレビューステージが
    毎ラウンド新しい nit を立てるので、must-fix が減っていても打ち切られる。
    """
    st = ctx.st
    rounds: list[tuple[str, list[str]]] = []
    prev_total = prev_must = 10**9
    change_kind = "logic"

    for index in range(1, review_policy.MAX_ROUNDS + 1):
        label = str(index)
        rounds.append((label, review_round(ctx, task, index, change_kind)))

        judged = call(ctx, stages.TABLE["judge"], task, label)
        if not judged.ok:
            return False, rounds, f"ジャッジが失敗した: {judged.error}"

        # **ジャッジの報告を信じない。** 数は review.json から数える
        data = review_store.read(ctx.run.review(task["id"]))
        tally = review_policy.tally(data) if data else {"open": -1, "openMustFix": -1}
        console.info(f"  r{label}: open {tally['open']}（must-fix {tally['openMustFix']}）")
        run_store.set_task(st, task["id"], rounds=index)

        if tally["open"] == 0:
            return True, rounds, ""
        reason = review_policy.stop_reason(index, tally, prev_total, prev_must)
        if reason:
            return False, rounds, reason
        prev_total, prev_must = tally["open"], tally["openMustFix"]

        # 修正はそのラウンドのレビューで出た指摘を直すので、同じラウンドの番号で記録する
        fixed = call(ctx, stages.TABLE["fix"], task, label)
        if not fixed.ok:
            return False, rounds, f"修正ステージが失敗した: {fixed.error}"
        result = fixed.result or {}
        run_store.set_task(st, task["id"], implSession=fixed.session_id)
        change_kind = str(result.get("changeKind") or "logic")
        record_judgements(st, result)

    return False, rounds, "ラウンド上限"
