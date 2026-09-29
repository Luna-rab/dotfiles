"""この turn に編集ツールで足した行を、transcript のエントリから求める。

**判断の対象は、この turn で Claude 自身が編集ツールで書いた行だけ。** `git diff HEAD` を
見る方式にすると、人が手で書いたものも、前の turn から未コミットで残っているものも
毎回上がってくる。git を使わないので、git リポジトリの外で編集したファイルも対象になる。

**その代わり Bash 越しの書き換えは見えない。** `sed -i`・heredoc・リダイレクトで書き換えた
分は transcript の `tool_input` に現れないので取りこぼす。

**SubagentStop では `agent_transcript_path` を読む。** `transcript_path` は親の transcript で、
サブエージェントの編集は載っていない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

# 人が書いたものではないので見ない
SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        ".venv",
        "venv",
        "__pycache__",
        ".git",
    }
)

EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


def is_subagent(payload: dict) -> bool:
    return payload.get("hook_event_name") == "SubagentStop"


def transcript_field(payload: dict) -> str:
    """payload のどのキーに、この turn の transcript のパスがあるか。"""
    return "agent_transcript_path" if is_subagent(payload) else "transcript_path"


def is_user_prompt(entry: dict) -> bool:
    """transcript の 1 行が人の発言か。turn の区切りに使う。

    tool_result だけの user エントリ（ツールの戻り値）と、Claude Code が差し込む
    isMeta 付きのエントリは区切りにしない。
    """
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(block.get("type") == "text" for block in content if isinstance(block, dict))
    return False


def edits_in_last_turn(entries: Iterable[dict]) -> list[dict]:
    """直近のユーザー発言より後にある Edit / Write / MultiEdit の入力を、時系列順に返す。"""
    edits: list[dict] = []
    for entry in entries:
        if is_user_prompt(entry):
            edits.clear()
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in EDIT_TOOLS
                and isinstance(block.get("input"), dict)
            ):
                edits.append({"name": block["name"], **block["input"]})
    return edits


def added_lines(old: str, new: str) -> set[str]:
    """new にあって old に無い行（前後の空白を落として比較）。

    old を引くのが要点。引かないと、再インデントやコードの移動で同じ行が old と new の
    両方に載るたびに発火してしまう。同じ内容の行が増えた分だけ数えるので Counter を使う。
    """
    grown = Counter(s.strip() for s in new.splitlines()) - Counter(
        s.strip() for s in old.splitlines()
    )
    grown.pop("", None)
    return set(grown)


def is_scratchpad(path: Path, temp_roots: Iterable[Path]) -> bool:
    """会話が終われば捨てる scratchpad の中か。autodev の worktree は一時ディレクトリの外にある。"""
    return "scratchpad" in path.parts and any(path.is_relative_to(root) for root in temp_roots)


def added_lines_by_file(
    edits: list[dict], cwd: Path, temp_roots: Iterable[Path]
) -> dict[Path, set[str]]:
    roots = tuple(temp_roots)
    result: dict[Path, set[str]] = {}
    for edit in edits:
        raw_path = edit.get("file_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = cwd / path
        if is_scratchpad(path, roots):
            continue
        lines = result.setdefault(path, set())
        name = edit["name"]
        if name == "Write":
            lines.update(added_lines("", str(edit.get("content") or "")))
        elif name == "Edit":
            lines.update(
                added_lines(str(edit.get("old_string") or ""), str(edit.get("new_string") or ""))
            )
        elif name == "MultiEdit":
            for sub in edit.get("edits") or []:
                if isinstance(sub, dict):
                    lines.update(
                        added_lines(
                            str(sub.get("old_string") or ""), str(sub.get("new_string") or "")
                        )
                    )
    return {path: lines for path, lines in result.items() if lines}


def is_generated(path: Path) -> bool:
    """依存パッケージやビルドの出力の中か。人が書いたものではないので見ない。"""
    return bool(SKIP_DIR_NAMES.intersection(path.parts))


def shown_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)
