"""レビュアーの体数と、レビューを打ち切る条件、決着の数え方。

**体数も打ち切りの条件も段の表（`config/stages.py`）には無い。** レビュアーを 1 体増やしても
ここと `ports/review_store.py` の `REVIEWERS` の 2 行で済む。

数えるのは `review.json` の中身を受け取ってからで、ファイルを読むのは
`ports/review_store.py` である。
"""

from __future__ import annotations

from typing import Any

#: レビュー → 裁定 → 修正を回す上限。`stop_reason()` がこの数で打ち切る
MAX_ROUNDS = 3


def expected_reviewers(tier: str, change_kind: str, round_index: int) -> list[str]:
    """そのラウンドで走るべきレビュアー。**体数をコードに埋めない。**

    - `light` は通常レビュー 1 体（敵対的が拾う「前提の誤り」のリスクが小さい）
    - `standard` の 1 巡目は通常＋敵対的
    - 2 巡目以降は通常 1 体（見る差分が「open を直した分」だけなので、同じ差分を
      もう一度「すべて誤りである」前提で読み直す価値が下がる）
    - 修正が docs だけなら通常 1 体
    """
    if tier == "light" or change_kind == "docs":
        return ["review:normal"]
    if round_index == 1:
        return ["review:normal", "review:adversarial"]
    return ["review:normal"]


def stop_reason(index: int, tally: dict[str, int], prev_total: int, prev_must: int) -> str | None:
    """このラウンドで打ち切るか。打ち切るなら理由、続けるなら None。

    **上限だけだと、修正しても指摘が減らないタスクで上限までトークンを無駄に使う。**
    無進捗は「open の総数」と「open の must-fix」の**両方**が前ラウンド以上のときだけ立てる。
    """
    if index >= MAX_ROUNDS:
        return f"ラウンド上限（open {tally['open']} 件 / must-fix {tally['openMustFix']} 件）"
    if tally["open"] >= prev_total and tally["openMustFix"] >= prev_must:
        return f"無進捗（open {tally['open']} 件）"
    return None


def tally(data: dict[str, Any]) -> dict[str, int]:
    """決着の判定に使う数。`openTotal` と `openMustFix` は無進捗の判定にも使う。"""
    out = {
        "total": len(data["items"]),
        "open": 0,
        "openMustFix": 0,
        "closed": 0,
        "rejected": 0,
    }
    for item in data["items"].values():
        if item["status"] == "open":
            out["open"] += 1
            if item["rating"] == "must-fix":
                out["openMustFix"] += 1
        else:
            out[item["status"]] += 1
    return out
