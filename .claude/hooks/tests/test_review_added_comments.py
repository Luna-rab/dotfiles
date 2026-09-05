"""`review-added-comments.py` の検査。

合成した transcript（会話の JSONL）と一時ファイルを使い、フックを関数として呼ぶ。
実行はリポジトリのルートから `uv run pytest`。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[1] / "review-added-comments.py"


@pytest.fixture(scope="module")
def hook():
    # ファイル名にハイフンがあるので import 文では読めない。sys.modules に登録してから実行するのは、
    # `from __future__ import annotations` 付きの dataclass が自分のモジュールをそこから引くため
    spec = importlib.util.spec_from_file_location("review_added_comments", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def user_entry(text: str, *, meta: bool = False) -> dict:
    entry = {"type": "user", "message": {"role": "user", "content": text}}
    if meta:
        entry["isMeta"] = True
    return entry


def tool_result_entry() -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def tool_use_entry(name: str, **inp) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": inp}],
        },
    }


def write_transcript(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def run_hook(
    hook, monkeypatch, capsys, transcript: Path, cwd: Path, *, active: bool = False
) -> dict | None:
    payload = {"transcript_path": str(transcript), "cwd": str(cwd), "stop_hook_active": active}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def test_no_edits_in_turn_allows_stop(hook, tmp_path, monkeypatch, capsys):
    transcript = write_transcript(tmp_path, [user_entry("hello")])
    assert run_hook(hook, monkeypatch, capsys, transcript, tmp_path) is None


def test_stop_hook_active_allows_stop_even_with_comments(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.py"
    target.write_text("# added\nx = 1\n", encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [
            user_entry("go"),
            tool_use_entry("Write", file_path=str(target), content="# added\nx = 1\n"),
        ],
    )
    assert run_hook(hook, monkeypatch, capsys, transcript, tmp_path, active=True) is None


def test_python_docstring_and_trailing_comment_are_listed(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.py"
    content = (
        '"""module doc"""\n'
        "\n"
        "\n"
        "def f():\n"
        '    """returns one"""\n'
        '    url = "https://example.com"  # not a comment inside the string, but this is\n'
        "    return 1\n"
    )
    target.write_text(content, encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [user_entry("go"), tool_use_entry("Write", file_path=str(target), content=content)],
    )
    result = run_hook(hook, monkeypatch, capsys, transcript, tmp_path)
    assert result is not None and result["decision"] == "block"
    reason = result["reason"]
    assert "3 件" in reason
    assert 'a.py:1: """module doc"""' in reason
    assert 'a.py:5: """returns one"""' in reason
    assert "a.py:6: # not a comment inside the string, but this is" in reason


def test_only_lines_added_by_edit_are_listed(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.sh"
    after = "#!/bin/sh\n# old comment\n# new comment\necho 1\n"
    target.write_text(after, encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [
            user_entry("go"),
            tool_use_entry(
                "Edit",
                file_path=str(target),
                old_string="# old comment\necho 1\n",
                new_string="# old comment\n# new comment\necho 1\n",
            ),
        ],
    )
    result = run_hook(hook, monkeypatch, capsys, transcript, tmp_path)
    assert result is not None
    assert "a.sh:3: # new comment" in result["reason"]
    assert "old comment" not in result["reason"]
    assert "#!/bin/sh" not in result["reason"]


def test_pragmas_pep723_and_license_are_excluded(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.py"
    content = (
        "#!/usr/bin/env -S uv run --script\n"
        "# /// script\n"
        '# dependencies = ["pyyaml"]\n'
        "# ///\n"
        "# SPDX-License-Identifier: MIT\n"
        "import os  # noqa: F401\n"
        "x: int = 1  # type: ignore[assignment]\n"
    )
    target.write_text(content, encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [user_entry("go"), tool_use_entry("Write", file_path=str(target), content=content)],
    )
    assert run_hook(hook, monkeypatch, capsys, transcript, tmp_path) is None


def test_markdown_and_unknown_suffix_are_ignored(hook, tmp_path, monkeypatch, capsys):
    md = tmp_path / "README.md"
    md.write_text("# heading\n// not code\n", encoding="utf-8")
    unknown = tmp_path / "data.xyz"
    unknown.write_text("# something\n", encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [
            user_entry("go"),
            tool_use_entry("Write", file_path=str(md), content="# heading\n// not code\n"),
            tool_use_entry("Write", file_path=str(unknown), content="# something\n"),
        ],
    )
    assert run_hook(hook, monkeypatch, capsys, transcript, tmp_path) is None


def test_turn_boundary_ignores_tool_results_and_meta(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.ts"
    content = "// added in turn\nconst x = 1;\n"
    target.write_text(content, encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [
            user_entry("first turn"),
            tool_use_entry("Write", file_path=str(target), content="// from an earlier turn\n"),
            user_entry("second turn"),
            tool_use_entry("Write", file_path=str(target), content=content),
            tool_result_entry(),
            user_entry("injected", meta=True),
        ],
    )
    result = run_hook(hook, monkeypatch, capsys, transcript, tmp_path)
    assert result is not None
    assert "a.ts:1: // added in turn" in result["reason"]
    assert "earlier turn" not in result["reason"]


def test_block_comment_spans_are_reported_once(hook, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.rs"
    content = "/* first line\n   second line */\nfn main() {}\n"
    target.write_text(content, encoding="utf-8")
    transcript = write_transcript(
        tmp_path,
        [user_entry("go"), tool_use_entry("Write", file_path=str(target), content=content)],
    )
    result = run_hook(hook, monkeypatch, capsys, transcript, tmp_path)
    assert result is not None
    assert "1 件" in result["reason"]
    assert "a.rs:1: /* first line (〜2 行)" in result["reason"]


def test_missing_file_is_skipped(hook, tmp_path, monkeypatch, capsys):
    transcript = write_transcript(
        tmp_path,
        [
            user_entry("go"),
            tool_use_entry("Write", file_path=str(tmp_path / "gone.py"), content="# x\n"),
        ],
    )
    assert run_hook(hook, monkeypatch, capsys, transcript, tmp_path) is None
