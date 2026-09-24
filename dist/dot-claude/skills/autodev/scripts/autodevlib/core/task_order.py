"""state.json の tasks を組み立て、次に回す 1 本・スタックに追加する先・ラン全体の状態を決める。"""

from __future__ import annotations

from typing import Any

STATUSES = ("pending", "running", "stacked", "blocked", "failed")
TIERS = ("light", "standard")


def add_tasks(data: dict[str, Any], run_name: str, planned: list[dict[str, Any]]) -> None:
    """計画ステージの割り方を state に入れる。ブランチ名は既存の規約のまま組み立てる。"""
    for index, src in enumerate(planned, start=1):
        tier = src.get("tier", "standard")
        if tier not in TIERS:
            tier = "standard"  # 迷ったら standard（敵対的レビューの網を外さない）
        data["tasks"].append(
            {
                "id": f"task{index}",
                "subject": src.get("subject", f"task{index}"),
                "tier": tier,
                "branch": f"stack/{run_name}--task-{index}",
                "dod": src.get("dod", ""),
                "acceptance": src.get("acceptance", ""),
                "scope": src.get("scope", ""),
                "entrypoints": src.get("entrypoints", ""),
                "contracts": src.get("contracts", ""),
                "blockedBy": src.get("blockedBy", []),
                "status": "pending",
                "pr": None,
                "reason": None,
                "implSession": None,
                #: テスト作成ステージが commit した時点の SHA。完了チェック⑤の基準になる
                "testsAt": None,
                "rounds": 0,
                "adversarialRan": False,
                "reviewersSeen": [],
            }
        )


def next_pending(data: dict[str, Any]) -> dict[str, Any] | None:
    """次に回すタスク。**番号の小さいものから順に 1 本ずつ。**

    決定どおり同時に走らせないので、`blockedBy` は順番の指定としてだけ効く
    （前のタスクが `stacked` になっていなければ、そこで止める）。
    """
    for item in data["tasks"]:
        if item["status"] in ("stacked",):
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
    if all(i["status"] == "stacked" for i in st["tasks"]):
        return "stacked"
    return "running"
