"""段へ渡す文面の組み立て（`core/prompt.py`）。

マーカーが埋まらないと、段は `${tree}` のような文字列をそのままパスとして扱う。
`safe_substitute` は例外を投げないので、**埋め忘れはこの検査でしか出ない。**
"""

from __future__ import annotations

import functools
from typing import Any

import pytest
from autodevlib.config import stages
from autodevlib.core import prompt
from conftest import SKILL_ROOT

CONTRACT = "/skills/autodev/contracts/implementation.md"
LAUNCHER = "/skills/autodev/scripts/autodev.py"


@functools.cache
def loaded() -> str:
    """`templates/prompt.md` の本文。

    **読むのは検査の中である。** import の時点で読むと、ファイルが見つからないときに
    この 1 ファイルだけでなく全部の検査が走らなくなる。
    """
    return (SKILL_ROOT / "templates" / "prompt.md").read_text(encoding="utf-8")


def values(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "work": "demo",
        "tree": "/state/autodev/demo/tree",
        "brief": "/state/autodev/demo/brief.md",
        "map": "/state/autodev/demo/map.md",
        "task_id": "task1",
        "tier": "standard",
        "subject": "土台を作る",
        "dod": "検証コマンドが緑",
        "acceptance": "A が B になる",
        "round": "1",
        "parent": "main",
        "branch": "stack/demo--task-1",
        "review": "/state/autodev/demo/tasks/task1/review.json",
    }
    base.update(over)
    return base


def build(stage: stages.Stage, **over: Any) -> str:
    return prompt.build_prompt(
        stage,
        values(**over),
        template=loaded(),
        contract_path=CONTRACT,
        launcher_path=LAUNCHER,
    )


# --- マーカーが残らないこと --------------------------------------------------


def test_マーカーを全部埋める():
    """`${` が 1 つ残ると、段がそれをパスやブランチ名として扱う。"""
    body = build(stages.TABLE["impl"])
    assert "${" not in body
    assert "$" not in body


def test_契約と前提と地図のパスを渡したまま出す():
    body = build(stages.TABLE["impl"])
    assert CONTRACT in body
    assert "/state/autodev/demo/brief.md" in body
    assert "/state/autodev/demo/map.md" in body


def test_読み替え表に起動用のパスを入れる():
    """段は `autodev review …` を絶対パスで呼ぶ。PATH に頼らない。"""
    body = build(stages.TABLE["review:normal"])
    assert f"| `<autodev>` | `{LAUNCHER}` |" in body
    assert "| `<ツリー>` | `/state/autodev/demo/tree` |" in body


def test_タスクの無い段はこのタスクの節を出さない():
    body = build(stages.TABLE["plan"], task_id=None)
    assert "## このタスク" not in body
    assert "${" not in body


def test_タスクのある段は番号と受入条件を出す():
    body = build(stages.TABLE["impl"])
    assert "- 番号: `task1`（リスク階層 `standard`）" in body
    assert "- 受入条件: A が B になる" in body


def test_指示が無ければ節ごと出さない():
    assert "## 指示" not in build(stages.TABLE["impl"])
    assert "## 指示（起動時に人間が渡したもの）" in build(
        stages.TABLE["impl"], instruction=" 先に型を直す "
    )


def test_埋め忘れたマーカーはそのまま残る():
    """`safe_substitute` なので例外で落ちない。段は `${…}` をパスとして扱って落ちる。

    埋め忘れを見つけるのは `test_マーカーを全部埋める` で、ここは落ち方を決める。
    """
    body = prompt.build_prompt(
        stages.TABLE["impl"],
        values(),
        template="頭 ${未知のマーカー} 尾",
        contract_path=CONTRACT,
        launcher_path=LAUNCHER,
    )
    assert body == "頭 ${未知のマーカー} 尾"


def test_値にドル記号が入っても崩れない():
    """`safe_substitute` は 1 度しか置換しない。埋めた値の中の `$` は再展開されない。"""
    body = build(stages.TABLE["impl"], extra="`$HOME` と `${work}` をそのまま出す")
    assert "`$HOME` と `${work}` をそのまま出す" in body


# --- 不変条件 ----------------------------------------------------------------


def test_共通の不変条件はどの段にも入る():
    for stage in stages.TABLE.values():
        text = prompt.system_append(stage)
        assert "PR を作らない" in text
        assert "push しない" in text


def test_実装段はテストを変更しないことを含む():
    text = prompt.system_append(stages.TABLE["impl"])
    assert "テストファイルを変更しない" in text
    assert "StructuredOutput" in text  # 結果を返す段


def test_修正段は実装と同じ不変条件で走る():
    """`fix` の役割の鍵は `impl` である。ここがずれると修正段だけ網が外れる。"""
    assert prompt.INVARIANTS[prompt.ROLE_KEY["fix"]] == prompt.INVARIANTS["impl"]


def test_レビュー段はdoneを必ず呼ぶことを含む():
    text = prompt.system_append(stages.TABLE["review:adversarial"])
    assert "review done` を必ず呼ぶ" in text
    assert "StructuredOutput" not in text  # 結果を返さない段


def test_裁定段はopenの全件に決着を付けることを含む():
    assert "open の全件に決着を付ける" in prompt.system_append(stages.TABLE["judge"])


def test_段の役割を名乗り次の段を呼ばないと書く():
    text = prompt.system_append(stages.TABLE["plan"])
    assert text.startswith("あなたは autodev の **計画** の段である。")
    assert "次の段を自分で呼ばない" in text


# --- 段の表の全段 ------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(stages.TABLE))
def test_全段で文面が組める(name: str):
    """`config/stages.py` に段を足して `ROLE_KEY` を足し忘れると `KeyError` で落ちる。

    段の起動時に落ちると run が 1 本無駄になるので、ここで落とす。
    """
    stage = stages.TABLE[name]
    assert stage.role in prompt.system_append(stage)
    body = build(stage)
    assert "${" not in body
    assert stage.role in body
