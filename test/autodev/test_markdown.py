"""PR 本文と brief に差す塊の組み立て（`core/markdown.py`）。

**進行状態の唯一の出所は state.json である。** 概要 PR の本文はまとめステージが書き、
その中のマーカーを driver が置き換える。マーカーの位置には見出しがあるので、空のときの文言も
字面で確かめる。
"""

from __future__ import annotations

from typing import Any

import pytest
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


def test_タスクが無ければその旨を出す():
    assert markdown.tasks_block(state()) == "タスクはありません。"
    assert markdown.tasks_block({}) == "タスクはありません。"


def test_知らない状態はそのまま出す():
    """`STATUS_LABEL` に無い状態を出さずに隠すと、表から 1 行消える。"""
    body = markdown.tasks_block(state(task(status="unknown")))
    assert "| task1 | unknown |" in body


def test_タスクの一覧に見出しを付けない():
    """見出しはまとめステージが本文に書く。driver が足すと 2 つ並ぶ。"""
    body = markdown.tasks_block(state(task()))
    assert body.startswith("| # | 状態 |")


# --- 要対応 ------------------------------------------------------------------


def test_要確認と失敗だけを要対応に挙げる():
    """見出しも末尾の空行も付けない。差すのは中身だけ。"""
    body = markdown.held_block(
        state(
            task(),
            task(id="task2", status="blocked", reason="受入条件が定まらない"),
            task(id="task3", status="failed", reason=None),
        )
    )
    assert body == "- task2（要確認）: 受入条件が定まらない\n- task3（失敗）: 理由の記録なし"


def test_要対応が無ければその旨を出す():
    assert markdown.held_block(state(task())) == "要対応はありません。"
    assert markdown.held_block(state()) == "要対応はありません。"
    assert markdown.held_block({}) == "要対応はありません。"


# --- マーカーの置き換え ------------------------------------------------------

FULL: dict[str, Any] = {
    "tasks": [
        task(),
        task(
            id="task2",
            status="blocked",
            pr=None,
            tier="light",
            subject="本体",
            reason="受入条件が定まらない",
        ),
    ],
    "decisions": [{"body": "ORM を使わない"}],
    "deferrals": [{"body": "移行は後で"}],
}

TASKS_TABLE = (
    "| # | 状態 | PR | 階層 | 内容 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| task1 | スタック済み | #12 | standard | 土台を作る |\n"
    "| task2 | 要確認 | — | light | 本体 |"
)


def test_タスクのマーカーを表に置き換える():
    assert markdown.fill_markers("<!-- autodev:tasks -->", FULL) == TASKS_TABLE


def test_要対応のマーカーを箇条書きに置き換える():
    assert (
        markdown.fill_markers("<!-- autodev:held -->", FULL)
        == "- task2（要確認）: 受入条件が定まらない"
    )


def test_要対応は理由が無ければ理由の記録なしと書き失敗も挙げる():
    data = state(
        task(id="task1", status="blocked", reason=None),
        task(id="task2", status="failed", reason="テストが落ちる"),
        task(id="task3", status="dropped", reason="不要"),
    )
    assert markdown.fill_markers("<!-- autodev:held -->", data) == (
        "- task1（要確認）: 理由の記録なし\n- task2（失敗）: テストが落ちる"
    )


def test_スコープ外のマーカーを箇条書きに置き換える():
    assert markdown.fill_markers("<!-- autodev:deferrals -->", FULL) == "- 移行は後で"


def test_判断ログのマーカーを箇条書きに置き換える():
    assert markdown.fill_markers("<!-- autodev:decisions -->", FULL) == "- ORM を使わない"


def test_複数の判断は1行ずつ並べる():
    data = state(decisions=[{"body": "ORM を使わない"}, {"body": "移行は 2 ステージで"}])
    assert markdown.fill_markers("<!-- autodev:decisions -->", data) == (
        "- ORM を使わない\n- 移行は 2 ステージで"
    )


def test_4つのマーカーを1つの本文の中で置き換える():
    body = (
        "## タスク\n\n<!-- autodev:tasks -->\n\n"
        "## 要対応\n\n<!-- autodev:held -->\n\n"
        "## スコープ外\n\n<!-- autodev:deferrals -->\n\n"
        "<details><summary>判断ログ</summary>\n\n<!-- autodev:decisions -->\n\n</details>\n"
    )
    assert markdown.fill_markers(body, FULL) == (
        f"## タスク\n\n{TASKS_TABLE}\n\n"
        "## 要対応\n\n- task2（要確認）: 受入条件が定まらない\n\n"
        "## スコープ外\n\n- 移行は後で\n\n"
        "<details><summary>判断ログ</summary>\n\n- ORM を使わない\n\n</details>\n"
    )


