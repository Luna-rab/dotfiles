"""PR 本文と brief に差す塊の組み立て（`core/markdown.py`）。

**進行状態の唯一の出所は state.json である。** ここで組んだ塊が空文字になると、PR 本文の
節が見出しだけ残る（または見出しごと消える）ので、空のときの文言も字面で確かめる。
"""

from __future__ import annotations

from typing import Any

from autodevlib.core import markdown


def state(*tasks: dict[str, Any], **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"tasks": list(tasks)}
    data.update(over)
    return data


def task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "task1",
        "status": "stacked",
        "pr": 12,
        "tier": "standard",
        "subject": "土台を作る",
        "reason": None,
    }
    base.update(over)
    return base


# --- タスクの一覧 ------------------------------------------------------------


def test_タスクの一覧を表にする():
    body = markdown.tasks_block(
        state(
            task(),
            task(id="task2", status="pending", pr=None, tier="light", subject="本体を書く"),
        )
    )
    assert body.splitlines() == [
        "| # | 状態 | PR | 階層 | 内容 |",
        "| --- | --- | --- | --- | --- |",
        "| task1 | スタック済み | #12 | standard | 土台を作る |",
        "| task2 | 未着手 | — | light | 本体を書く |",
    ]


def test_タスクを割る前はまだ割っていないと出す():
    assert markdown.tasks_block(state()) == "まだ割っていない。"
    assert markdown.tasks_block({}) == "まだ割っていない。"


def test_知らない状態はそのまま出す():
    """`STATUS_LABEL` に無い状態を出さずに隠すと、表から 1 行消える。"""
    body = markdown.tasks_block(state(task(status="unknown")))
    assert "| task1 | unknown |" in body


# --- 要対応 ------------------------------------------------------------------


def test_要確認と失敗だけを要対応に挙げる():
    body = markdown.held_block(
        state(
            task(),
            task(id="task2", status="blocked", reason="受入条件が定まらない"),
            task(id="task3", status="failed", reason=None),
        )
    )
    assert body == (
        "## 要対応\n\n- task2（要確認）: 受入条件が定まらない\n- task3（失敗）: 理由の記録なし\n\n"
    )


def test_要対応が無ければ節ごと出さない():
    assert markdown.held_block(state(task())) == ""
    assert markdown.held_block(state()) == ""


# --- 決定と持ち越し ----------------------------------------------------------


def test_決定を箇条書きにする():
    data = state(decisions=[{"body": "ORM を使わない"}, {"body": "移行は 2 ステージで"}])
    assert markdown.entries_block(data, "decisions", "決めたこと") == (
        "## 決めたこと\n\n- ORM を使わない\n- 移行は 2 ステージで\n\n"
    )


def test_中身が無ければ節ごと出さない():
    assert markdown.entries_block(state(), "decisions", "決めたこと") == ""
    assert markdown.entries_block(state(decisions=[]), "decisions", "決めたこと") == ""
    assert markdown.entries_block(state(decisions=None), "decisions", "決めたこと") == ""


# --- 箇条書き ----------------------------------------------------------------


def test_値をコード記法の箇条書きにする():
    assert markdown.bullets(["uv run ruff check .", "uv run pytest -q"], "無し") == (
        "- `uv run ruff check .`\n- `uv run pytest -q`"
    )


def test_空のときは渡した文言を出す():
    """検証コマンドが 0 本のランでは、空行ではなくその旨を brief に出す。"""
    assert markdown.bullets([], "設定されていない。") == "設定されていない。"
