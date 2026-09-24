"""再計画。止まったタスクの割り方を、再計画ステージに直させる。

再計画ステージが選べる手は 4 つで、どれを使うかはステージが決める。

- 止まったタスクの範囲・受入条件を広げて、同じタスクで直す
- 指摘を同じランの後ろのタスクへ移す（移した先のタスクのレビューで解決を確かめる）
- 未着手のタスクを組み直す（分ける・まとめる・順番を変える）
- 止まったタスクを取り下げて割り直す

**スタック済みのタスクには触らない。** 変えると上に積んだブランチの積み替えと PR の書き換えが要る。

**再計画はラン全体で `MAX_REPLANS` 回まで。** 超えたら人に聞く。ラウンドに上限が無いので、
これが止まらずに回り続けるのを防ぐ最後の歯止めである。
"""

from __future__ import annotations

from typing import Any

from ..config import stages
from ..core import task_order
from ..ports import console, review_store, run_store
from .build import questions_of
from .context import Ctx, NeedsReplan, Waiting
from .stage_call import call_or_wait, record_judgements

MAX_REPLANS = 2


def brief(ctx: Ctx, task: dict[str, Any], need: NeedsReplan) -> str:
    """再計画ステージに渡す材料。止まった理由・未解決の指摘・変えてよいタスクと変えてはいけないタスク。"""
    data = review_store.read(ctx.run.review(task["id"])) or {"items": {}}
    open_items = review_store.items(data)
    lines = [
        "## 止まったタスク",
        "",
        f"`{task['id']}`（{task['subject']}）。ジャッジの分類: {need.reason}",
        "",
        "### 未解決の指摘",
        "",
    ]
    lines += [
        f"- {i['id']}（{i['rating']}、{i['location']}）: {i['review']}"
        + ("  ← 停滞" if i["id"] in need.items else "")
        for i in open_items
    ] or ["- （なし）"]
    lines += ["", "## ほかのタスク", ""]
    for item in ctx.st["tasks"]:
        if item["id"] == task["id"]:
            continue
        mark = "変えてはいけない" if item["status"] == "stacked" else "組み直してよい"
        if item["status"] in ("stacked", "pending"):
            lines.append(
                f"- `{item['id']}`（{item['status']}、{mark}）: {item['subject']} / 受入条件: {item['acceptance']}"
            )
    return "\n".join(lines)


def replan(ctx: Ctx, need: NeedsReplan) -> None:
    st, run = ctx.st, ctx.run
    task = run_store.task(st, need.task_id)
    done = int(st.get("replans") or 0)
    if done >= MAX_REPLANS:
        raise Waiting(
            task["id"],
            [
                {
                    "id": f"{task['id']}-replan-limit",
                    "question": f"再計画を {MAX_REPLANS} 回しても {task['id']} が進まない（{need.reason}）。"
                    "未解決の指摘をどう扱うか、範囲と受入条件をどう変えるかを決めてください。",
                }
            ],
        )

    got = call_or_wait(ctx, stages.TABLE["replan"], task, "0", extra=brief(ctx, task, need))
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, "replan", result))

    st["replans"] = done + 1
    moves = task_order.apply_replan(st, st["name"], task["id"], result)
    carried: dict[str, list[dict[str, Any]]] = {}
    for review_id, to_task in moves:
        moved = review_store.move(run.review(task["id"]), review_id, to_task, need.reason)
        review_store.add_carried(run.review(to_task), moved, task["id"])
        carried.setdefault(to_task, []).append(moved)
    for to_task, items in carried.items():
        target = run_store.task(st, to_task)
        target["acceptance"] = str(target["acceptance"]) + task_order.carry_note(task["id"], items)

    record_judgements(st, result)
    summary = str(result.get("notes") or "").strip() or "（再計画ステージの説明なし）"
    run_store.add_decision(
        st,
        "decision",
        f"{task['id']} で再計画した（{st['replans']} 回目）: {need.reason} → {summary}",
    )
    console.info(f"再計画した（{st['replans']}/{MAX_REPLANS}）: {summary}")
    ctx.save()
