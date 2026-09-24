"""`review-new-comments.py` の検査。

フックは transcript（会話の JSONL）の Edit / Write / MultiEdit の入力を見るので、
一時ディレクトリに transcript と編集後のファイルを両方置いてから関数として呼ぶ。
実行はリポジトリのルートから `uv run pytest`。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from conftest import CLAUDE_HOOKS

HOOK_PATH = CLAUDE_HOOKS / "review-new-comments.py"


@pytest.fixture(scope="module")
def hook():
    # ファイル名にハイフンがあるので import 文では読めない。sys.modules に登録してから実行するのは、
    # `from __future__ import annotations` 付きの NamedTuple が自分のモジュールをそこから引くため
    spec = importlib.util.spec_from_file_location("review_new_comments", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def user_says(text: str, *, meta: bool = False) -> dict:
    entry: dict = {"type": "user", "message": {"role": "user", "content": text}}
    if meta:
        entry["isMeta"] = True
    return entry


def tool_result() -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def tool_use(name: str, **inp) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": inp}],
        },
    }


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def wrote(root: Path, name: str, text: str) -> tuple[Path, dict]:
    """ファイルを置き、それを Write で書いたことにする transcript のエントリを返す。"""
    path = write(root, name, text)
    return path, tool_use("Write", file_path=str(path), content=text)


def review(hook, monkeypatch, capsys, root: Path, entries: list[dict], **kw):
    """フックを呼び、Claude に返る本文（無ければ None）を返す。"""
    transcript = root / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    # 重複抑制の記録をテストごとに分ける。/tmp に残すと他のテストの結果に影響する
    monkeypatch.setattr(hook.tempfile, "gettempdir", lambda: str(root / "state"))
    payload = {
        "transcript_path": str(transcript),
        "cwd": str(root),
        "session_id": kw.get("session", "s"),
        "stop_hook_active": kw.get("active", False),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


def test_no_edits_says_nothing(hook, tmp_path, monkeypatch, capsys):
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("hello")]) is None


def test_stop_hook_active_says_nothing(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.py", "# 足したコメント\nx = 1\n")
    entries = [user_says("go"), edit]
    assert review(hook, monkeypatch, capsys, tmp_path, entries, active=True) is None


def test_bash_written_file_is_not_seen(hook, tmp_path, monkeypatch, capsys):
    """Bash 越しの書き換えは tool_input に現れないので取りこぼす。承知の上での穴。"""
    write(tmp_path, "a.py", "# heredoc で書いたコメント\nx = 1\n")
    entries = [user_says("go"), tool_use("Bash", command="cat > a.py <<'EOF'\n# ...\nEOF")]
    assert review(hook, monkeypatch, capsys, tmp_path, entries) is None


def test_file_edited_before_this_turn_is_not_seen(hook, tmp_path, monkeypatch, capsys):
    """前の turn の編集は対象外。直近のユーザー発言で区切る。"""
    path = write(tmp_path, "a.py", "# 前の turn のコメント\nx = 1\n")
    entries = [
        user_says("first"),
        tool_use("Write", file_path=str(path), content="# 前の turn のコメント\nx = 1\n"),
        user_says("second"),
    ]
    assert review(hook, monkeypatch, capsys, tmp_path, entries) is None


def test_tool_results_and_meta_do_not_end_the_turn(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.ts", "// この turn で足した\nconst x = 1;\n")
    entries = [user_says("go"), edit, tool_result(), user_says("差し込み", meta=True)]
    body = review(hook, monkeypatch, capsys, tmp_path, entries)
    assert body is not None
    assert "a.ts:1  この turn で足した" in body


def test_trailing_comment_is_reported(hook, tmp_path, monkeypatch, capsys):
    """コードの後ろに付いたコメントも拾う。行頭が記号の行だけを見ていると取りこぼす。"""
    _, edit = wrote(tmp_path, "a.py", "x = 1  # 1 を入れる\n")
    body = review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit])
    assert body is not None
    assert "a.py:1  1 を入れる" in body


def test_markers_inside_python_string_are_not_comments(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.py", 'QUERY = """\n# これは文字列の中\nSELECT 1\n"""\n')
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit]) is None


def test_markers_inside_template_literal_are_not_comments(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.js", "const s = `\n/* これは文字列の中 */\n`;\n")
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit]) is None


def test_consecutive_line_comments_become_one_row(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.py", "# 1 行目\n# 2 行目\n# 3 行目\nx = 1\n")
    body = review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit])
    assert body is not None
    assert "■ コードのコメント（1 件）" in body
    assert "a.py:1  1 行目（3 行）" in body


def test_docstring_and_rust_doc_comment_go_to_block_section(hook, tmp_path, monkeypatch, capsys):
    _, py = wrote(tmp_path, "a.py", '"""モジュールの説明"""\nx = 1\n')
    _, rs = wrote(tmp_path, "b.rs", "/// doc 1 行目\n/// doc 2 行目\nfn main() {}\n")
    body = review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), py, rs])
    assert body is not None
    assert "■ ブロックコメントと docstring（2 件）" in body
    assert "a.py:1  モジュールの説明（docstring・1 行）" in body
    assert "b.rs:1  doc 1 行目（doc コメント・2 行）" in body


