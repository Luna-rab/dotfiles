"""レビュー → ジャッジ → 直す手を、未解決の指摘が 0 件になるまで回す。

**ラウンドに上限は無い。** 修正を受けても直らない指摘が出たら（停滞）、ジャッジに原因を分類させ、
原因ごとの手に移る。停滞の条件と分類から手を選ぶ規則は `core/review_policy.py` にある。

| 手 | すること |
| --- | --- |
| fix | 修正ステージが指摘を直す |
| tests | テスト作成ステージがテストを直し、修正ステージが続ける |
| approach | 実装を新しいセッションでやり直す（前の方針に引きずられないように） |
| replan | `NeedsReplan` を投げる。`drive()` が再計画ステージを呼ぶ |
| ask | `Waiting` を投げる。`drive()` が人に聞いて回答待ちで終わる |
"""

from __future__ import annotations

import concurrent.futures
from typing import Any

from ..config import stages
from ..core import review_policy
from ..ports import console, review_store
from .build import make_tests, settle_conflicts, write_code
from .context import Ctx, NeedsReplan, Waiting
from .stage_call import call_or_wait


def review_round(ctx: Ctx, task: dict[str, Any], index: int, change_kind: str) -> list[str]:
    """そのラウンドのレビューステージを起こす。1 ラウンド目の 2 体は同時に走らせる。

    エラーで終わったレビューステージは 1 回呼び直し、それでも落ちたら人に聞く。走り終えない
    レビューを残すと、完了チェック④が落ち続ける（修正ステージには直せない）。
    """
    expected = review_policy.expected_reviewers(task["tier"], change_kind, index)
    label = str(index)
    review_store.init(ctx.run.review(task["id"]))

    if len(expected) == 1:
        for name in expected:
            call_or_wait(ctx, stages.TABLE[name], task, label)
        return expected

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(expected)) as pool:
        futures = [
            pool.submit(call_or_wait, ctx, stages.TABLE[name], task, label) for name in expected
        ]
        for future in futures:
            future.result()
    return expected


def stale_note(stale_ids: list[str]) -> str:
    """ジャッジに渡す、停滞している指摘の一覧。"""
    if not stale_ids:
        return ""
    return (
        "## 停滞している指摘\n\n"
        f"次の指摘は修正を {review_policy.STALE_AFTER} 回受けても未解決だった: {', '.join(stale_ids)}。\n"
        "確かめてまだ直っていなければ、原因を分類して結果の `escalation` に書く。"
    )


def open_items(ctx: Ctx, task: dict[str, Any]) -> list[dict[str, Any]]:
    data = review_store.read(ctx.run.review(task["id"])) or {"items": {}}
    return review_store.items(data)


def review_fix_loop(ctx: Ctx, task: dict[str, Any]) -> None:
    """未解決が 0 件になったら戻る。進めなくなったら `NeedsReplan` か `Waiting` を投げる。

    ラウンドの番号は `task["rounds"]` から続ける。回答待ちや再計画から戻ってきても、
    前のラウンドのログを上書きしない。
    """
    path = ctx.run.review(task["id"])
    change_kind = str(task.get("changeKind") or "logic")
    while True:
        index = int(task.get("rounds") or 0) + 1
        label = str(index)
        expected = review_round(ctx, task, index, change_kind)
        task["rounds"] = index
        task.setdefault("reviewRounds", []).append([label, expected])
        ctx.save()

        before = review_store.read(path) or {"items": {}}
        stale_ids = review_policy.stale(task.get("fixAttempts") or {}, before)
        judged = call_or_wait(ctx, stages.TABLE["judge"], task, label, extra=stale_note(stale_ids))
        # **ジャッジの報告を信じない。** 数は review.json から数える
        data = review_store.read(path) or {"items": {}}
        tally = review_policy.tally(data)
        console.info(f"  r{label}: 未解決 {tally['open']}（must-fix {tally['openMustFix']}）")
        if tally["open"] == 0:
            ctx.save()
            return

        escalation = (judged.result or {}).get("escalation") or None
        stale_ids = review_policy.stale(task.get("fixAttempts") or {}, data)
        action = review_policy.route(escalation, stale_ids)
        reason = str((escalation or {}).get("reason") or "").strip()
        items = [str(i) for i in (escalation or {}).get("items") or stale_ids]
        if action != "fix":
            console.info(f"  停滞: {action}（{reason or ', '.join(items)}）")

        if action == "replan":
            raise NeedsReplan(
                task["id"],
                reason or f"修正を {review_policy.STALE_AFTER} 回受けても解決しない指摘がある",
                items,
            )
        if action == "ask":
            asked = [str(q) for q in (escalation or {}).get("questions") or []] or [reason]
            raise Waiting(
                task["id"],
                [
                    {"id": f"{task['id']}-r{label}-q{n}", "question": q}
                    for n, q in enumerate(asked, start=1)
                ],
            )

        def fix(extra: str = "", label: str = label) -> dict[str, Any]:
            return write_code(ctx, task, "fix", label, extra)

        if action == "tests":
            make_tests(
                ctx,
                task,
                label,
                extra=f"## ジャッジの分類: テストが誤っている\n\n{reason}\n\n"
                f"対象の指摘: {', '.join(items)}\n\n"
                "受入条件と照らして確かめ、正しければテストを直して commit する。",
            )
            # 直す手が変わったので、停滞の数え直しを始める
            task["fixAttempts"] = {}
            result = fix(
                "## テストを直した\n\nジャッジの分類を受けてテスト作成ステージがテストを直した。"
                "直したテストと未解決の指摘に合わせて直す。"
            )
        elif action == "approach":
            task["fixAttempts"] = {}
            task["implSession"] = None
            listed = "\n".join(
                f"- {i['id']}（{i['rating']}、{i['location']}）: {i['review']}"
                for i in open_items(ctx, task)
            )
            result = write_code(
                ctx,
                task,
                "impl",
                label,
                "## 実装をやり直す\n\n"
                f"これまでの方針では次の指摘が直らなかった（ジャッジの分類: {reason}）。\n\n"
                f"{listed}\n\n今あるコミットと `<レビュー>` の経緯を読み、同じ方針を繰り返さずに直す。",
            )
        else:
            result = fix()

        change_kind = str(result.get("changeKind") or "logic")
        task["changeKind"] = change_kind
        settle_conflicts(ctx, task, result, label, fix)
        after = review_store.read(path) or {"items": {}}
        task["fixAttempts"] = review_policy.bump_attempts(task.get("fixAttempts") or {}, after)
        ctx.save()
