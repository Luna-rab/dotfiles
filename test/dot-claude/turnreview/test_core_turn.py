"""`turnreview.core.turn` と `turnreview.core.report` を、ファイルを置かずに確かめる。"""

from __future__ import annotations

from pathlib import Path

from turnreview.core.report import SUBAGENT_RULE, hook_output, notes_for, report_key
from turnreview.core.turn import added_lines_by_file, edits_in_last_turn, transcript_field


def said(text: str, *, meta: bool = False) -> dict:
    return {"type": "user", "isMeta": meta, "message": {"content": text}}


def wrote(path: str, content: str) -> dict:
    block = {"type": "tool_use", "name": "Write", "input": {"file_path": path, "content": content}}
    return {"type": "assistant", "message": {"content": [block]}}


def test_人の発言で区切り_差し込みでは区切らない():
    entries = [said("1"), wrote("/a", "x"), said("2"), wrote("/b", "y"), said("注入", meta=True)]
    assert [edit["file_path"] for edit in edits_in_last_turn(entries)] == ["/b"]


def test_相対パスはcwdから引き_scratchpadは外す():
    tmp = Path("/tmp")
    edits = [
        {"name": "Write", "file_path": "src/a.py", "content": "x = 1\n"},
        {"name": "Write", "file_path": "/tmp/claude-1/s/scratchpad/b.py", "content": "y\n"},
        # scratchpad という名前でも一時ディレクトリの外なら見る（autodev の worktree など）
        {"name": "Write", "file_path": "/work/scratchpad/c.py", "content": "z\n"},
    ]
    added = added_lines_by_file(edits, Path("/repo"), [tmp])
    assert set(added) == {Path("/repo/src/a.py"), Path("/work/scratchpad/c.py")}


def test_サブエージェントは自分のtranscriptを読む():
    assert transcript_field({"hook_event_name": "Stop"}) == "transcript_path"
    assert transcript_field({"hook_event_name": "SubagentStop"}) == "agent_transcript_path"


def test_サブエージェントにだけ元の報告の書き直しを求める():
    assert notes_for({"hook_event_name": "Stop"}) == ()
    assert notes_for({"hook_event_name": "SubagentStop"}) == (SUBAGENT_RULE,)
    sub = hook_output({"hook_event_name": "SubagentStop"}, "本文")
    assert sub["hookSpecificOutput"] == {
        "hookEventName": "SubagentStop",
        "additionalContext": "本文",
    }


def test_報告済みの鍵は本文が変われば変わる():
    assert report_key("line", "a.py", "# x") == report_key("line", "a.py", "# x")
    assert report_key("line", "a.py", "# x") != report_key("line", "a.py", "# y")
