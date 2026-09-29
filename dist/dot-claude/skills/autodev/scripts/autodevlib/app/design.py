"""設計ファイルと、その確かめ（設計レビュー → 設計のジャッジ → 設計を書いたステージの直し）。

**計画・再計画ステージの結果は、すぐ state.json に写さず「提案」として持つ。** 設計の must-fix が
0 件になってから driver が写す（`app/drive.py` の `settle_proposal()`）。確かめる前の割り方で
タスクが動き出すことが無い。

    提案 → 設計レビュー（毎ラウンドまっさら） → 設計のジャッジ（セッションを続ける）
     ↑                                               │ must-fix が残る
     └────────── 設計を書いたステージが直す ←────────┘

- **前の版に戻ったかを見分けるのは設計のジャッジである。** 設計レビューに過去の版を渡すと、
  前のラウンドの結論がフレーミングになって検出が落ちる（DESIGN.md の線引き 6）
- **must-fix が 0 件なら、should-fix と nit が残っていても通す。** 残りは設計ファイルの末尾に
  書き足して、テスト作成・実装ステージへ申し送る。nit 1 件のために計画を書き直すと、1 回
  $5〜30 かかるうえ、書き足した箇所に次の指摘が立つ
- 前の版に戻った・受入条件が曖昧・停滞（直しを 2 回受けても未解決）・1 つの提案で
  `DESIGN_ROUNDS` 回回っても must-fix が残る、のどれかなら人に聞く
- 提案のタスクがすべて `light` なら、設計レビューを飛ばす

設計を書いたステージをどう呼び直すかは、そのステージの側（`planning.revise()` など）が持つ。
ここはその関数を受け取って呼ぶだけにする（計画と再計画で結果の写し方が違う）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import stages
from ..core import review_policy, task_order
from ..ports import console, files, review_store, run_store
from .context import Ctx, Waiting
from .stage_call import DESIGN_ID, call_or_wait

#: 設計を書いたステージを、直しの指示（`extra`）を渡して呼び直し、`propose()` し直す関数
Revise = Callable[[Ctx, str], None]

#: state.json の `design` の形
DEFAULTS: dict[str, Any] = {
    #: 最後に書き出した設計ファイルの版
    "version": 0,
    #: まだ state.json に写していない計画・再計画ステージの結果。写したら None に戻す
    "proposal": None,
    #: 呼び直したとき、どこから続けるか。`review`（設計レビューから）/ `fix`（直しから）/
    #: `settled`（must-fix が 0 件になった。写すのを待っている）
    "step": "review",
    "rounds": 0,
    #: いまの提案で回したラウンドの数。上限（`DESIGN_ROUNDS`）で人に聞いたら 0 に戻す
    "proposalRounds": 0,
    "fixAttempts": {},
    #: 設計のジャッジのセッション。**ランの間ずっと続ける**（前の版の経緯を覚えさせる）
    "judgeSession": None,
    #: 人が回答で決めたこと。設計のステージに毎回渡す
    "notes": [],
}


def state(st: dict[str, Any]) -> dict[str, Any]:
    d = st.setdefault(DESIGN_ID, {})
    for key, value in DEFAULTS.items():
        d.setdefault(key, value.copy() if isinstance(value, (dict, list)) else value)
    return d


def pending(st: dict[str, Any]) -> dict[str, Any] | None:
    """まだ state.json に写していない提案。"""
    d = st.get(DESIGN_ID)
    return d.get("proposal") if isinstance(d, dict) else None


def propose(
    ctx: Ctx, stage_name: str, session: str | None, result: dict[str, Any], **context: Any
) -> int:
    """計画・再計画ステージの結果を提案として持ち、設計ファイルの新しい版を書き出す。戻り値は版。

    **版は消さずに残す**（`<設計の履歴>`）。設計のジャッジが前の版に戻ったかを見比べる。

    設計レビューを飛ばすか（`skipReview`）は、提案の直しを始める前の 1 回目で決め、直した提案に
    引き継ぐ。直すたびに決め直すと、直した版がたまたま light だけになったとき、未解決の指摘を
    残したまま写してしまう。
    """
    d = state(ctx.st)
    previous = d.get("proposal")
    text = str(result.get("design") or "").strip()
    if not text:
        # 空で置き換えると、以後のテスト作成ステージが呼べる形を失う。前の版のままにする
        text = (
            files.read_text(ctx.run.design, "").strip()
            or f"（{stage_name} ステージが設計を書かなかった）"
        )
    d["version"] = int(d["version"]) + 1
    version = d["version"]
    files.write_text(ctx.run.design_version(version), text + "\n")
    files.write_text(ctx.run.design, text + "\n")
    proposal = {
        "stage": stage_name,
        "session": session,
        "result": result,
        "version": version,
        **context,
    }
    proposal["skipReview"] = (
        previous["skipReview"]
        if previous and "skipReview" in previous
        else task_order.all_light(proposed_tasks(proposal)) and not context.get("alwaysReview")
    )
    d["proposal"] = proposal
    d["step"] = "review"
    ctx.save()
    return version


def close(ctx: Ctx) -> None:
    """提案を state.json に写し終えた。次の提案は停滞の数え直しから始める。"""
    d = state(ctx.st)
    d["proposal"] = None
    d["step"] = "review"
    d["fixAttempts"] = {}
    d["proposalRounds"] = 0
    ctx.save()


def proposal_tasks(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """提案の結果に並んだタスク。"""
    result = proposal.get("result") or {}
    return [t for t in result.get("tasks") or [] if isinstance(t, dict)]


def proposed_tasks(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """提案で、これから回すタスク。**止まったタスクを残す再計画なら、そのタスクも入れる。**"""
    tasks = proposal_tasks(proposal)
    result = proposal.get("result") or {}
    if proposal.get("currentTier") and result.get("keepCurrent", True):
        tasks.append({"tier": proposal["currentTier"]})
    return tasks


def notes_block(d: dict[str, Any]) -> str:
    notes = d.get("notes") or []
    if not notes:
        return ""
    return "\n\n### 人が決めたこと（受入条件と同じ重さで守る）\n\n" + "\n".join(
        f"- {note}" for note in notes
    )


def rejected_block(data: dict[str, Any]) -> str:
    """設計レビューに渡す、却下済み・申し送り済みの指摘。

    設計レビューは毎ラウンド新しく立つので、渡さないと却下した論点を重大度を上げて立て直す。
    渡すのは結論（何を直したか）ではなく「立て直さない論点」なので、前の版に引きずられる害は小さい。
    """
    rejected = [i for i in review_store.items(data, only_open=False) if i["status"] == "rejected"]
    if not rejected:
        return ""
    lines = [
        "",
        "",
        "## 却下済みの指摘（同じ論点を立て直さない）",
        "",
    ]
    for item in rejected:
        why = item["comments"][-1]["body"] if item.get("comments") else ""
        lines.append(f"- {item['id']}（{item['rating']}、{item['location']}）: {item['review']}")
        if why:
            lines.append(f"  - 却下の理由: {why}")
    return "\n".join(lines)


def proposal_note(d: dict[str, Any], data: dict[str, Any]) -> str:
    """設計レビューに渡す、提案された割り方。設計ファイルと突き合わせて読ませる。"""
    proposal = d["proposal"]
    lines = [f"## 提案されている割り方（設計 v{proposal['version']} と一緒に確かめる）", ""]
    for index, task in enumerate(proposal_tasks(proposal), start=1):
        lines.append(f"{index}. {task.get('subject', '')}（{task.get('tier', 'standard')}）")
        for label, key in (
            ("受入条件", "acceptance"),
            ("範囲", "scope"),
            ("他タスクとの約束", "contracts"),
        ):
            if task.get(key):
                lines.append(f"   - {label}: {task[key]}")
    if proposal.get("context"):
        lines += ["", str(proposal["context"])]
    return "\n".join(lines) + rejected_block(data) + notes_block(d)


def judge_note(d: dict[str, Any], stale_ids: list[str]) -> str:
    version = d["proposal"]["version"]
    lines = [
        "## 設計の版",
        "",
        f"いまの版は v{version}（`<設計>`）。v1〜v{version} は `<設計の履歴>` にある。",
    ]
    if stale_ids:
        lines += [
            "",
            "## 停滞している指摘",
            "",
            f"次の指摘は直しを {review_policy.STALE_AFTER} 回受けても未解決だった: "
            f"{', '.join(stale_ids)}。確かめてまだ直っていなければ、原因を結果の `escalation` に書く。",
        ]
    return "\n".join(lines) + notes_block(d)


def fix_note(ctx: Ctx, d: dict[str, Any]) -> str:
    """設計を書いたステージに渡す、直しの指示。"""
    proposal = d["proposal"]
    path = ctx.run.review(DESIGN_ID)
    data = review_store.read(path) or {"items": {}}
    listed = "\n".join(
        f"- {i['id']}（{i['rating']}、{i['location']}）: {i['review']}"
        for i in review_store.items(data)
    )
    return (
        "## 設計レビューの指摘を直す\n\n"
        f"設計 v{proposal['version']} に、次の未解決の指摘がある。直した設計と割り方で、"
        "**結果をもう一度すべて返す**（`design` は差分ではなく全文で書く）。\n\n"
        f"{listed or '- （未解決の指摘は無い。人が決めたことに合わせて直す）'}\n\n"
        "指摘ごとに、何をどう直したかを次のコマンドで残す。直すべきでないと考える指摘には、その理由を書く。"
        "**status は動かさない**（動かせるのは設計のジャッジだけ）。\n\n"
        f'`<autodev> review comment --dir {path} --id <id> --commenter {proposal["stage"]} --body "…"`'
        + notes_block(d)
    )


def questions_for(
    label: str, escalation: dict[str, Any] | None, stale_ids: list[str]
) -> list[dict[str, str]]:
    cause = str((escalation or {}).get("cause") or "")
    reason = str((escalation or {}).get("reason") or "").strip()
    asked = [str(q) for q in (escalation or {}).get("questions") or []]
    if cause == "reverted":
        back = (escalation or {}).get("revertedTo")
        head = f"設計が前の版（v{back}）の形に戻った" if back else "設計が前の版の形に戻った"
        asked = [f"{head}: {reason}。どちらの形にするかを決めてください。", *asked]
    elif not asked:
        asked = [
            reason
            or f"設計の指摘 {', '.join(stale_ids)} が、直しを {review_policy.STALE_AFTER} 回受けても"
            "解決しない。どう直すかを決めてください。"
        ]
    return [
        {"id": f"{DESIGN_ID}-r{label}-q{n}", "question": q} for n, q in enumerate(asked, start=1)
    ]


def round_limit_question(label: str, data: dict[str, Any]) -> dict[str, str]:
    """1 つの提案で `DESIGN_ROUNDS` 回回っても must-fix が残ったときの質問。"""
    left = "\n".join(
        f"- {i['id']}（{i['location']}）: {i['review']}"
        for i in review_store.items(data)
        if i["rating"] == "must-fix"
    )
    return {
        "id": f"{DESIGN_ID}-r{label}-rounds",
        "question": f"設計を {review_policy.DESIGN_ROUNDS} ラウンド直しても、次の must-fix が残っている。\n"
        f"{left}\n"
        "指摘ごとに、実装ステージに任せて通すか、どう直させるかを回答してください。"
        "指示が 1 ランに大きすぎるなら、ランを止めて指示を分けてください。",
    }


def hand_off(ctx: Ctx, path: str, data: dict[str, Any]) -> None:
    """must-fix が 0 件で設計を通すとき、残った should-fix と nit を後ろのステージへ申し送る。

    設計ファイルの末尾に節を書き足す。`<設計>` を読むテスト作成・実装ステージに、仕組みを足さずに
    届く。タスクの `review.json` に立て直さないのは、タスクの段階で同じ往復を起こさないためである。
    """
    left = review_store.items(data)
    if not left:
        return
    version = state(ctx.st)["proposal"]["version"]
    for item in left:
        review_store.hand_off(path, item["id"], f"must-fix が 0 件なので設計 v{version} を通した")
    listed = "\n".join(
        f"- {i['id']}（{i['rating']}、{i['location']}）: {i['review']}" for i in left
    )
    section = (
        "\n## 設計で残った指摘（実装ステージが決めてよい）\n\n"
        "設計レビューが立てたが、設計の段階では直さなかった。テスト作成・実装ステージで扱いを決める。\n\n"
        f"{listed}\n"
    )
    files.write_text(ctx.run.design, files.read_text(ctx.run.design, "") + section)
    run_store.add_decision(
        ctx.st,
        "decision",
        f"設計 v{version} を must-fix 0 件で通し、残った指摘 {len(left)} 件"
        f"（{', '.join(i['id'] for i in left)}）を設計ファイルで実装ステージへ申し送った",
    )


def settle(ctx: Ctx, revise: Revise) -> None:
    """提案の設計の must-fix が 0 件になったら戻る。人の判断が要るなら `Waiting` を投げる。

    ラウンドの番号は `design.rounds` から続ける。提案をまたいでも前のログを上書きしない。
    """
    st = ctx.st
    d = state(st)
    proposal = d.get("proposal")
    if not proposal or d["step"] == "settled":
        return
    if proposal.get("skipReview"):
        run_store.add_decision(
            st,
            "decision",
            f"設計 v{proposal['version']} のタスクがすべて light なので、設計レビューを飛ばした",
        )
        d["step"] = "settled"
        ctx.save()
        return

    path = ctx.run.review(DESIGN_ID)
    while True:
        if d["step"] == "fix":
            revise(ctx, fix_note(ctx, d))
            d = state(st)
            after = review_store.read(path) or {"items": {}}
            d["fixAttempts"] = review_policy.bump_attempts(d["fixAttempts"], after)
            d["step"] = "review"
            ctx.save()

        label = str(int(d["rounds"]) + 1)
        review_store.init(path)
        before = review_store.read(path) or {"items": {}}
        call_or_wait(
            ctx, stages.TABLE["design-review"], None, label, extra=proposal_note(d, before)
        )
        d["rounds"] = int(label)
        d["proposalRounds"] = int(d["proposalRounds"]) + 1
        ctx.save()
        data = review_store.read(path) or {"items": {}}
        if "design-review" not in review_store.reviewers_seen(data, label):
            # 走ったが指摘 0 件だったのか、途中で終わったのかを区別できない。通さずに人に見せる
            raise Waiting(
                DESIGN_ID,
                [
                    {
                        "id": f"{DESIGN_ID}-r{label}-review",
                        "question": "設計レビューが `review done` を呼ばずに終わった"
                        f"（ログ: {ctx.run.log(DESIGN_ID, 'design-review', label)}）。"
                        "原因を取り除いたら、続けてよいと回答してください。",
                    }
                ],
            )

        stale_ids = review_policy.stale(d["fixAttempts"], data)
        try:
            judged = call_or_wait(
                ctx, stages.TABLE["design-judge"], None, label, extra=judge_note(d, stale_ids)
            )
        except Waiting:
            # 2 回続けて落ちたセッションを持ち越すと、回答しても同じセッションで落ち続ける。
            # 新しいセッションでも、過去の版は `<設計の履歴>` から読み直せる
            d["judgeSession"] = None
            ctx.save()
            raise
        # **ジャッジの報告を信じない。** 数は review.json から数える
        data = review_store.read(path) or {"items": {}}
        tally = review_policy.tally(data)
        console.info(f"  設計 r{label}: 未解決 {tally['open']}（must-fix {tally['openMustFix']}）")
        if tally["openMustFix"] == 0:
            hand_off(ctx, path, data)
            d["step"] = "settled"
            ctx.save()
            return

        d["step"] = "fix"
        ctx.save()
        ask_if_stuck(ctx, d, label, data, (judged.result or {}).get("escalation") or None)


def ask_if_stuck(
    ctx: Ctx,
    d: dict[str, Any],
    label: str,
    data: dict[str, Any],
    escalation: dict[str, Any] | None,
) -> None:
    """must-fix が残ったラウンドの終わりに、直しへ進まず人に聞くなら `Waiting` を投げる。"""
    stale_ids = review_policy.stale(d["fixAttempts"], data)
    if review_policy.design_route(escalation, stale_ids) == "ask":
        raise Waiting(DESIGN_ID, questions_for(label, escalation, stale_ids))
    if int(d["proposalRounds"]) >= review_policy.DESIGN_ROUNDS:
        # 回答したら、人が決めたことを渡して直しから続け、もう一度上限まで回せる
        d["proposalRounds"] = 0
        ctx.save()
        raise Waiting(DESIGN_ID, [round_limit_question(label, data)])