EMPTY_TEXT = {
    "<!-- autodev:tasks -->": "タスクはありません。",
    "<!-- autodev:held -->": "要対応はありません。",
    "<!-- autodev:deferrals -->": "スコープ外にしたものはありません。",
    "<!-- autodev:decisions -->": "判断ログはありません。",
}


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"tasks": [], "decisions": [], "deferrals": []},
        {"tasks": [], "decisions": None},
    ],
    ids=["空の状態", "空の一覧", "Noneと欠落"],
)
@pytest.mark.parametrize("marker", sorted(EMPTY_TEXT))
def test_中身が無ければ決まった文に置き換える(marker: str, data: dict[str, Any]):
    assert markdown.fill_markers(marker, data) == EMPTY_TEXT[marker]


def test_止まったタスクが無ければ要対応はありませんと出す():
    data = state(task(), task(id="task2", status="pending", pr=None))
    assert markdown.fill_markers("<!-- autodev:held -->", data) == "要対応はありません。"


def test_同じマーカーが2回あれば両方置き換える():
    body = "上\n<!-- autodev:deferrals -->\n中\n  <!-- autodev:deferrals -->  \n下"
    assert markdown.fill_markers(body, FULL) == "上\n- 移行は後で\n中\n- 移行は後で\n下"


def test_行の途中にあるマーカーは置き換えない():
    """起動時の指示や本文がマーカーを説明している箇所。置き換えると表のセルに表が入る。"""
    body = (
        "| `<!-- autodev:tasks -->` | タスク表 |\n"
        "マーカー <!-- autodev:held --> を置く。\n"
        "<!-- autodev:deferrals -->"
    )
    assert markdown.fill_markers(body, FULL) == (
        "| `<!-- autodev:tasks -->` | タスク表 |\nマーカー <!-- autodev:held --> を置く。\n- 移行は後で"
    )


def test_マーカーが無い本文はそのまま返す():
    body = "## 概要\n\n何も差さない。\n"
    assert markdown.fill_markers(body, FULL) == body
    assert markdown.fill_markers("", FULL) == ""


def test_本文のドル記号を変えない():
    """`string.Template` を通すと `$$` が `$` になり `${tasks}` が消える。"""
    body = "費用は $$ で、`$HOME` と `${tasks}` と `${run_name}` をそのまま出す。"
    assert markdown.fill_markers(body, FULL) == body
    mixed = f"{body}\n<!-- autodev:decisions -->"
    assert markdown.fill_markers(mixed, FULL) == f"{body}\n- ORM を使わない"


def test_一覧に無いマーカーはそのまま残す():
    body = "<!-- autodev:unknown -->\n<!-- autodev:deferrals -->"
    assert markdown.fill_markers(body, FULL) == "<!-- autodev:unknown -->\n- 移行は後で"


def test_差した中身にあるマーカー文字列は置き換えない():
    """マーカー自体を扱うランでは、subject や判断ログにマーカーの文字列が出る。
    差した中身まで読み直すと state.json の文面が本文で書き換わり、結果が MARKERS の並び順で変わる。
    """
    data = state(
        task(subject="<!-- autodev:held --> \\1 $$ を置く"),
        task(
            id="task2",
            status="failed",
            pr=None,
            tier="light",
            subject="<!-- autodev:deferrals -->",
            reason="<!-- autodev:tasks --> が残る",
        ),
        decisions=[{"body": "<!-- autodev:decisions --> と <!-- autodev:tasks --> を使う"}],
        deferrals=[{"body": "<!-- autodev:held --> は後で"}],
    )
    body = (
        "<!-- autodev:tasks -->\n<!-- autodev:held -->\n"
        "<!-- autodev:deferrals -->\n<!-- autodev:decisions -->"
    )
    assert markdown.fill_markers(body, data) == (
        "| # | 状態 | PR | 階層 | 内容 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| task1 | スタック済み | #12 | standard | <!-- autodev:held --> \\1 $$ を置く |\n"
        "| task2 | 失敗 | — | light | <!-- autodev:deferrals --> |\n"
        "- task2（失敗）: <!-- autodev:tasks --> が残る\n"
        "- <!-- autodev:held --> は後で\n"
        "- <!-- autodev:decisions --> と <!-- autodev:tasks --> を使う"
    )


# --- 箇条書き ----------------------------------------------------------------


def test_値をコード記法の箇条書きにする():
    assert markdown.bullets(["uv run ruff check .", "uv run pytest -q"], "無し") == (
        "- `uv run ruff check .`\n- `uv run pytest -q`"
    )


def test_空のときは渡した文言を出す():
    """検証コマンドが 0 本のランでは、空行ではなくその旨を brief に出す。"""
    assert markdown.bullets([], "設定されていない。") == "設定されていない。"
