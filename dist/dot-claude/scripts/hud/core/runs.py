"""`autodev status --json` の結果を読む。形は ADDENDUM §12「status --json の形」にある。

driver が生きているかは `run.driver_running` だけで決める。`updated_at`（イベントを確定した時刻）も
`progress.updated` も、走っていても古くなるので生存の目安にしない。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

#: `run.phase` の表示名
PHASE_LABEL = {
    "not-started": "始まっていない",
    "planning": "計画中",
    "running": "実行中",
    "panicked": "パニック",
    "finishing": "仕上げ中",
    "finished": "完了",
}
#: statusline に出すのは、終えていないランのうち、最後のイベントからこの秒数の内のものと回答待ちのもの。
#: 捨てたランが statusline に残り続けないための窓で、driver が生きているかの判定ではない
RECENT = 3 * 3600
#: 積む数の分母に入れないタスクの状態（止めた・引き継がれた・破棄した）
NOT_COUNTED = ("dropped", "superseded", "discarded")


@dataclass(frozen=True)
class Listing:
    """全ランの status。`error` は、一覧そのものが取れなかった理由。"""

    runs: list[dict]
    error: str | None = None


@dataclass(frozen=True)
class Running:
    """`status` が running の実行 1 つ。"""

    task: str
    stage: str
    round: int
    #: `started_at` からの秒数。まだ始まっていなければ None
    seconds: float | None
    #: 進み具合（`progress`）のターン数と直前のツール。LLM のステージだけが書く
    turns: int
    tool: str


def dicts(value: Any) -> list[dict]:
    """配列のうち、オブジェクトの要素だけ。配列でなければ空。

    status の欄は崩れていることがある（`progress` は実行器が書いたファイルの中身そのまま）。
    型を確かめずに回すと、statusline が traceback を出して落ちる。
    """
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def items(value: Any) -> list:
    """配列ならそのまま、配列でなければ空。"""
    return value if isinstance(value, list) else []


def number(value: Any) -> int:
    """整数ならそのまま、ほかは 0。bool も整数の仲間なので外す。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _failure(code: int | None, message: str, shape: str) -> str:
    if code == 0:
        return f"{shape}が返らなかった" + (f" · {message}" if message else "")
    return message or f"終了コード {code}"


def listing(code: int | None, data: Any, message: str) -> Listing:
    """`status --json`（`--name` なし）の結果。終了コード 0 で配列が返れば一覧。"""
    if code == 0 and isinstance(data, list):
        return Listing(dicts(data))
    return Listing([], _failure(code, message, "JSON の配列"))


def single(code: int | None, data: Any, message: str) -> tuple[dict | None, str | None]:
    """`status --json --name` の結果。（ラン, 読めない理由）。

    終了コード 1 は「ランが無い」だけでなく、CLI の捕まえていない例外でも返るので、ここでは
    見分けない。ランが消えたかは、一覧にそのラン名があるかで決める（`gone`）。
    """
    if code == 0 and isinstance(data, dict):
        return data, None
    return None, _failure(code, message, "JSON のオブジェクト")


def gone(name: str, found: Listing) -> bool:
    """一覧が読めて、そこにもそのラン名が無い。一覧も読めなければ、消えたとは言えない。"""
    return found.error is None and all(name_of(st) != name for st in found.runs)


def age(stamp: Any, now: dt.datetime) -> float | None:
    """ISO 8601 の時刻から `now` までの秒数。読めなければ None。

    status の時刻は `Z` で終わる。Python 3.10 の `fromisoformat` は `Z` を読めないので置き換える。
    """
    if not isinstance(stamp, str):
        return None
    try:
        at = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.astimezone()
    return (now - at).total_seconds()


def name_of(st: dict) -> str:
    return str(st.get("name") or "?")


def error_of(st: dict) -> str | None:
    """読めないランなら、その理由。読めないランには `name` と `error` しか無い。"""
    error = st.get("error")
    return str(error) if error else None


def run_of(st: dict) -> dict:
    run = st.get("run")
    return run if isinstance(run, dict) else {}


def phase(st: dict) -> str:
    return str(run_of(st).get("phase") or "")


def phase_label(st: dict) -> str:
    value = phase(st)
    return PHASE_LABEL.get(value, value or "?")


