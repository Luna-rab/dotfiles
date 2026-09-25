"""state.json の tasks を組み立て、次に回す 1 本・スタックに追加する先・ラン全体の状態を決める。"""

from __future__ import annotations

from typing import Any

#: `blocked` と `failed` は、要対応で止まっていた頃のランを読むためだけに残している。
#: 今の driver はタスクをこの 2 つにしない（止まるときは回答待ちにする）
STATUSES = ("pending", "running", "stacked", "dropped", "blocked", "failed")
TIERS = ("light", "standard")

#: タスクの進み具合。driver が落ちても、呼び直せばここから続く
PHASES = ("tests", "build", "review", "gate")

#: 再計画ステージが書き換えてよい、止まったタスクの項目
EDITABLE = ("subject", "dod", "acceptance", "scope", "entrypoints", "contracts")


def new_task(number: int, run_name: str, src: dict[str, Any]) -> dict[str, Any]:
    """計画・再計画ステージが返したタスク 1 つを、state に入れる形にする。"""
    tier = src.get("tier", "standard")
    if tier not in TIERS:
        tier = "standard"  # 迷ったら standard（敵対的レビューの網を外さない）
    return {
        "id": f"task{number}",
        "subject": src.get("subject", f"task{number}"),
        "tier": tier,
        "branch": f"stack/{run_name}--task-{number}",
        "dod": src.get("dod", ""),
        "acceptance": src.get("acceptance", ""),
        "scope": src.get("scope", ""),
        "entrypoints": src.get("entrypoints", ""),
        "contracts": src.get("contracts", ""),
        "blockedBy": src.get("blockedBy", []),
        "status": "pending",
        "phase": "tests",
        "pr": None,
        "reason": None,
        "implSession": None,
        #: ジャッジのセッション。同じタスクの間は使い回し、再計画をまたいだら作り直す
        "judgeSession": None,
        #: テスト作成ステージが commit した時点の SHA。完了チェック⑤の基準になる
        "testsAt": None,
        "rounds": 0,
        #: 走らせたラウンドと、そのラウンドで走るべきだったレビュー。完了チェック④が読む
        "reviewRounds": [],
        #: 指摘ごとに、未解決のまま直す手を受けた回数。停滞の検知に使う
        "fixAttempts": {},
        #: 人が回答で決めたこと。このタスクのステージに毎回渡す
        "notes": [],
        "adversarialRan": False,
        "reviewersSeen": [],
    }


def add_tasks(data: dict[str, Any], run_name: str, planned: list[dict[str, Any]]) -> None:
    """計画ステージの割り方を state に入れる。ブランチ名は既存の規約のまま組み立てる。"""
    for index, src in enumerate(planned, start=1):
        data["tasks"].append(new_task(index, run_name, src))


def _number(task_id: str) -> int:
    return int(task_id.removeprefix("task") or 0)


def apply_replan(
    data: dict[str, Any], run_name: str, task_id: str, result: dict[str, Any]
) -> list[tuple[str, str]]:
    """再計画ステージの結果を state に写す。戻り値は移す指摘と移す先 `[(指摘 id, タスク id)]`。

    - 止まったタスクを残すなら `current` の値で項目を書き換える。捨てるなら `dropped` にする
    - 止まったタスクより後ろの未着手のタスクを、`tasks` で丸ごと差し替える
    - スタック済みのタスクには触らない
    - **新しいタスクの番号は、使ったことのある番号と重ねない。** 捨てたタスクのブランチは残るので、
      同じ名前で作ると `start_task_branch()` が古いコミットの上に乗る
    """
    tasks = data["tasks"]
    pos = next(i for i, t in enumerate(tasks) if t["id"] == task_id)
    current = tasks[pos]
    if result.get("keepCurrent", True):
        for key, value in (result.get("current") or {}).items():
            if key in EDITABLE and value:
                current[key] = value
        # 範囲が変わったので、前の経緯を覚えたジャッジと停滞の数え方を引き継がない
        current["judgeSession"] = None
        current["fixAttempts"] = {}
    else:
        current["status"] = "dropped"
        current["reason"] = str(result.get("notes") or "再計画で取り下げた")

    first = max(_number(t["id"]) for t in tasks) + 1
    added: list[dict[str, Any]] = []
    moves: list[tuple[str, str]] = []
    for offset, src in enumerate(result.get("tasks") or []):
        task = new_task(first + offset, run_name, src)
        added.append(task)
        moves += [(str(review_id), task["id"]) for review_id in src.get("carry") or []]
    rest = [t for t in tasks[pos + 1 :] if t["status"] != "pending"]
    data["tasks"] = [*tasks[: pos + 1], *added, *rest]
    return moves


def carry_note(from_task: str, items: list[dict[str, Any]]) -> str:
    """移された指摘を、移す先のタスクの受入条件に足す文。"""
    lines = [f"\n\n{from_task} から移した指摘（このタスクで解決する）:"]
    lines += [
        f"- {item['id']}（{item['rating']}、{item['location']}）: {item['review']}"
        for item in items
    ]
    return "\n".join(lines)


def next_pending(data: dict[str, Any]) -> dict[str, Any] | None:
    """次に回すタスク。**番号の小さいものから順に 1 本ずつ。**

    決定どおり同時に走らせないので、`blockedBy` は順番の指定としてだけ効く。
    """
    for item in data["tasks"]:
        if item["status"] in ("stacked", "dropped"):
            continue
        if item["status"] in ("blocked", "failed"):
            return None
        return item
    return None


def counts(data: dict[str, Any]) -> dict[str, int]:
    out = {name: 0 for name in STATUSES}
    for item in data["tasks"]:
        out[item["status"]] = out.get(item["status"], 0) + 1
    return out


def parent_of(st: dict[str, Any], task: dict[str, Any]) -> str:
    """そのタスクをスタックに追加する先。**順に 1 本ずつ回すので、親ブランチは動かない**（積み替えが要らない）。"""
    parent = st["overviewBranch"]
    for item in st["tasks"]:
        if item["id"] == task["id"]:
            break
        if item["status"] == "stacked":
            parent = item["branch"]
    return parent


def outcome_of(st: dict[str, Any]) -> str:
    """ランが今どういう状態か。**呼び出し元のエージェントがこれを読んで次を決める。**"""
    if st.get("deferred"):
        return "waiting"
    if not st["tasks"]:
        return "planning"
    held = [i for i in st["tasks"] if i["status"] in ("blocked", "failed")]
    if held:
        return "held"
    if all(i["status"] in ("stacked", "dropped") for i in st["tasks"]):
        return "stacked"
    return "running"
