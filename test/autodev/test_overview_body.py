"""概要 PR の本文（`app/publish.py` の `write_overview_body` と `summarize`）。

本文はまとめステージが書き、driver はその中のマーカーを state.json の中身に置き換えて、
末尾に署名を足す。**本文は `string.Template` に通さない**——通すと、ステージが書いた `$$` が
`$` になり、`${tasks}` が消える。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from autodevlib.app import publish, stage_call
from autodevlib.app.context import Ctx
from autodevlib.config import paths, stages
from autodevlib.core import prompt
from autodevlib.ports import runner, templates
from conftest import SKILL_ROOT

SIGNATURE = (
    "autodev が作成（ラン名 `demo`、更新 2026-09-25T10:00:00）。マージは人間が `gh stack merge` で\n"
    "下から行う。"
)

TASKS = [
    {
        "id": "task1",
        "status": "stacked",
        "pr": 12,
        "tier": "standard",
        "subject": "土台を作る",
        "reason": None,
    },
    {
        "id": "task2",
        "status": "blocked",
        "pr": None,
        "tier": "light",
        "subject": "本体",
        "reason": "受入条件が定まらない",
    },
]

TASKS_TABLE = (
    "| # | 状態 | PR | 階層 | 内容 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| task1 | スタック済み | #12 | standard | 土台を作る |\n"
    "| task2 | 要確認 | — | light | 本体 |"
)


def ctx(tmp_path, monkeypatch, **over: Any) -> Ctx:
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    run = paths.Run("demo")
    run.ensure()
    st: dict[str, Any] = {
        "name": "demo",
        "updatedAt": "2026-09-25T10:00:00",
        "instruction": "先に型を直す",
        "tasks": [dict(item) for item in TASKS],
        "decisions": [{"body": "ORM を使わない"}],
        "deferrals": [{"body": "移行は後で"}],
        "running": {},
    }
    st.update(over)
    return Ctx(run=run, st=st)


def written(c: Ctx) -> str:
    publish.write_overview_body(c)
    return Path(c.run.overview_pr_body).read_text(encoding="utf-8")


def prose_file(c: Ctx) -> Path:
    return Path(c.run.prose("overview"))


# --- write_overview_body -----------------------------------------------------


def test_本文のマーカーを置き換えて末尾に署名を足す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(
        c.run,
        "overview",
        "> 概要\n\n## タスク\n\n<!-- autodev:tasks -->\n\n## 要対応\n\n<!-- autodev:held -->\n\n"
        "## スコープ外\n\n<!-- autodev:deferrals -->\n\n"
        "<details><summary>判断ログ</summary>\n\n<!-- autodev:decisions -->\n\n</details>",
    )
    out = written(c)
    assert out.startswith(
        f"> 概要\n\n## タスク\n\n{TASKS_TABLE}\n\n"
        "## 要対応\n\n- task2（要確認）: 受入条件が定まらない\n\n"
        "## スコープ外\n\n- 移行は後で\n\n"
        "<details><summary>判断ログ</summary>\n\n- ORM を使わない\n\n</details>"
    )
    assert out.rstrip("\n").endswith(SIGNATURE)
    assert "<!-- autodev:" not in out


def test_中身が無ければ決まった文を差す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch, tasks=[], decisions=None)
    del c.st["deferrals"]
    templates.write_prose(
        c.run,
        "overview",
        "<!-- autodev:tasks -->\n<!-- autodev:held -->\n"
        "<!-- autodev:deferrals -->\n<!-- autodev:decisions -->",
    )
    assert written(c).startswith(
        "タスクはありません。\n要対応はありません。\n"
        "スコープ外にしたものはありません。\n判断ログはありません。"
    )


def test_同じマーカーが2回あれば両方置き換える(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(c.run, "overview", "<!-- autodev:held -->\n\n<!-- autodev:held -->")
    held = "- task2（要確認）: 受入条件が定まらない"
    assert written(c).startswith(f"{held}\n\n{held}")


def test_マーカーが無い本文もそのまま出して署名を足す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(c.run, "overview", "## 概要\n\nマーカーを置かない本文。")
    out = written(c)
    assert out.startswith("## 概要\n\nマーカーを置かない本文。")
    assert out.rstrip("\n").endswith(SIGNATURE)
    assert "| task1 |" not in out  # driver は足さない


def test_本文のドル記号をそのまま書き出す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(
        c.run,
        "overview",
        "費用は $$ で、`$HOME` と `${tasks}` と `${run_name}` を出す。\n\n<!-- autodev:deferrals -->",
    )
    out = written(c)
    assert out.startswith(
        "費用は $$ で、`$HOME` と `${tasks}` と `${run_name}` を出す。\n\n- 移行は後で"
    )


def test_一覧に無いマーカーはそのまま残す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(c.run, "overview", "<!-- autodev:unknown -->")
    assert written(c).startswith("<!-- autodev:unknown -->")


def test_書き出すたびに保存した本文から置き換え直す(tmp_path, monkeypatch):
    """保存した本文を置き換えてしまうと、後から 1 本スタックに追加しても表が古いまま残る。"""
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(c.run, "overview", "<!-- autodev:tasks -->")
    saved = prose_file(c).read_text(encoding="utf-8")
    first = written(c)
    assert "| task2 | 要確認 | — | light | 本体 |" in first

    c.st["tasks"][1].update(status="stacked", pr=13, reason=None)
    second = written(c)
    assert "| task2 | スタック済み | #13 | light | 本体 |" in second
    assert "要確認" not in second
    assert prose_file(c).read_text(encoding="utf-8") == saved
    assert "<!-- autodev:tasks -->" in saved


# --- まとめステージがまだ書いていないとき ------------------------------------------


def test_まとめステージが書く前は最小限の本文になる(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    assert not prose_file(c).exists()
    out = written(c)
    lines = out.splitlines()
    assert lines[0].startswith("> stacked PR の概要 PR。")
    assert not lines[1].startswith(">")  # 引用は 1 行
    assert TASKS_TABLE in out
    assert "- task2（要確認）: 受入条件が定まらない" in out
    assert out.rstrip("\n").endswith(SIGNATURE)
    assert "<!-- autodev:" not in out
    for heading in ("何をする作業か", "起動時の指示", "この PR について"):
        assert heading not in out


def test_最小限の本文は中身が無くても決まった文で埋まる(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch, tasks=[], decisions=[], deferrals=[])
    out = written(c)
    assert out.startswith("> stacked PR の概要 PR。")
    assert "タスクはありません。" in out
    assert "要対応はありません。" in out
    assert out.rstrip("\n").endswith(SIGNATURE)
    assert "<!-- autodev:" not in out


# --- summarize ---------------------------------------------------------------


def result(got: dict[str, Any] | None, *, ok: bool = True) -> runner.Result:
    return runner.Result(
        stage="summary",
        code=0 if ok else 1,
        session_id=None,
        text="",
        usage={},
        reason="success" if ok else "error_during_execution",
        log="",
        result=got,
        error=None if ok else "落ちた",
    )


def summarize_with(c: Ctx, monkeypatch, got: runner.Result) -> None:
    monkeypatch.setattr(publish, "call", lambda *args, **kwargs: got)
    publish.summarize(c, "0")


def test_まとめステージの本文をマーカー入りのまま保存する(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    body = "\n\n> 概要 $$\n\n## タスク\n\n<!-- autodev:tasks -->\n\n"
    summarize_with(c, monkeypatch, result({"body": body}))
    assert prose_file(c).read_text(encoding="utf-8") == (
        "> 概要 $$\n\n## タスク\n\n<!-- autodev:tasks -->\n"
    )
    assert written(c).startswith(f"> 概要 $$\n\n## タスク\n\n{TASKS_TABLE}")


@pytest.mark.parametrize(
    "got",
    [
        result({"body": ""}),
        result({"body": "  \n "}),
        result({"prose": "古いキー"}),
        result(None),
        result({"body": "<!-- autodev:tasks -->"}, ok=False),
    ],
    ids=["空", "空白だけ", "bodyが無い", "結果が無い", "ステージ失敗"],
)
def test_本文が無いか失敗したら保存した本文を変えない(tmp_path, monkeypatch, got):
    c = ctx(tmp_path, monkeypatch)
    templates.write_prose(c.run, "overview", "前の本文 <!-- autodev:held -->")
    before = prose_file(c).read_text(encoding="utf-8")
    summarize_with(c, monkeypatch, got)
    assert prose_file(c).read_text(encoding="utf-8") == before


def test_まとめステージのスキーマはbodyだけを持つ():
    schema = json.loads((SKILL_ROOT / "schemas" / "summary.json").read_text(encoding="utf-8"))
    assert schema["required"] == ["body"]
    assert set(schema["properties"]) == {"body"}
    assert schema["properties"]["body"]["type"] == "string"


# --- まとめステージに渡す値 ---------------------------------------------------------


def test_まとめステージにはスタック済みのタスクPR本文を渡す(tmp_path, monkeypatch):
    c = ctx(
        tmp_path,
        monkeypatch,
        tasks=[
            {"id": "task1", "status": "stacked"},
            {"id": "task2", "status": "blocked"},
            {"id": "task3", "status": "stacked"},
            {"id": "task4", "status": "pending"},
        ],
    )
    got = stage_call.stage_values(c, stages.TABLE["summary"], None, "1", "")
    assert got["task_pr_bodies"] == [c.run.task_pr_body("task1"), c.run.task_pr_body("task3")]
    assert got["instruction"] == "先に型を直す"


def test_スタック済みが無ければ空の一覧を渡す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch, tasks=[{"id": "task1", "status": "pending"}])
    got = stage_call.stage_values(c, stages.TABLE["summary"], None, "0", "")
    assert got["task_pr_bodies"] == []


def test_まとめ以外のステージにはタスクPR本文を渡さない(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    got = stage_call.stage_values(c, stages.TABLE["plan"], None, "0", "")
    assert got.get("task_pr_bodies") is None


def stage_prompt(c: Ctx, name: str, task: dict[str, Any] | None = None) -> str:
    stage = stages.TABLE[name]
    return prompt.build_prompt(
        stage,
        stage_call.stage_values(c, stage, task, "1", ""),
        template=templates.template("prompt"),
        contract_path=paths.contract(stage.contract),
        launcher_path=paths.launcher(),
    )


IMPL_TASK = {
    "id": "task1",
    "tier": "standard",
    "subject": "土台を作る",
    "dod": "検証コマンドが緑",
    "acceptance": "A が B になる",
    "scope": "core/",
    "entrypoints": "core/x.py",
    "contracts": "",
    "branch": "stack/demo--task-1",
    "parent": "stack/demo--task-0",
}


def test_まとめステージの表にツリーのbaseを出す(tmp_path, monkeypatch):
    """指示書の `git diff <ツリー の base>..HEAD` に値が付かないと、ステージは base を推測する。"""
    c = ctx(tmp_path, monkeypatch, base="main")
    assert "| `<ツリー の base>` | `main` |" in stage_prompt(c, "summary")


def test_まとめ以外のステージの表にはツリーのbaseを出さない(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch, base="main")
    assert "<ツリー の base>" not in stage_prompt(c, "plan")
    assert "<ツリー の base>" not in stage_prompt(c, "impl", IMPL_TASK)


# --- テンプレートと文書 ------------------------------------------------------------

MARKERS = (
    "<!-- autodev:tasks -->",
    "<!-- autodev:held -->",
    "<!-- autodev:deferrals -->",
    "<!-- autodev:decisions -->",
)


def read(*parts: str) -> str:
    return SKILL_ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_最小限の本文はテンプレートにある():
    text = read("templates", "overview-pr-body-minimal.md")
    assert text.lstrip().startswith("> stacked PR の概要 PR。")
    assert "<!-- autodev:tasks -->" in text
    assert "<!-- autodev:held -->" in text


def test_まとめステージの指示書はプレースホルダ表と同じ名前を使う():
    text = read("contracts", "summary.md")
    for name in ("<ブリーフ>", "<コードマップ>", "<ツリー の base>", "<タスク PR 本文>"):
        assert name in text
    for marker in MARKERS:
        assert marker in text
    assert "`body`" in text
    assert "prose" not in text