def awaiting(st: dict) -> bool:
    return bool(run_of(st).get("awaiting_answer"))


def driver_stopped(st: dict) -> bool:
    """driver が走っているはずのフェーズなのに走っていない。呼び直すまで進まない。

    回答待ちでは、driver が 4 で終えて回答を待つのがいつもの流れなので、止まっているとは言わない。
    `driver_running` の無い古い形でも言わない。
    """
    run = run_of(st)
    return (
        run.get("driver_running") is False
        and not awaiting(st)
        and phase(st) in ("running", "planning", "finishing")
    )


def live_children(st: dict) -> tuple[int, ...]:
    """前の driver が残した、まだ生きている子の pid。"""
    return tuple(number(pid) for pid in items(run_of(st).get("live_children")) if number(pid))


def panic_cause(st: dict) -> str:
    """1 行にした原因。原因には claude の標準エラーがそのまま入ることがあり、改行で行が崩れる。"""
    return " ".join(str(run_of(st).get("panic_cause") or "").split())


def tasks(st: dict) -> list[dict]:
    return dicts(st.get("tasks"))


def implementation(st: dict) -> list[dict]:
    return [t for t in tasks(st) if t.get("kind") == "implementation"]


def questions(st: dict) -> list[dict]:
    return dicts(st.get("questions"))


def executions(task: dict) -> list[dict]:
    return dicts(task.get("executions"))


def escalations(owner: dict) -> list[dict]:
    """ラン（`escalations[]`）かタスク（`tasks[].escalations[]`）の、開いているエスカレーション。"""
    return dicts(owner.get("escalations"))


def flow_of(task: dict) -> dict | None:
    flow = task.get("flow")
    return flow if isinstance(flow, dict) else None


def overview_pr(st: dict) -> int | None:
    stack = st.get("stack")
    overview = stack.get("overview") if isinstance(stack, dict) else None
    pr = overview.get("pr") if isinstance(overview, dict) else None
    return pr if isinstance(pr, int) else None


def task_label(task: dict) -> str:
    """タスクの件名。計画タスクと git 管理タスクには件名が無いので、種類の名前で呼ぶ。"""
    title = task.get("title")
    if title:
        return str(title)
    return {"planning": "計画", "git": "git 管理"}.get(str(task.get("kind")), "")


def running(st: dict, now: dt.datetime) -> list[Running]:
    """走っている実行（タスクの順、タスクの中は始めた順）。書き直す前のフローの実行も含む。"""
    out: list[Running] = []
    for task in tasks(st):
        for execution in executions(task):
            if execution.get("status") != "running":
                continue
            progress = execution.get("progress")
            progress = progress if isinstance(progress, dict) else {}
            out.append(
                Running(
                    task=str(task.get("id") or "?"),
                    stage=str(execution.get("stage") or "?"),
                    round=number(execution.get("round")),
                    seconds=age(execution.get("started_at"), now),
                    turns=number(progress.get("turns")),
                    tool=str(progress.get("lastTool") or ""),
                )
            )
    return out


def shown(st: dict, now: dt.datetime) -> bool:
    """statusline に出すラン。

    読めないランは出さない（古いディレクトリ 1 つで毎回赤字が出る。autodev-watch のリストでだけ見せる）。
    回答待ちとパニックは、人が動くまで進まないので、時間の窓に関わらず出し続ける。
    """
    if error_of(st) or phase(st) == "finished":
        return False
    if awaiting(st) or phase(st) == "panicked":
        return True
    updated = age(st.get("updated_at"), now)
    return updated is not None and updated <= RECENT


def order(statuses: list[dict], now: dt.datetime) -> list[dict]:
    """終えていないランを先に、あとは最後のイベントの新しい順。読めないランは最後。"""

    def key(st: dict) -> tuple[int, float]:
        if error_of(st):
            return (2, 0.0)
        updated = age(st.get("updated_at"), now)
        return (int(phase(st) == "finished"), updated if updated is not None else float("inf"))

    return sorted(statuses, key=key)


def short(seconds: float) -> str:
    """経過時間の表記。1 分未満は秒、1 時間未満は分と秒、それ以上は時と分。"""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"
