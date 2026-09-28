"""再計画。止まったタスクの割り方と設計ファイルを、再計画ステージに直させる。

再計画ステージが選べる手は 4 つで、どれを使うかはステージが決める。

- 止まったタスクの範囲・受入条件を広げて、同じタスクで直す
- 指摘を同じランの後ろのタスクへ移す（移した先のタスクのレビューで解決を確かめる）
- 未着手のタスクを組み直す（分ける・まとめる・順番を変える）
- 止まったタスクを取り下げて割り直す

**割り方と設計は切り離さない。** 形を変えると後ろのタスクの受入条件が変わり、割り方を変えると
境界の形が変わる。だから再計画ステージは設計ファイルも丸ごと返し、計画ステージと同じく
**提案**として設計レビューで確かめてから state.json に写す（`apply()`）。

**スタック済みのタスクには触らない。** 変えると上に積んだブランチの積み替えと PR の書き換えが要る。

**スタックに追加しないまま `REPLANS_WITHOUT_PROGRESS` 回続けて再計画したら、人に聞く。**
回数はタスクを 1 本スタックに追加するたびに 0 に戻す（`app/publish.py`）。ラン全体の回数では
止めない——設計を直すたびに再計画を通るので、真っ当に進むランでも回数は増える。
"""

from __future__ import annotations

from typing import Any

from ..config import stages
from ..core import task_order
from ..ports import console, review_store, run_store
from . import design
from .build import questions_of
from .context import Ctx, NeedsReplan, Waiting
from .stage_call import call_or_wait, record_judgements

#: スタックに追加しないまま、この回数だけ続けて再計画したら人に聞く
REPLANS_WITHOUT_PROGRESS = 2

#: 何が再計画を求めたか。再計画ステージへの材料の見出しになる
KIND_LABEL = {
    "scope": "ジャッジの分類（範囲の外に手を入れないと直せない）",
    "design-gap": "テスト作成ステージの報告（受入条件をテストにするのに、設計ファイルに無い形が要る）",
    "interface-change": "実装・修正ステージの報告（受入条件を満たすには、設計ファイルの形を変える必要がある）",
}

#: 設計が変わったら、テストを新しい設計に合わせてから実装を続ける。`scope` は範囲の直しで、
#: テストの前提は変わらないのでそのまま続ける
RETEST_KINDS = ("design-gap", "interface-change")


def resume_note(proposal: dict[str, Any]) -> str:
    """設計を直したあと、テスト作成と実装に渡す文。何がなぜ変わったかを読ませる。"""
    notes = str((proposal.get("result") or {}).get("notes") or "").strip()
    return (
        f"## 設計が変わった（再計画、設計 v{proposal['version']}）\n\n"
        f"{KIND_LABEL.get(str(proposal.get('kind')), proposal.get('kind'))}: {proposal.get('reason')}\n\n"
        f"再計画ステージの判断: {notes or '（説明なし）'}\n\n"
        "`<設計>` を読み直し、新しい設計に合わせる。報告した変更が設計に入っていなければ、"
        "その変更は却下された。いまの設計のとおりに作る。"
    )


def brief(ctx: Ctx, task: dict[str, Any], need: NeedsReplan) -> str:
    """再計画ステージに渡す材料。止まった理由・未解決の指摘・変えてよいタスクと変えてはいけないタスク。"""
    data = review_store.read(ctx.run.review(task["id"])) or {"items": {}}
    open_items = review_store.items(data)
    lines = [
        "## 止まったタスク",
        "",
        f"`{task['id']}`（{task['subject']}）。{KIND_LABEL.get(need.kind, need.kind)}: {need.reason}",
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
    lines += other_tasks(ctx, task)
    return "\n".join(lines)


def other_tasks(ctx: Ctx, task: dict[str, Any]) -> list[str]:
    lines = []
    for item in ctx.st["tasks"]:
        if item["id"] == task["id"]:
            continue
        mark = "変えてはいけない" if item["status"] == "stacked" else "組み直してよい"
        if item["status"] in ("stacked", "pending"):
            lines.append(
                f"- `{item['id']}`（{item['status']}、{mark}）: {item['subject']} / 受入条件: {item['acceptance']}"
            )
    return lines


def review_context(
    ctx: Ctx, task: dict[str, Any], need: NeedsReplan, result: dict[str, Any]
) -> str:
    """設計レビューに渡す、再計画の前提。止まったタスクをどうするかと、変えられないタスク。"""
    keep = result.get("keepCurrent", True)
    changed = ", ".join(sorted((result.get("current") or {}).keys())) or "なし"
    lines = [
        "## 再計画の前提",
        "",
        f"- 理由: {KIND_LABEL.get(need.kind, need.kind)}: {need.reason}",
        f"- 止まったタスク `{task['id']}`（{task['subject']}）を"
        + (f"残して続ける（書き換える項目: {changed}）" if keep else "取り下げる"),
        "- 上の割り方は、止まったタスクの後ろで回す未着手のタスクの全部である",
        "",
        "### ほかのタスク",
        "",
        *(other_tasks(ctx, task) or ["- （なし）"]),
    ]
    return "\n".join(lines)


def replan(ctx: Ctx, need: NeedsReplan) -> None:
    """再計画ステージを呼び、結果を**提案**として持つ。写すのは `drive.settle_proposal()` である。"""
    st = ctx.st
    task = run_store.task(st, need.task_id)
    streak = int(st.get("replansSinceStack") or 0)
    if streak >= REPLANS_WITHOUT_PROGRESS:
        raise Waiting(
            task["id"],
            [
                {
                    "id": f"{task['id']}-replan-limit",
                    "question": f"タスクを 1 本もスタックに追加しないまま、再計画を {streak} 回続けた"
                    f"（今回の理由: {need.reason}）。"
                    "未解決の指摘をどう扱うか、範囲と受入条件と設計をどう変えるかを決めてください。",
                }
            ],
        )

    got = call_or_wait(ctx, stages.TABLE["replan"], task, "0", extra=brief(ctx, task, need))
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, "replan", result))

    st["replansSinceStack"] = streak + 1
    st["replans"] = int(st.get("replans") or 0) + 1
    design.propose(
        ctx,
        "replan",
        got.session_id,
        result,
        taskId=task["id"],
        reason=need.reason,
        kind=need.kind,
        currentTier=task["tier"],
        # 報告を受けて設計の形を変えるときは、タスクが light でも設計レビューを通す。light は
        # 新しい公開インターフェースを作らないという前提が、報告の時点で崩れている
        alwaysReview=need.kind != "scope",
        context=review_context(ctx, task, need, result),
    )


