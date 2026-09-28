"""レビューステージの体数、停滞の検知、ジャッジの分類から次の手を決める規則、解消の数え方。

**体数も停滞の条件もステージの表（`config/stages.py`）には無い。** レビューステージを 1 体増やしても
ここと `ports/review_store.py` の `REVIEW_STAGES` の 2 行で済む。

**ラウンドに上限は無い。** 指摘が全件解消するまで回す。止まらずに回り続けるのを防ぐのは
停滞の検知で、検知したらジャッジに原因を分類させ、原因ごとの手（テストの直し・実装のやり直し・
再計画・人への質問）に移る。

数えるのは `review.json` の中身を受け取ってからで、ファイルを読むのは
`ports/review_store.py` である。
"""

from __future__ import annotations

from typing import Any

#: 同じ指摘が修正をこの回数受けても未解決なら、停滞とみなす
STALE_AFTER = 2

#: ジャッジが返す停滞の原因と、driver が打つ手
#:
#: - `tests`     テストが誤っている → テスト作成ステージが直し、修正ステージが続ける
#: - `approach`  範囲の中で直せるはずが堂々巡り → 実装を新しいセッションでやり直す
#: - `scope`     範囲の外に手を入れないと直せない（タスクの割り方の問題） → 再計画
#: - `ambiguous` 受入条件が曖昧で人が決める → 回答待ち
ROUTES = {"tests": "tests", "approach": "approach", "scope": "replan", "ambiguous": "ask"}


#: 設計のジャッジが返す原因。どちらも人に聞く
#:
#: - `reverted`  前の版の形に戻った（その版から変えた理由があったのに、また同じ形になった）
#: - `ambiguous` 受入条件か指示が一意に定まらず、どちらの設計にするか人が決める
DESIGN_CAUSES = ("reverted", "ambiguous")


def expected_reviewers(tier: str, change_kind: str, round_index: int) -> list[str]:
    """そのラウンドで走るべきレビューステージ。**体数をコードに埋めない。**

    - `light` は通常レビュー 1 体（敵対的が拾う「前提の誤り」のリスクが小さい）
    - `standard` の 1 ラウンド目は通常＋敵対的
    - 2 ラウンド目以降は通常 1 体（見る差分が「open を直した分」だけなので、同じ差分を
      もう一度「すべて誤りである」前提で読み直す価値が下がる）
    - 修正が docs だけなら通常 1 体
    """
    if tier == "light" or change_kind == "docs":
        return ["review:normal"]
    if round_index == 1:
        return ["review:normal", "review:adversarial"]
    return ["review:normal"]


def bump_attempts(attempts: dict[str, int], data: dict[str, Any]) -> dict[str, int]:
    """直す手（修正・実装のやり直し）が 1 回走ったあと、その時点で未解決の指摘ごとに回数を 1 足す。"""
    out = dict(attempts)
    for review_id, item in data["items"].items():
        if item["status"] == "open":
            out[review_id] = out.get(review_id, 0) + 1
    return out


def stale(attempts: dict[str, int], data: dict[str, Any]) -> list[str]:
    """修正を `STALE_AFTER` 回受けても未解決の指摘。**ラウンドごとに新しく立った指摘は数えない。**"""
    return [
        review_id
        for review_id, item in sorted(data["items"].items(), key=lambda kv: int(kv[0][1:]))
        if item["status"] == "open" and attempts.get(review_id, 0) >= STALE_AFTER
    ]


def route(escalation: dict[str, Any] | None, stale_ids: list[str]) -> str:
    """ジャッジの分類から次の手を決める。`fix` / `tests` / `approach` / `replan` / `ask` のどれか。

    停滞しているのにジャッジが分類を返さなかったら再計画に回す。**スタックに追加しないまま
    再計画を続けると人に聞く**ので、ジャッジが分類を返さないまま修正を繰り返すことは無い。
    """
    cause = str((escalation or {}).get("cause") or "")
    if cause in ROUTES:
        return ROUTES[cause]
    return "replan" if stale_ids else "fix"


def design_route(escalation: dict[str, Any] | None, stale_ids: list[str]) -> str:
    """設計のジャッジの分類から次の手を決める。`fix`（設計を書いたステージが直す）か `ask`。

    **停滞しているのにジャッジが分類を返さなかったら人に聞く。** タスクと違って、設計には
    割り方を直す手（再計画）の先が無い。直し続けても変わらないなら、人が決めるしかない。
    """
    cause = str((escalation or {}).get("cause") or "")
    if cause in DESIGN_CAUSES or stale_ids:
        return "ask"
    return "fix"


def tally(data: dict[str, Any]) -> dict[str, int]:
    """解消の判定に使う数。`moved` は同じランの別のタスクへ移した指摘で、そのタスクで解決を確かめる。"""
    out = {
        "total": len(data["items"]),
        "open": 0,
        "openMustFix": 0,
        "closed": 0,
        "rejected": 0,
        "moved": 0,
    }
    for item in data["items"].values():
        if item["status"] == "open":
            out["open"] += 1
            if item["rating"] == "must-fix":
                out["openMustFix"] += 1
        else:
            out[item["status"]] = out.get(item["status"], 0) + 1
    return out
