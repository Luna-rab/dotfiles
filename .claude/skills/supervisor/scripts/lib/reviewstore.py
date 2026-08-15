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

スレッドは「**修正 → 判定の 1 往復**」を 1 本とする。判定が付いた（`transition` を持つ）
スレッドは閉じたものとして扱い、次のコメントは新しいスレッドを立てる。この規則を
`target_thread()` に閉じ込めてあるので、呼ぶ側は「新しいスレッドにするか」を判断しない。

同じタスクのレビュアー 2 体が同時に書きうるので、読み書きは `fcntl.flock` で直列化する
（`with_lock()` / `read_only()`）。ロックを取らずに「読む → 足す → 書く」を行うと、
先に読んだ側が書き戻した時点で後から書かれた指摘が消える。
"""

import contextlib
import fcntl
import json
import os

from .shell import die

REVIEW_FILE = "review.json"

# レビューを立てられる役割。裁定と実装は立てられない（指摘を出すのはレビュアーだけ）
REVIEWERS = ("review:normal", "review:adversarial")
# コメントを書ける役割
COMMENTERS = ("impl:a", "impl:b", "review:normal", "review:adversarial", "judge")
# status を動かせる役割
JUDGE = "judge"

RATINGS = ("must-fix", "should-fix", "nit")
STATUSES = ("open", "closed", "rejected")

# 許す status 遷移。closed / rejected から open へ戻せるのは、裁定が畳んだ後に
# 同じ問題の再発が見つかる経路があるためである（戻せるのも裁定だけ）
TRANSITIONS = {
    ("open", "closed"),
    ("open", "rejected"),
    ("closed", "open"),
    ("rejected", "open"),
}


def review_path(directory):
    """そのタスクの review.json のパス。"""
    return os.path.join(directory, REVIEW_FILE)


def exists(path):
    """review.json が既に作られているか。

    「レビューが走ったか」の唯一の外形的な手がかりである。指摘 0 件で終わったラウンドは
    1 件も書き込まないので、中身では走行の有無を判定できない。そこでレビューエージェントに
    ラウンドの先頭で `init` を呼ばせ、**ファイルの実在**を走行の証拠として使う
    （`--require-empty` がこれを見る）。
    """
    return os.path.isfile(path)


def check_reviewer(reviewer):
    if reviewer not in REVIEWERS:
        die(f"reviewer は {' / '.join(REVIEWERS)} のいずれかにしてください: {reviewer!r}")
    return reviewer


def check_commenter(commenter):
    if commenter not in COMMENTERS:
        die(f"commenter は {' / '.join(COMMENTERS)} のいずれかにしてください: {commenter!r}")
    return commenter


def check_judge(commenter):
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


def check_rating(rating):
    if rating not in RATINGS:
        die(f"rating は {' / '.join(RATINGS)} のいずれかにしてください: {rating!r}")
    return rating


def check_status(status):
    if status not in STATUSES:
        die(f"status は {' / '.join(STATUSES)} のいずれかにしてください: {status!r}")
    return status


def check_transition(current, to):
    """status の遷移を検査する。同じ status への遷移も拒む。"""
    if (current, to) not in TRANSITIONS:
        die(
            f"{current} から {to} へは動かせません。"
            "動かせるのは open→closed / open→rejected / closed→open / rejected→open です"
        )
    return to


def check_text(value, label):
    """空文字と空白だけを拒む。指摘も返信も、中身が無ければ次の人が動けない。"""
    text = str(value or "").strip()
    if not text:
        die(f"{label} が空です")
    return text


def check_location(location):
    """`path` または `path:line` の形。改行を含む値は拒む。"""
    text = check_text(location, "location")
    if "\n" in text:
        die("location に改行を含められません（`src/core/parser.rs:42` の形で書いてください）")
    return text


def _load(handle):
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
    return data


def _dump(handle, data):
    handle.seek(0)
    handle.truncate()
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


@contextlib.contextmanager
def with_lock(path):
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


def read_only(path):
    """共有ロックで読むだけ。ファイルが無ければ空の dict。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            return _load(handle)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _next_id(existing, prefix):
    """`r1` `r2` … の次の番号。数字以外の ID が混ざっていても番号を進める。"""
    numbers = []
    for key in existing:
        if isinstance(key, str) and key.startswith(prefix) and key[len(prefix):].isdigit():
            numbers.append(int(key[len(prefix):]))
    return f"{prefix}{max(numbers, default=0) + 1}"


def new_review_id(data):
    return _next_id(data, "r")


def get_review(data, review_id):
    """review-id で引く。無ければ止める。"""
    review = data.get(review_id)
    if review is None:
        known = " / ".join(data) or "（1 件もありません）"
        die(f"review-id {review_id!r} がありません。あるのは: {known}")
    return review


def target_thread(review, force_new=False):
    """コメントを足すスレッドを決めて返す。

    判定（`transition`）が付いたスレッドは 1 往復が終わったものなので、そこには足さず
    新しいスレッドを立てる。呼ぶ側が毎回判断しないで済むように、規則をここに置く。
    """
    threads = review.setdefault("threads", [])
    if not force_new and threads and "transition" not in threads[-1]:
        return threads[-1]
    thread = {"thread_id": _next_id([t.get("thread_id") for t in threads], "t"),
              "comments": []}
    threads.append(thread)
    return thread


def tally(data):
    """status ごとの件数と、open のうち rating が must-fix のものの件数。

    `open_must_fix` を同じ場所で返すのは、裁定エージェントが `schema` の `openMustFix` に
    入れる値だからである。ワークフローの無進捗判定は「open の総数」と「open の must-fix」の
    両方が前ラウンド以上かで打ち切りを決める（workflow-script.md の `reviewFixLoop`）ので、
    裁定に目で数えさせると数え違いがそのまま打ち切り判定の狂いになる。
    """
    counts = {status: 0 for status in STATUSES}
    counts["open_must_fix"] = 0
    for review in data.values():
        status = review.get("status")
        if status in counts:
            counts[status] += 1
        if status == "open" and review.get("rating") == "must-fix":
            counts["open_must_fix"] += 1
    return counts


def listed(data, review_id=None):
    """出力用に review-id を含めた配列にする。"""
    items = data.items() if review_id is None else [(review_id, data[review_id])]
    return [dict(id=key, **value) for key, value in items]
