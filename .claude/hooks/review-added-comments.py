#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["tree-sitter-language-pack>=0.13"]
# ///
"""Claude Code の Stop フック。この turn で Claude が追加したコメントを列挙し、1 件ずつ要否を問い直させる。

AI は処理をそのまま言い直しただけのコメントを大量に書く。書いている最中のコメントは
推論の足場になるので残してよいが、そのまま放置するとコードの可読性が下がる。
CLAUDE.md に「不要なコメントを書くな」と書いても長い会話ではコンテキストに埋もれて守られない。
そこで、Claude が応答を終える直前（Stop）に、追加したコメントの実物を突きつけて見直させる。

PostToolUse ではなく Stop にしているのは、編集のたびに割り込むと会話がぶつ切りになるため。

動き:

1. 標準入力の JSON（Claude Code が渡す）から `transcript_path` と `stop_hook_active` を読む
2. `stop_hook_active` が true なら、このフックが起こした見直しの turn なので何もしない（exit 0）
3. transcript（会話の JSONL）を読み、直近のユーザー発言より後の Edit / Write / MultiEdit の入力から
   「追加した行」を集める
4. 編集したファイルを tree-sitter で構文解析してコメントを取り出し（Python は docstring も加える）、
   追加した行に含まれるものだけ残す
5. 1 件も無ければ exit 0。あれば `{"decision": "block", "reason": ...}` を標準出力に書いて、
   Claude に見直しをさせる

subagent の中でも同じ判定をする（入力の `agent_id` は見ない）。
スクリプト内で何か失敗したときは exit 0 にして、Claude の停止を妨げない。

手動で試すとき:

    echo '{"transcript_path": "<jsonl>", "stop_hook_active": false}' | .claude/hooks/review-added-comments.py

`--warm` を付けて起動すると依存の取得と文法のロードだけして終わる（install.sh が呼ぶ）。
"""

from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tree_sitter_language_pack import get_parser

# 拡張子（またはファイル名）から tree-sitter の言語名を引く。
# ここに無いものは対象外。Markdown・JSON・プレーンテキストは、`#` や `//` が
# コメントではなく本文なので載せていない。
LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".fish": "fish",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".groovy": "groovy",
    ".gradle": "groovy",
    ".swift": "swift",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".jl": "julia",
    ".dart": "dart",
    ".zig": "zig",
    ".nix": "nix",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".clj": "clojure",
    ".el": "elisp",
    ".vim": "vim",
    ".ps1": "powershell",
    ".sql": "sql",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
    ".json5": "json5",
    ".jsonc": "json5",
    ".proto": "proto",
    ".graphql": "graphql",
    ".tf": "terraform",
    ".hcl": "hcl",
    ".cmake": "cmake",
    ".mk": "make",
    ".just": "just",
    ".tex": "latex",
    ".typ": "typst",
}
LANGUAGE_BY_NAME: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "Makefile": "make",
    "GNUmakefile": "make",
    "CMakeLists.txt": "cmake",
    "Justfile": "just",
    "justfile": "just",
}

# コメントの形をしているが、消す・残すの判断対象にならないもの。
# lint / 型検査への指示、ライセンス表記、shebang。
PRAGMA_MARKERS = (
    "noqa",
    "type:",
    "ruff:",
    "pyright:",
    "mypy:",
    "fmt:",
    "pragma:",
    "eslint-",
    "prettier-",
    "@ts-",
    "biome-",
    "SPDX-",
    "Copyright",
    "License",
    "-*- coding",
)

# 見直しの指示。reason としてそのまま Claude に渡る。
INSTRUCTION_HEAD = (
    "この turn で追加したコメントが {count} 件あります。"
    "1 件ずつ「これを削ったら読者は何を間違えるか」を問い、答えられないものは削除してください。\n"
    "削る: コードをそのまま言い直したもの。処理の見出しだけのもの。経緯や作業ログ。\n"
    "残す: なぜそうしたか。落とし穴・制約・守るべき規約。コードから読み取れない前提。\n"
    "コードの動作は変えないでください。最後に、削ったものと残したものを 1 行ずつ報告してください。\n"
)


@dataclass(frozen=True)
class Comment:
    """ファイル内の 1 つのコメント。複数行のブロックコメントは行の範囲で持つ。"""

    path: Path
    start_line: int  # 1 始まり
    end_line: int  # 1 始まり、この行を含む
    text: str  # 先頭行の内容（reason に載せる）


# ---------------------------------------------------------------------------
# transcript から「この turn で追加した行」を集める
# ---------------------------------------------------------------------------


def is_user_prompt(entry: dict) -> bool:
    """transcript の 1 行が、人（またはユーザー役）の発言かどうか。

    tool_result だけの user エントリ（ツールの戻り値）と、Claude Code が差し込む
    isMeta 付きのエントリは turn の区切りにしない。
    """
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(block.get("type") == "text" for block in content if isinstance(block, dict))
    return False


def iter_edits_in_last_turn(transcript_path: Path) -> list[dict]:
    """直近のユーザー発言より後にある Edit / Write / MultiEdit の tool_use 入力を、時系列順に返す。"""
    edits: list[dict] = []
    with transcript_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
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
                    and block.get("name") in ("Edit", "Write", "MultiEdit")
                    and isinstance(block.get("input"), dict)
                ):
                    edits.append({"name": block["name"], **block["input"]})
    return edits