def revise(ctx: Ctx, extra: str) -> None:
    """設計の指摘を、再計画ステージに同じセッションの続きで直させ、提案し直す。"""
    proposal = design.pending(ctx.st) or {}
    task = run_store.task(ctx.st, str(proposal["taskId"]))
    got = call_or_wait(
        ctx, stages.TABLE["replan"], task, "0", extra=extra, continue_from=proposal.get("session")
    )
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, "replan", result))
    need = NeedsReplan(task["id"], str(proposal.get("reason") or ""), [], str(proposal.get("kind")))
    keys = ("taskId", "reason", "kind", "currentTier", "alwaysReview")
    context = {k: proposal[k] for k in keys if k in proposal}
    design.propose(
        ctx,
        "replan",
        got.session_id or proposal.get("session"),
        result,
        **context,
        context=review_context(ctx, task, need, result),
    )


def apply(ctx: Ctx, proposal: dict[str, Any]) -> None:
    """確かめ終えた再計画の結果を state.json に写す。

    **ここでは state.json を保存しない。** 呼び出し元の `design.close()` が提案を消すのと同じ
    1 回の書き出しで残す。途中で保存すると、そこで落ちたときに同じ提案を二度写す。

    指摘の移管は review.json に先に書かれるので、state.json を保存する前に落ちると、呼び直しで
    同じ移管をもう一度通る。そのときは移し済みの指摘を移し直さず、移した先に立て直し済みなら
    重ねない（state.json は写す前のままなので、新しいタスクの番号は前回と同じになる）。
    """
    st, run = ctx.st, ctx.run
    result = proposal["result"]
    task = run_store.task(st, str(proposal["taskId"]))
    reason = str(proposal.get("reason") or "")
    moves = task_order.apply_replan(st, st["name"], task["id"], result)
    carried: dict[str, list[dict[str, Any]]] = {}
    for review_id, to_task in moves:
        source = review_store.read(run.review(task["id"])) or {"items": {}}
        item = source["items"].get(review_id)
        if item is None:
            console.info(f"  {task['id']} に指摘 {review_id} が無いので移さない")
            continue
        if item.get("status") == "moved" and item.get("movedTo") == to_task:
            moved = {"id": review_id, **item}
        else:
            moved = review_store.move(run.review(task["id"]), review_id, to_task, reason)
        target = review_store.read(run.review(to_task)) or {"items": {}}
        origin = f"{task['id']}/{review_id}"
        if not any(i.get("movedFrom") == origin for i in target["items"].values()):
            review_store.add_carried(run.review(to_task), moved, task["id"])
        carried.setdefault(to_task, []).append(moved)
    for to_task, items in carried.items():
        target = run_store.task(st, to_task)
        target["acceptance"] = str(target["acceptance"]) + task_order.carry_note(task["id"], items)

    if proposal.get("kind") in RETEST_KINDS and task["status"] != "dropped":
        # テスト作成からやり直す。テストがすでにあれば（interface-change）、実装が途中まであるので
        # 落ちることは確かめない（`build.make_tests()` は初めて書くときだけ確かめる）
        task["phase"] = "tests"
        task["resumeNote"] = resume_note(proposal)

    record_judgements(st, result)
    summary = str(result.get("notes") or "").strip() or "（再計画ステージの説明なし）"
    run_store.add_decision(
        st,
        "decision",
        f"{task['id']} で再計画した（{st.get('replans') or 1} 回目、設計 v{proposal['version']}）: "
        f"{reason} → {summary}",
    )
    console.info(
        f"再計画した（スタックに追加するまでに {st.get('replansSinceStack')} 回目）: {summary}"
    )
