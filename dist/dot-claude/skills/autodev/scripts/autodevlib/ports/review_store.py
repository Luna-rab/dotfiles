"""レビュー記録（review.json）の読み書き。

ステージは JSON を直接書かず、`autodev review …` を通す。書式の書き間違い（rating の綴り、
許されない status 遷移、コメント無しの status 変更）をその場で拒むためである。
**落ちた指摘が静かに消えると、次のラウンドで誰も気づけない。**

**status を動かせるのはジャッジだけである。** 実装もレビューステージも動かせない——自分で閉じられると
「未解決が 0 件」が自己承認になる。判定は役割の名乗りではなく、**driver がジャッジの process にだけ
渡す `AUTODEV_JUDGE_TOKEN`** で行う。他のステージはこの環境変数を持たないので、名乗っても通らない。

1 ラウンド目は通常レビューと敵対的レビューが同時に走るので、書き込みは flock で直列化する。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
from collections.abc import Generator
from typing import Any

from ..core import review_policy

REVIEW_STAGES: tuple[str, ...] = ("review:normal", "review:adversarial")
COMMENTERS: tuple[str, ...] = (*REVIEW_STAGES, "impl", "judge")
RATINGS: tuple[str, ...] = ("must-fix", "should-fix", "nit")
STATUSES: tuple[str, ...] = ("open", "closed", "rejected")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("closed", "rejected"),
    # 直した箇所が次のラウンドで壊れていたら、ジャッジが開き直せる
    "closed": ("open",),
    "rejected": ("open",),
}
JUDGE_TOKEN_ENV = "AUTODEV_JUDGE_TOKEN"


class Refused(Exception):
    """ステージの呼び方が規約に反している。理由をそのままステージへ返す。"""


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


@contextlib.contextmanager
def opened(path: str) -> Generator[dict[str, Any], None, None]:
    """review.json を排他で開いて、抜けるときに書き戻す。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = f"{path}.lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = _read(path)
            yield data
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"nextId": 1, "items": {}, "runs": []}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("nextId", 1)
    data.setdefault("items", {})
    data.setdefault("runs", [])
    return data


def read(path: str) -> dict[str, Any] | None:
    """走行の証拠として**ファイルの実在**を見る。無ければ None。

    指摘 0 件で終わったラウンドは 1 件も書き込まないので、中身だけでは
    「走ったが指摘なし」と「起動しなかった」を区別できない。
    """
    if not os.path.exists(path):
        return None
    return _read(path)


def init(path: str) -> bool:
    """空で作る。既にあれば中身に触らない。レビューステージがラウンドの先頭で呼ぶ。"""
    created = not os.path.exists(path)
    with opened(path):
        pass
    return created


def check_reviewer(value: str) -> str:
    if value not in REVIEW_STAGES:
        raise Refused(
            f"reviewer は {' / '.join(REVIEW_STAGES)} のいずれかにしてください: {value!r}"
        )
    return value


def check_commenter(value: str) -> str:
    if value not in COMMENTERS:
        raise Refused(f"commenter は {' / '.join(COMMENTERS)} のいずれかにしてください: {value!r}")
    return value


def check_rating(value: str) -> str:
    if value not in RATINGS:
        raise Refused(f"rating は {' / '.join(RATINGS)} のいずれかにしてください: {value!r}")
    return value


def check_judge() -> None:
    """ジャッジだけが status を動かせる。名乗りではなく環境変数で判定する。"""
    if not os.environ.get(JUDGE_TOKEN_ENV):
        raise Refused(
            "status を動かせるのはジャッジだけです。"
            "レビューは new と comment、実装は comment だけを使ってください"
        )


