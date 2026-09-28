"""タスク PR の本文（`app/publish.py` の `publish`）。

PR 本文ステージが本文を返さなかったときも、指示書の構成と同じく「## 何が変わるか」から始める。
"""

from __future__ import annotations

from typing import Any

import pytest
from autodevlib.app import publish
from autodevlib.app.context import Ctx
from autodevlib.config import paths
from autodevlib.ports import proc, runner

TASK: dict[str, Any] = {
    "id": "task2",
    "subject": "指示書を書き直す",
    "dod": "構成が新しくなる",
    "branch": "stack/demo--task-2",
    "parent": "stack/demo--task-1",
}


def publish_until_push(tmp_path, monkeypatch, got: runner.Result) -> str:
    """本文を書き出したところで push を失敗させ、書き出した本文を返す（gh も git も呼ばない）。"""
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    run = paths.Run("demo")
    run.ensure()
    c = Ctx(
        run=run,
        st={"name": "demo", "overviewPr": 7, "tasks": [dict(TASK)], "running": {}},
    )
    monkeypatch.setattr(publish, "call", lambda *args, **kwargs: got)
    monkeypatch.setattr(
        publish.repo, "push", lambda *args, **kwargs: proc.Run(code=1, out="", err="止める")
    )
    with pytest.raises(SystemExit):
        publish.publish(c, dict(TASK))
    return open(run.task_pr_body("task2"), encoding="utf-8").read()


def test_スタックに追加したら続けて再計画した回数を数え直す(tmp_path, monkeypatch):
    """数え直さないと、進んでいるランでも再計画 2 回で人に聞いて止まる。"""
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    run = paths.Run("demo")
    run.ensure()
    c = Ctx(
        run=run,
        st={
            "name": "demo",
            "overviewPr": 7,
            "tasks": [{**TASK, "status": "running", "tier": "standard"}],
            "running": {},
            "decisions": [],
            "deferrals": [],
            "replansSinceStack": 2,
        },
    )
    ok = proc.Run(code=0, out="", err="")
    monkeypatch.setattr(publish, "call", lambda *a, **k: stage_result({"body": "本文"}))
    monkeypatch.setattr(publish.repo, "push", lambda *a, **k: ok)
    monkeypatch.setattr(publish.forge, "pr_create", lambda *a, **k: (12, ok))
    monkeypatch.setattr(publish.forge, "stack_link", lambda *a, **k: ok)
    monkeypatch.setattr(publish.forge, "pr_edit", lambda *a, **k: ok)
    publish.publish(c, dict(TASK))
    assert c.st["tasks"][0]["status"] == "stacked"
    assert c.st["replansSinceStack"] == 0


def stage_result(got: dict[str, Any] | None, *, ok: bool = True) -> runner.Result:
    return runner.Result(
        stage="pr-body",
        code=0 if ok else 1,
        session_id=None,
        text="",
        usage={},
        reason="success" if ok else "error_during_execution",
        log="",
        result=got,
        error=None if ok else "落ちた",
    )


@pytest.mark.parametrize(
    "got",
    [stage_result({"body": "  \n"}), stage_result(None, ok=False)],
    ids=["空の本文", "ステージが落ちた"],
)
def test_本文が返らないときの最小限の本文は何が変わるかから始まる(tmp_path, monkeypatch, got):
    text = publish_until_push(tmp_path, monkeypatch, got)
    assert text == (
        "> stacked PR の task2。全体の計画と進行は概要 PR #7 にある。"
        "**下から順にレビューし、下から順にマージする。**\n"
        "\n"
        "## 何が変わるか\n\n指示書を書き直す\n\n## DoD\n\n構成が新しくなる\n"
    )