def added_lines(old: str, new: str) -> Counter[str]:
    """new にあって old に無い行（前後の空白を除いた内容で比較）。同じ内容が増えた分だけ数える。"""
    old_counter = Counter(s.strip() for s in old.splitlines())
    new_counter = Counter(s.strip() for s in new.splitlines())
    diff = new_counter - old_counter
    diff.pop("", None)
    return diff


def added_lines_by_file(edits: list[dict], cwd: Path) -> dict[Path, set[str]]:
    result: dict[Path, set[str]] = {}
    for edit in edits:
        raw_path = edit.get("file_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = cwd / path
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
    return result


# ---------------------------------------------------------------------------
# ファイルからコメントを取り出す
# ---------------------------------------------------------------------------


def language_for(path: Path) -> str | None:
    return LANGUAGE_BY_NAME.get(path.name) or LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def tree_sitter_comments(
    path: Path, language: str, source: bytes, lines: list[str]
) -> list[Comment]:
    """tree-sitter の構文木から、型名に comment を含む最外側のノードを集める。

    Rust の `///` や Lua の `--[[ ]]` はコメントノードの中に子ノード（doc_comment、
    comment_content）を持つので、コメントを見つけたらその下には潜らない。
    """
    tree = get_parser(language).parse(source)  # type: ignore[arg-type]
    found: list[Comment] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            # 末尾の改行を含むノード（Rust の line_comment）は 1 行はみ出すので戻す
            if node.end_point[1] == 0 and end > start:
                end -= 1
            first_line = (node.text or b"").decode("utf-8", errors="replace").splitlines()[0]
            found.append(Comment(path, start, end, first_line.strip()))
            continue
        stack.extend(reversed(node.children))
    return found


def python_docstrings(path: Path, source: str, lines: list[str]) -> list[Comment]:
    """モジュール・クラス・関数の docstring。tree-sitter では文字列ノードなので ast で拾う。"""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return []
    found: list[Comment] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = first.end_lineno or first.lineno
            found.append(Comment(path, first.lineno, end, lines[first.lineno - 1].strip()))
    return found


def pep723_block_lines(lines: list[str]) -> set[int]:
    """PEP 723 の `# /// script` 〜 `# ///` に挟まれた行番号（1 始まり）。依存の宣言なので判断対象にしない。"""
    inside = False
    result: set[int] = set()
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not inside and stripped.startswith("# /// "):
            inside = True
        if inside:
            result.add(i)
            if stripped == "# ///":
                inside = False
    return result


def is_excluded(comment: Comment, lines: list[str], skipped_lines: set[int]) -> bool:
    if comment.start_line in skipped_lines:
        return True
    if comment.start_line == 1 and comment.text.startswith("#!"):
        return True
    body = " ".join(lines[comment.start_line - 1 : comment.end_line])
    return any(marker in body for marker in PRAGMA_MARKERS)


def comments_in_file(path: Path) -> list[Comment]:
    language = language_for(path)
    if language is None or not path.is_file():
        return []
    source_bytes = path.read_bytes()
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []
    lines = source.splitlines()
    found = tree_sitter_comments(path, language, source_bytes, lines)
    if language == "python":
        found.extend(python_docstrings(path, source, lines))
    skipped = pep723_block_lines(lines) if language == "python" else set()
    found = [c for c in found if not is_excluded(c, lines, skipped)]
    return sorted(set(found), key=lambda c: c.start_line)


def comments_added_this_turn(added: dict[Path, set[str]]) -> list[Comment]:
    hits: list[Comment] = []
    for path, new_lines in added.items():
        comments = comments_in_file(path) if new_lines else []
        if not comments:
            continue
        file_lines = path.read_text(encoding="utf-8").splitlines()
        for comment in comments:
            span = file_lines[comment.start_line - 1 : comment.end_line]
            if any(line.strip() in new_lines for line in span):
                hits.append(comment)
    return hits


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


def build_reason(hits: list[Comment], cwd: Path) -> str:
    head = INSTRUCTION_HEAD.format(count=len(hits))
    items = []
    for c in hits:
        try:
            shown = c.path.relative_to(cwd)
        except ValueError:
            shown = c.path
        suffix = "" if c.start_line == c.end_line else f" (〜{c.end_line} 行)"
        items.append(f"{shown}:{c.start_line}: {c.text}{suffix}")
    return head + "\n" + "\n".join(items) + "\n"


def warm_up() -> None:
    """依存を取り寄せて Python 文法をロードするだけ。初回の Stop でダウンロード待ちが起きないようにする。"""
    get_parser("python")


def main() -> int:
    if "--warm" in sys.argv[1:]:
        warm_up()
        return 0
    hook_input = json.load(sys.stdin)
    if hook_input.get("stop_hook_active"):
        return 0
    transcript = Path(str(hook_input.get("transcript_path") or ""))
    if not transcript.is_file():
        return 0
    cwd = Path(str(hook_input.get("cwd") or Path.cwd()))
    edits = iter_edits_in_last_turn(transcript)
    hits = comments_added_this_turn(added_lines_by_file(edits, cwd))
    if not hits:
        return 0
    json.dump(
        {"decision": "block", "reason": build_reason(hits, cwd)}, sys.stdout, ensure_ascii=False
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 何が起きても Claude の停止は妨げない
        print(f"review-added-comments: skipped ({exc!r})", file=sys.stderr)
        sys.exit(0)
