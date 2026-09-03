"""review.json の読み書きと、役割・rating・status 遷移の検査。

**GitHub を呼ばない。** レビューの記録はタスクごとのファイル
`<ベース>/notes/task<番号>/review.json` に閉じている。PR は全件が決着してから作るので、
レビューの往復が走っている間、GitHub 側には何も存在しない。

ファイルの形は次のとおり。キーが review-id で、挿入順（＝立った順）を保つ。

    {
      "r1": {
        "reviewer": "review:normal",
        "rating": "must-fix",
        "location": "src/core/parser.rs:42",
        "review": "境界値 len == 0 のとき slice[0] で panic する。",
        "status": "open",
        "threads": [
          {
            "thread_id": "t1",
            "comments": [
              {"commenter": "impl:a", "comment": "早期 return を追加 (a1b2c3d)"},
              {"commenter": "judge", "comment": "parser.rs:44 を確認。cargo test 通過"}
            ],
            "transition": {"from": "open", "to": "closed"}
          }
        ]
      }
    }

この形を dict のまま扱わず dataclass に載せているのは、フィールド名の打ち間違いを読み込みの
時点で落とすためである。dict だと `review["ratting"]` のような綴り違いが実行時まで気づかれず、
`.get()` が None を返して静かに素通りする。dataclass なら `from_json()` が知らないキーを
その場で拒み、型検査（ty）もフィールド名を見られる。

**pydantic を使わないのは、このスクリプトを標準ライブラリだけで動かすためである。**
supervisor のスクリプトは配布された dotfiles の中から `python3` で直接起動されるので、
外部パッケージを増やすと「入れた人が別途インストールしないと動かない」状態になる。
検査の中身（rating の綴り、許されない status 遷移）は下の `check_*` が持つ。

スレッドは「**修正 → 判定の 1 往復**」を 1 本とする。判定が付いた（`transition` を持つ）
スレッドは閉じたものとして扱い、次のコメントは新しいスレッドを立てる。この規則を
`target_thread()` に閉じ込めてあるので、呼ぶ側は「新しいスレッドにするか」を判断しない。

同じタスクのレビュアー 2 体が同時に書きうるので、読み書きは `fcntl.flock` で直列化する
（`with_lock()` / `read_only()`）。ロックを取らずに「読む → 足す → 書く」を行うと、
先に読んだ側が書き戻した時点で後から書かれた指摘が消える。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any

from .shell import die

REVIEW_FILE = "review.json"

# レビューを立てられる役割。裁定と実装は立てられない（指摘を出すのはレビュアーだけ）
REVIEWERS: tuple[str, ...] = ("review:normal", "review:adversarial")
# コメントを書ける役割
COMMENTERS: tuple[str, ...] = ("impl:a", "impl:b", "review:normal", "review:adversarial", "judge")
# status を動かせる役割
JUDGE = "judge"

RATINGS: tuple[str, ...] = ("must-fix", "should-fix", "nit")
STATUSES: tuple[str, ...] = ("open", "closed", "rejected")

# 許す status 遷移。closed / rejected から open へ戻せるのは、裁定が畳んだ後に
# 同じ問題の再発が見つかる経路があるためである（戻せるのも裁定だけ）
TRANSITIONS: set[tuple[str, str]] = {
    ("open", "closed"),
    ("open", "rejected"),
    ("closed", "open"),
    ("rejected", "open"),
}


def _require_keys(raw: object, allowed: set[str], required: set[str], what: str) -> dict[str, Any]:
    """JSON の 1 オブジェクトが、知っているキーだけを持つことを確かめる。

    知らないキーを黙って捨てない。捨てると、書式を増やした新しい版が書いた review.json を
    古い版が読んだときに、その場では通って**書き戻しで欠落する**。読めない形は落とす。
    """
    if not isinstance(raw, dict):
        die(f"review.json の {what} がオブジェクトではありません: {raw!r}")
    unknown = set(raw) - allowed
    if unknown:
        die(f"review.json の {what} に知らないキーがあります: {', '.join(sorted(unknown))}")
    missing = required - set(raw)
    if missing:
        die(f"review.json の {what} にキーがありません: {', '.join(sorted(missing))}")
    return raw


@dataclass
class Comment:
    """1 件のコメント。誰が書いたかと本文だけを持つ。"""

    commenter: str
    comment: str

    @classmethod
    def from_json(cls, raw: object) -> Comment:
        data = _require_keys(raw, {"commenter", "comment"}, {"commenter", "comment"}, "comment")
        return cls(commenter=str(data["commenter"]), comment=str(data["comment"]))

    def to_json(self) -> dict[str, str]:
        return {"commenter": self.commenter, "comment": self.comment}


@dataclass
class Transition:
    """そのスレッドで起きた status の遷移。

    JSON 側のキーは `from` だが、Python の予約語なのでフィールド名は `from_status` にする。
    読み書きの両方でここだけ名前を差し替える（呼ぶ側はこの違いを意識しない）。
    """

    from_status: str
    to: str

    @classmethod
    def from_json(cls, raw: object) -> Transition:
        data = _require_keys(raw, {"from", "to"}, {"from", "to"}, "transition")
        return cls(from_status=str(data["from"]), to=str(data["to"]))

    def to_json(self) -> dict[str, str]:
        return {"from": self.from_status, "to": self.to}


@dataclass
class Thread:
    """「修正 → 判定」の 1 往復。判定が付くと `transition` が入り、その往復は終わる。"""

    thread_id: str
    comments: list[Comment] = field(default_factory=list)
    transition: Transition | None = None

    @property
    def settled(self) -> bool:
        """判定が付いているか。付いていればこのスレッドにはもう足さない。"""
        return self.transition is not None

    @classmethod
    def from_json(cls, raw: object) -> Thread:
        data = _require_keys(raw, {"thread_id", "comments", "transition"}, {"thread_id"}, "thread")
        comments = data.get("comments") or []
        if not isinstance(comments, list):
            die(f"review.json の thread.comments が配列ではありません: {comments!r}")
        transition = data.get("transition")
        return cls(
            thread_id=str(data["thread_id"]),
            comments=[Comment.from_json(c) for c in comments],
            transition=Transition.from_json(transition) if transition is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "thread_id": self.thread_id,
            "comments": [c.to_json() for c in self.comments],
        }
        if self.transition is not None:
            out["transition"] = self.transition.to_json()
        return out


@dataclass
class Review:
    """1 件の指摘。rating は立てた後、誰も変えられない（変更の口をここに作らない）。"""

    reviewer: str
    rating: str
    location: str
    review: str
    status: str = "open"
    threads: list[Thread] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: object) -> Review:
        data = _require_keys(
            raw,
            {"reviewer", "rating", "location", "review", "status", "threads"},
            {"reviewer", "rating", "location", "review", "status"},
            "review",
        )
        threads = data.get("threads") or []
        if not isinstance(threads, list):
            die(f"review.json の review.threads が配列ではありません: {threads!r}")
        return cls(
            reviewer=str(data["reviewer"]),
            rating=str(data["rating"]),
            location=str(data["location"]),
            review=str(data["review"]),
            status=str(data["status"]),
            threads=[Thread.from_json(t) for t in threads],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "rating": self.rating,
            "location": self.location,
            "review": self.review,
            "status": self.status,
            "threads": [t.to_json() for t in self.threads],
        }


# review-id をキーにした台帳。挿入順（＝立った順）を保つ
Reviews = dict[str, Review]


def review_path(directory: str) -> str:
    """そのタスクの review.json のパス。"""
    return os.path.join(directory, REVIEW_FILE)


def exists(path: str) -> bool:
    """review.json が既に作られているか。

    「レビューが走ったか」の唯一の外形的な手がかりである。指摘 0 件で終わったラウンドは
    1 件も書き込まないので、中身では走行の有無を判定できない。そこでレビューエージェントに
    ラウンドの先頭で `init` を呼ばせ、**ファイルの実在**を走行の証拠として使う
    （`--require-empty` がこれを見る）。
    """
    return os.path.isfile(path)


def check_reviewer(reviewer: str) -> str:
    if reviewer not in REVIEWERS:
        die(f"reviewer は {' / '.join(REVIEWERS)} のいずれかにしてください: {reviewer!r}")
    return reviewer


def check_commenter(commenter: str) -> str:
    if commenter not in COMMENTERS:
        die(f"commenter は {' / '.join(COMMENTERS)} のいずれかにしてください: {commenter!r}")
    return commenter


def check_judge(commenter: str) -> str:
    """status を動かせるのは裁定だけである。

    自分が受けた指摘を自分で閉じられると、「open が 0 件」が自己承認になる。
    呼び手を名乗らせて弾く（名乗りは偽れるが、契約どおりに動くエージェントは必ず落ちる）。
    """
    check_commenter(commenter)
    if commenter != JUDGE:
        die(
            f"status を動かせるのは {JUDGE} だけです: {commenter!r}"
            "（レビュアーと実装は comment で所見を残してください。"
            "直ったかどうかを判定して閉じるのは裁定役です）"
        )
    return commenter


def check_rating(rating: str) -> str:
    if rating not in RATINGS:
        die(f"rating は {' / '.join(RATINGS)} のいずれかにしてください: {rating!r}")
    return rating


def check_status(status: str) -> str:
    if status not in STATUSES:
        die(f"status は {' / '.join(STATUSES)} のいずれかにしてください: {status!r}")
    return status


def check_transition(current: str, to: str) -> str:
    """status の遷移を検査する。同じ status への遷移も拒む。"""
    if (current, to) not in TRANSITIONS:
        die(
            f"{current} から {to} へは動かせません。"
            "動かせるのは open→closed / open→rejected / closed→open / rejected→open です"
        )
    return to


def check_text(value: object, label: str) -> str:
    """空文字と空白だけを拒む。指摘も返信も、中身が無ければ次の人が動けない。"""
    text = str(value or "").strip()
    if not text:
        die(f"{label} が空です")
    return text


def check_location(location: object) -> str:
    """`path` または `path:line` の形。改行を含む値は拒む。"""
    text = check_text(location, "location")
    if "\n" in text:
        die("location に改行を含められません（`src/core/parser.rs:42` の形で書いてください）")
    return text


def _load(handle: IO[str]) -> Reviews:
    handle.seek(0)
    text = handle.read()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"review.json を解釈できません: {exc}")
    if not isinstance(data, dict):
        die("review.json は review-id をキーにしたオブジェクトにしてください")
    return {str(key): Review.from_json(value) for key, value in data.items()}


def _dump(handle: IO[str], data: Reviews) -> None:
    handle.seek(0)
    handle.truncate()
    json.dump(
        {key: review.to_json() for key, review in data.items()},
        handle,
        ensure_ascii=False,
        indent=2,
    )
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


@contextlib.contextmanager
def with_lock(path: str) -> Iterator[Reviews]:
    """排他ロックを取って読み、呼び出し元の変更を書き戻す。

    `yield` の後で書くので、呼び出し元が `die()` で止まったときは書き戻さない
    （検査に落ちた変更が半分だけ残ることがない）。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # "a+" は無ければ作る。既存の中身は消さない（読んでから truncate する）
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            data = _load(handle)
            yield data
            _dump(handle, data)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_only(path: str) -> Reviews:
    """共有ロックで読むだけ。ファイルが無ければ空の dict。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            return _load(handle)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _next_id(existing: list[str], prefix: str) -> str:
    """`r1` `r2` … の次の番号。数字以外の ID が混ざっていても番号を進める。"""
    numbers = [
        int(key[len(prefix) :])
        for key in existing
        if isinstance(key, str) and key.startswith(prefix) and key[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1}"


def new_review_id(data: Reviews) -> str:
    return _next_id(list(data), "r")


def get_review(data: Reviews, review_id: str) -> Review:
    """review-id で引く。無ければ止める。"""
    review = data.get(review_id)
    if review is None:
        known = " / ".join(data) or "（1 件もありません）"
        die(f"review-id {review_id!r} がありません。あるのは: {known}")
    return review


def target_thread(review: Review, force_new: bool = False) -> Thread:
    """コメントを足すスレッドを決めて返す。

    判定（`transition`）が付いたスレッドは 1 往復が終わったものなので、そこには足さず
    新しいスレッドを立てる。呼ぶ側が毎回判断しないで済むように、規則をここに置く。
    """
    if not force_new and review.threads and not review.threads[-1].settled:
        return review.threads[-1]
    thread = Thread(thread_id=_next_id([t.thread_id for t in review.threads], "t"))
    review.threads.append(thread)
    return thread


def tally(data: Reviews) -> dict[str, int]:
    """status ごとの件数と、open のうち rating が must-fix のものの件数。

    `open_must_fix` を同じ場所で返すのは、裁定エージェントが `schema` の `openMustFix` に
    入れる値だからである。ワークフローの無進捗判定は「open の総数」と「open の must-fix」の
    両方が前ラウンド以上かで打ち切りを決める（task-workflow.js の `reviewFixLoop`）ので、
    裁定に目で数えさせると数え違いがそのまま打ち切り判定の狂いになる。
    """
    counts = dict.fromkeys(STATUSES, 0)
    counts["open_must_fix"] = 0
    for review in data.values():
        if review.status in counts:
            counts[review.status] += 1
        if review.status == "open" and review.rating == "must-fix":
            counts["open_must_fix"] += 1
    return counts


def listed(data: Reviews) -> list[dict[str, Any]]:
    """出力用に review-id を含めた配列にする。"""
    return [{"id": key, **review.to_json()} for key, review in data.items()]