def test_multiline_block_is_reported_once(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.rs", "fn main() {\n    /* 1 行目\n       2 行目 */\n}\n")
    body = review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit])
    assert body is not None
    assert "a.rs:2  1 行目（ブロック・2 行）" in body


def test_only_lines_added_by_edit_are_reported(hook, tmp_path, monkeypatch, capsys):
    path = write(tmp_path, "a.sh", "#!/bin/sh\n# 前からあるコメント\n# 足したコメント\necho 1\n")
    entries = [
        user_says("go"),
        tool_use(
            "Edit",
            file_path=str(path),
            old_string="# 前からあるコメント\necho 1\n",
            new_string="# 前からあるコメント\n# 足したコメント\necho 1\n",
        ),
    ]
    body = review(hook, monkeypatch, capsys, tmp_path, entries)
    assert body is not None
    assert "a.sh:3  足したコメント" in body
    assert "前からあるコメント" not in body


def test_multiedit_inputs_are_collected(hook, tmp_path, monkeypatch, capsys):
    path = write(tmp_path, "a.py", "# 1 つ目\nx = 1\n# 2 つ目\ny = 2\n")
    entries = [
        user_says("go"),
        tool_use(
            "MultiEdit",
            file_path=str(path),
            edits=[
                {"old_string": "x = 1\n", "new_string": "# 1 つ目\nx = 1\n"},
                {"old_string": "y = 2\n", "new_string": "# 2 つ目\ny = 2\n"},
            ],
        ),
    ]
    body = review(hook, monkeypatch, capsys, tmp_path, entries)
    assert body is not None
    assert "■ コードのコメント（2 件）" in body


def test_moved_line_does_not_fire(hook, tmp_path, monkeypatch, capsys):
    """old にも同じ行があるときは発火しない。再インデントやコードの移動で毎回鳴らないため。"""
    path = write(tmp_path, "a.py", "x = 1\n# 動かすコメント\ny = 2\n")
    entries = [
        user_says("go"),
        tool_use(
            "Edit",
            file_path=str(path),
            old_string="# 動かすコメント\nx = 1\n",
            new_string="x = 1\n# 動かすコメント\n",
        ),
    ]
    assert review(hook, monkeypatch, capsys, tmp_path, entries) is None


def test_pragmas_shebang_pep723_and_separators_are_excluded(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(
        tmp_path,
        "a.py",
        "#!/usr/bin/env -S uv run --script\n"
        "# /// script\n"
        '# dependencies = ["pyyaml"]\n'
        "# ///\n"
        "# SPDX-License-Identifier: MIT\n"
        "# ----------------------------\n"
        "# TODO: あとでやる\n"
        "import os  # noqa: F401\n"
        "x: int = 1  # type: ignore[assignment]\n",
    )
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit]) is None


def test_config_files_go_to_config_section(hook, tmp_path, monkeypatch, capsys):
    _, toml = wrote(tmp_path, "c.toml", "# key を有効にする\nkey = true\n")
    _, docker = wrote(tmp_path, "Dockerfile", "# ベースイメージ\nFROM alpine\n")
    body = review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), toml, docker])
    assert body is not None
    assert "■ 設定ファイルのコメント（2 件）" in body
    assert "■ コードのコメント" not in body


def test_markdown_and_unknown_suffix_are_ignored(hook, tmp_path, monkeypatch, capsys):
    _, md = wrote(tmp_path, "README.md", "# 見出し\n// コードではない\n")
    _, unknown = wrote(tmp_path, "data.xyz", "# なにか\n")
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), md, unknown]) is None


def test_skipped_directories_are_ignored(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "node_modules/a.js", "// 依存パッケージのコメント\n")
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit]) is None


def test_missing_file_is_skipped(hook, tmp_path, monkeypatch, capsys):
    entries = [
        user_says("go"),
        tool_use("Write", file_path=str(tmp_path / "gone.py"), content="# x\n"),
    ]
    assert review(hook, monkeypatch, capsys, tmp_path, entries) is None


def test_same_comment_is_reported_once_per_session(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.py", "# 足したコメント\nx = 1\n")
    entries = [user_says("go"), edit]
    assert review(hook, monkeypatch, capsys, tmp_path, entries, session="same") is not None
    assert review(hook, monkeypatch, capsys, tmp_path, entries, session="same") is None
    assert review(hook, monkeypatch, capsys, tmp_path, entries, session="other") is not None


def test_env_var_disables_the_hook(hook, tmp_path, monkeypatch, capsys):
    _, edit = wrote(tmp_path, "a.py", "# 足したコメント\nx = 1\n")
    monkeypatch.setenv("CLAUDE_SKIP_COMMENT_REVIEW", "1")
    assert review(hook, monkeypatch, capsys, tmp_path, [user_says("go"), edit]) is None


def test_warm_only_loads_the_grammar(hook, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["review-new-comments.py", "--warm"])
    assert hook.main() == 0