def check_text(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise Refused(f"{label} が空です")
    return value.strip()


def add(
    path: str,
    *,
    reviewer: str,
    rating: str,
    location: str,
    body: str,
    round_label: str,
) -> tuple[str, dict[str, int]]:
    check_reviewer(reviewer)
    check_rating(rating)
    body = check_text(body, "review")
    location = check_text(location, "location")
    with opened(path) as data:
        review_id = f"r{data['nextId']}"
        data["nextId"] += 1
        data["items"][review_id] = {
            "reviewer": reviewer,
            "round": round_label,
            "rating": rating,
            "location": location,
            "review": body,
            "status": "open",
            "at": now(),
            "comments": [],
            "transitions": [],
        }
        return review_id, review_policy.tally(data)


def comment(path: str, review_id: str, commenter: str, body: str) -> dict[str, int]:
    check_commenter(commenter)
    body = check_text(body, "comment")
    with opened(path) as data:
        item = _get(data, review_id)
        item["comments"].append({"by": commenter, "at": now(), "body": body})
        return review_policy.tally(data)


def set_status(path: str, review_id: str, to: str, body: str) -> dict[str, int]:
    """status を動かす。**コメントを必ず伴う。**

    コメント無しで畳めると「なぜ閉じたか」が残らない。次のラウンドのジャッジも、
    残件を読む人間も、判断の根拠を追えなくなる。
    """
    check_judge()
    if to not in STATUSES:
        raise Refused(f"status は {' / '.join(STATUSES)} のいずれかにしてください: {to!r}")
    body = check_text(body, "comment")
    with opened(path) as data:
        item = _get(data, review_id)
        current = item["status"]
        if to not in TRANSITIONS.get(current, ()):
            raise Refused(f"{current} から {to} へは動かせません: {review_id}")
        item["comments"].append({"by": "judge", "at": now(), "body": body})
        item["transitions"].append({"from": current, "to": to, "at": now()})
        item["status"] = to
        return review_policy.tally(data)


def _get(data: dict[str, Any], review_id: str) -> dict[str, Any]:
    item = data["items"].get(review_id)
    if item is None:
        known = ", ".join(sorted(data["items"])) or "(まだ 1 件も無い)"
        raise Refused(f"そのレビューが無い: {review_id}（ある id: {known}）")
    return item


def items(data: dict[str, Any], only_open: bool = True) -> list[dict[str, Any]]:
    out = []
    for review_id, item in sorted(data["items"].items(), key=lambda kv: int(kv[0][1:])):
        if only_open and item["status"] != "open":
            continue
        out.append({"id": review_id, **item})
    return out


def done(path: str, reviewer: str, round_label: str, found: int) -> dict[str, Any]:
    """レビューステージが**終わったことを報告する**。指摘 0 件でも必ず呼ぶ。

    指摘の有無から体数を数えると、「走ったが指摘 0 件」と「起動しなかった」を区別できない。
    だから走行そのものを記録に残す。
    """
    check_reviewer(reviewer)
    with opened(path) as data:
        # **ラウンドは文字列で書く。** 数値で入ると `reviewers_seen()` の照合が外れ、
        # 完了チェック④が「r1: review:normal が走っていない」と言って全タスクが blocked になる
        data["runs"].append(
            {"reviewer": reviewer, "round": str(round_label), "at": now(), "found": found}
        )
        return review_policy.tally(data)


def reviewers_seen(data: dict[str, Any], round_label: str | None = None) -> list[str]:
    """そのラウンドで**走り終えた**レビューステージの種別。

    体数をコードに埋めず、ここから数える。後からレビューステージを足しても、期待する体数の
    計算に手を入れなくて済む（`REVIEW_STAGES` に 1 行足すだけになる）。

    **`round` は文字列に直してから比べる。** `done()` は文字列で書くが、review.json は
    追跡しないファイルで手でも直せるので、読むときに型を仮定しない。数値の 1 と文字列の
    "1" が混ざると照合が外れ、完了チェック④が「r1: review:normal が走っていない」と言って
    全タスクが blocked になる。
    """
    seen: list[str] = []
    for run in data.get("runs", []):
        if round_label is not None and str(run["round"]) != round_label:
            continue
        if run["reviewer"] not in seen:
            seen.append(run["reviewer"])
    return seen


def adversarial_ran(data: dict[str, Any]) -> bool:
    """このタスクで敵対的レビューが**1 度でも**走り終えたか。

    ラウンド単位の体数では「standard なのに敵対的が 1 度も走っていない」を表せない
    （2 ラウンド目で解消したタスクは期待も実測も 1 になる）。タスク全体で見る。
    """
    return any(run["reviewer"] == "review:adversarial" for run in data.get("runs", []))
