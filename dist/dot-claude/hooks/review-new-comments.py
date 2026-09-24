#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["tree-sitter-language-pack>=0.13"]
# ///
"""応答を終える前に、この turn で足したコメントを一覧で見直させる Stop hook。

transcript（会話の JSONL）から、この turn の Edit / Write / MultiEdit が足した行を集め、
tree-sitter で構文解析して取り出したコメントのうち足された行に重なるものを、
`hookSpecificOutput.additionalContext` で Claude に返す。ファイルは読むだけで書き換えない。

**編集直後（PostToolUse）ではなく応答の終わり（Stop）に置いている。** 理由は 3 つ。

1. PostToolUse はツール 1 回ごとに発火するので、5 ファイルに書けば 15〜20 回割り込む。
2. コメントは次の編集の手がかりとして働く。書いた直後に消させると足場を毎回壊す。
3. 実装の途中で「別ファイルのコメントを消せ」と割り込むと、実装の筋が切れる。

**判断の対象は、この turn で Claude 自身が編集ツールで書いた行だけ。** `git diff HEAD` を
見る方式にすると、人が手で書いたコメントも、前の turn から未コミットで残っているコメントも
毎回上がってくる。

**その代わり Bash 越しの書き換えは見えない。** `sed -i`・heredoc・リダイレクトで書き換えた
分は transcript の `tool_input` に現れないので取りこぼす。git を使わないので、git リポジトリの
外で編集したファイルも対象になる。

**コメントの切り出しは tree-sitter に任せる。** 行頭の記号で照合する方式だと、
`printf("/* x")` のように文字列リテラルの中に記号がある行をコメントと誤って拾い、
逆に行の途中から始まるコメントを取りこぼす。構文木なら文字列とコメントを取り違えない。

**Python の docstring だけは `ast` で拾う。** tree-sitter から見ると docstring は
ただの文字列ノードなので、構文木では普通の複数行文字列と区別できない。モジュール・
クラス・関数の本体の先頭にある文字列だけが docstring である。

**書いた経緯に寄りかかったコメントを直させる指示は、セクションごとではなく共通で 1 回出す。**
どの記法でも同じ基準で裁けるうえ、3 つに分けて書くと同じ文が 3 回並ぶ（`HISTORY_RULE`）。

**3 つのセクションに分けて、指示を別にする。** 性質が違うので同じ基準では裁けない。

| セクション           | 中身                                                 | 指示           |
| -------------------- | ---------------------------------------------------- | -------------- |
| コードのコメント     | 1 行コメントの連なり                                 | 消す           |
| ブロックと docstring | 複数行のコメント、docstring、`///` `//!` `/**`        | 縮める         |
| 設定ファイル         | TOML・YAML・Dockerfile・Makefile などのコメント       | 緩めに見て消す |

**Markdown は対象外。** 文書は成果物であって、コードに付けた注釈ではない。
`.conf` も対象外——nginx とも INI とも取れる拡張子で、文法を決められない。

`decision: "block"` ではなく `additionalContext` を使う。ループ防止（`stop_hook_active` と
8 連続打ち切り）は同じで、transcript の表示が hook エラーではなく feedback になる。

`CLAUDE_SKIP_COMMENT_REVIEW=1` で丸ごと止まる。
`--warm` を付けて起動すると依存の取得と文法のロードだけして終わる（install.sh が呼ぶ）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from tree_sitter_language_pack import get_parser


class Language(NamedTuple):
    grammar: str  # tree-sitter の文法名
    config: bool = False
    python: bool = False


# **ここに無い拡張子は見ない。** キーは `get_parser` が受ける文法名でなければならない
CODE_GRAMMARS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "bash": (".sh", ".bash"),
    "zsh": (".zsh",),
    "fish": (".fish",),
    "javascript": (".js", ".mjs", ".cjs", ".jsx"),
    "typescript": (".ts", ".mts", ".cts"),
    "tsx": (".tsx",),
    "vue": (".vue",),
    "svelte": (".svelte",),
    "astro": (".astro",),
    "html": (".html", ".htm"),
    "xml": (".xml", ".xhtml", ".svg", ".xsl", ".plist", ".xaml", ".axaml"),
    "css": (".css",),
    "scss": (".scss",),
    "less": (".less",),
    "rust": (".rs",),
    "go": (".go",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh"),
    "objc": (".m", ".mm"),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "scala": (".scala",),
    "groovy": (".groovy", ".gradle"),
    "swift": (".swift",),
    "csharp": (".cs",),
    "fsharp": (".fs",),
    "ruby": (".rb",),
    "php": (".php",),
    "lua": (".lua",),
    "perl": (".pl", ".pm"),
    "r": (".r",),
    "julia": (".jl",),
    "dart": (".dart",),
    "zig": (".zig",),
    "nix": (".nix",),
    "nim": (".nim",),
    "tcl": (".tcl",),
    "awk": (".awk",),
    "solidity": (".sol",),
    "elixir": (".ex", ".exs"),
    "erlang": (".erl",),
    "haskell": (".hs",),
    "elm": (".elm",),
    "ocaml": (".ml", ".mli"),
    "clojure": (".clj", ".cljs"),
    "commonlisp": (".lisp",),
    "scheme": (".scm",),
    "elisp": (".el",),
    "vim": (".vim",),
    "powershell": (".ps1",),
    "asm": (".asm",),
    "vhdl": (".vhd", ".vhdl"),
    "sql": (".sql",),
    "proto": (".proto",),
    "graphql": (".graphql",),
    "latex": (".tex",),
    "typst": (".typ",),
}

CONFIG_GRAMMARS: dict[str, tuple[str, ...]] = {
    "toml": (".toml",),
    "yaml": (".yaml", ".yml"),
    "ini": (".ini", ".cfg", ".service"),
    "properties": (".properties",),
    "json5": (".json5", ".jsonc"),
    "terraform": (".tf", ".tfvars"),
    "hcl": (".hcl",),
    "cmake": (".cmake",),
    "make": (".mk",),
    "just": (".just",),
    "bash": (".env",),
}

# `.env` や `.gitignore` は `Path.suffix` が空になるので、拡張子ではなく名前で引く
CONFIG_FILENAMES: dict[str, tuple[str, ...]] = {
    "dockerfile": ("Dockerfile", "Containerfile"),
    "make": ("Makefile", "GNUmakefile"),
    "just": ("Justfile", "justfile"),
    "cmake": ("CMakeLists.txt",),
    "gitignore": (".gitignore", ".gitattributes", ".dockerignore", "CODEOWNERS"),
    "editorconfig": (".editorconfig",),
    "ini": (".npmrc",),
    "bash": (".env", ".envrc"),
}


def _table(
    code: dict[str, tuple[str, ...]], config: dict[str, tuple[str, ...]]
) -> dict[str, Language]:
    built: dict[str, Language] = {}
    for is_config, grammars in ((False, code), (True, config)):
        for grammar, keys in grammars.items():
            for key in keys:
                built[key] = Language(grammar, config=is_config, python=grammar == "python")
    return built


LANGUAGES = _table(CODE_GRAMMARS, CONFIG_GRAMMARS)
LANGUAGES_BY_NAME = _table({}, CONFIG_FILENAMES)

# 拡張子の無いスクリプトを shebang から見分ける
SHEBANG_GRAMMARS: tuple[tuple[str, Language], ...] = (
    ("python", Language("python", python=True)),
    ("zsh", Language("zsh")),
    ("bash", Language("bash")),
    ("sh", Language("bash")),
    ("ruby", Language("ruby")),
    ("perl", Language("perl")),
    ("node", Language("javascript")),
)

# 人が書いたコメントではないので見ない
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

# コメントの形をしているが、消すと動作が変わるもの。
# TODO と FIXME も外す——未完の作業を指しており、コードからは読み取れない
SKIP_BODY_PREFIXES = (
    "noqa",
    "type:",
    "pyright:",
    "mypy:",
    "ruff:",
    "flake8:",
    "pylint:",
    "fmt:",
    "pragma:",
    "coding:",
    "coding=",
    "-*-",
    "shellcheck",
    "eslint",
    "prettier-",
    "biome-",
    "@ts-",
    "tslint:",
    "istanbul",
    "deno-lint",
    "nolint",
    "noinspection",
    "go:",
    "keep-sorted",
    "region",
    "endregion",
    "todo",
    "fixme",
    "spdx-",
    "copyright",
    "license",
)

# `strip_markers` が先頭から順に当てるので、長い記号を先に置く（`///` は `//` より前）
OPENERS = (
    "{/**",
    "{/*",
    "///",
    "//!",
    "/**",
    "/*",
    "<!--",
    "--[[",
    '"""',
    "'''",
    "{-",
    "//",
    "--",
    ";;;",
    ";;",
    "#",
    ";",
    "%",
    "*",
)
CLOSERS = ("*/}", "*/", "-->", "]]", "-}", '"""', "'''")

MAX_PER_SECTION = 8
MAX_HEADING_CHARS = 60

# "line" 以外は「消す」ではなく「縮める」セクションへ入る
KIND_LABELS = {"docstring": "docstring", "doc": "doc コメント", "block": "ブロック"}


class Span(NamedTuple):
    start: int  # 1 始まり
    end: int  # 1 始まり、この行を含む
    heading: str  # 報告に出す 1 行（記号を落とした本文）
    kind: str  # "line" | "doc" | "block" | "docstring"


def language_for(path: Path) -> Language | None:
    if SKIP_DIR_NAMES.intersection(path.parts):
        return None
    if ".min." in path.name or ".generated." in path.name:
        return None
    if path.suffix:
        found = LANGUAGES.get(path.suffix.lower())
        if found:
            return found
    found = LANGUAGES_BY_NAME.get(path.name)
    if found:
        return found
    return language_from_shebang(path) if not path.suffix else None


def language_from_shebang(path: Path) -> Language | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            first = handle.readline(200)
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    for name, language in SHEBANG_GRAMMARS:
        if name in first:
            return language
    return None


def clip(text: str) -> str:
    return text if len(text) <= MAX_HEADING_CHARS else text[:MAX_HEADING_CHARS] + "…"


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


def edits_in_last_turn(transcript: Path) -> list[dict]:
    """直近のユーザー発言より後にある Edit / Write / MultiEdit の入力を、時系列順に返す。"""
    edits: list[dict] = []
    try:
        handle = transcript.open(encoding="utf-8")
    except OSError:
        return []
    with handle:
        for line in handle:
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
    return {path: lines for path, lines in result.items() if lines}


def read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def shown_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def strip_markers(text: str) -> str:
    body = text.strip()
    for opener in OPENERS:
        if body.startswith(opener):
            body = body[len(opener) :].strip()
            break
    for closer in CLOSERS:
        if body.endswith(closer):
            body = body[: -len(closer)].strip()
            break
    return body


def heading_of(raw: str) -> str:
    """コメントの中から、報告に出す 1 行を取り出す。

    ファイルの行ではなくコメントの文字列から取る。`x = 1  # 1 を入れる` のような
    行末のコメントで、見出しにコードまで入らないようにするため。
    """
    for line in raw.splitlines():
        body = strip_markers(line)
        if any(ch.isalnum() for ch in body):
            return body
    return "（説明なし）"


def tree_sitter_comments(grammar: str, source: bytes) -> list[tuple[int, int, str]]:
    """構文木からコメントノードを集める。

    Rust の `///` や Lua の `--[[ ]]` はコメントノードの中に子ノード（doc_comment、
    comment_content）を持つので、コメントを見つけたらその下には潜らない。
    """
    tree = get_parser(grammar).parse(source)  # type: ignore[arg-type]
    found: list[tuple[int, int, str]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            # 末尾の改行を含むノード（Rust の line_comment）は 1 行はみ出すので戻す
            if node.end_point[1] == 0 and end > start:
                end -= 1
            found.append((start, end, (node.text or b"").decode("utf-8", errors="replace")))
            continue
        stack.extend(reversed(node.children))
    return found


def python_docstrings(source: str) -> list[tuple[int, int]]:
    """モジュール・クラス・関数の docstring の (開始行, 終了行)。

    編集途中で構文が壊れているファイルは諦めて空を返す。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            spans.append((first.lineno, first.end_lineno or first.lineno))
    return spans


def pep723_lines(lines: list[str]) -> set[int]:
    """PEP 723 の `# /// script` 〜 `# ///` に挟まれた行番号。依存の宣言なので判断対象にしない。"""
    inside = False
    result: set[int] = set()
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not inside and stripped.startswith("# /// "):
            inside = True
        if inside:
            result.add(number)
            if stripped == "# ///":
                inside = False
    return result


def kind_of(raw: str, start: int, end: int) -> str:
    stripped = raw.strip()
    # JSDoc は 1 行で書かれていても関数の説明を置く場所なので、docstring と同じ扱いにする
    if stripped.startswith(("/**", "{/**")):
        return "docstring"
    if end > start:
        return "block"
    return "doc" if stripped.startswith(("///", "//!")) else "line"


def is_noise(raw: str, start: int, skipped: set[int]) -> bool:
    """消す・縮めるの判断対象にならないコメントか。"""
    if start in skipped:
        return True
    stripped = raw.strip()
    if start == 1 and stripped.startswith("#!"):
        return True
    body = strip_markers(raw)
    if body.lower().startswith(SKIP_BODY_PREFIXES):
        return True
    # `# ----` のような区切り行。コメントではなく体裁なので数えない
    return not any(ch.isalnum() for ch in body)


def spans_in_file(language: Language, lines: list[str], added: set[str]) -> list[Span]:
    """足された行に重なるコメントを、連なりでまとめて返す。

    1 行コメントの連なりをまとめるのは、4 行の Why コメントを 4 件として見せないため。
    `///` の連なりも 1 つの塊として扱う。
    """
    source = "\n".join(lines)
    skipped = pep723_lines(lines) if language.python else set()

    hits: list[tuple[int, int, str, str]] = []  # (開始行, 終了行, 種類, 生の文字列)
    for start, end, raw in tree_sitter_comments(language.grammar, source.encode("utf-8")):
        if start > len(lines) or is_noise(raw, start, skipped):
            continue
        hits.append((start, end, kind_of(raw, start, end), raw))
    if language.python:
        hits.extend(
            (start, end, "docstring", "\n".join(lines[start - 1 : end]))
            for start, end in python_docstrings(source)
        )

    kept = [
        (start, end, kind, raw)
        for start, end, kind, raw in sorted(set(hits))
        if any(line.strip() in added for line in lines[start - 1 : end])
    ]

    spans: list[Span] = []
    for start, end, kind, raw in kept:
        merged = (
            spans
            and kind in ("line", "doc")
            and spans[-1].kind == kind
            and spans[-1].end + 1 == start
        )
        if merged:
            previous = spans.pop()
            spans.append(Span(previous.start, end, previous.heading, kind))
        else:
            spans.append(Span(start, end, heading_of(raw), kind))
    return spans


def state_path(session_id: str) -> Path:
    directory = Path(tempfile.gettempdir()) / "claude-comment-review"
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return directory / f"{digest}.json"


def load_reported(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def save_reported(path: Path, keys: set[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(keys)), encoding="utf-8")
    except OSError:
        pass


HISTORY_RULE = (
    "どのセクションでも先に直すのは、書いた経緯に寄りかかったコメント。"
    "コメントは、いまそこにあるコードが何をするか・どんな制約の下で動くかだけを書く。"
    "コードだけを渡された人が確かめようのないことは書かない。\n"
    "  該当するのは、変更の履歴を語る行（「以前は〜だった」「〜から変えた」「〜を足した」"
    "「元の実装では」「修正済み」）、指示の出どころを指す行（「レビューで言われたので」"
    "「要望どおり」「指摘に対応」）、この会話の中でしか通じない語（「先ほどの」"
    "「今回の対応」「上記の変更」）、採らなかった案との対比（「〜する方法もあるが」）、"
    "作業の予定（自分が次に何をするかを書いた TODO）。\n"
    "  中身に残す価値があるなら、いまのコードの性質として言い直す。"
    "「B に変えた」ではなく「B でないと C が壊れる」と書く。言い直せないなら消す。\n"
    "  経緯そのものはコミットメッセージと PR 本文に書く。コードには残さない。"
)

CODE_RULE = (
    "  消すのは、処理を日本語に言い直したもの、変数名・関数名の和訳、"
    "「〜を初期化する」「〜を返す」だけの行。\n"
    "  残すのは、知らないと間違えることだけ。外部の制約（API の上限、OS の違い、"
    "既知のバグ）、なぜその手を選んだか、守らないと壊れる約束（順序、冪等性、上限）、"
    "実測した値とその条件。残す判断をしたものに理由を書き足さなくてよい。"
)

BLOCK_RULE = (
    "  こちらは消すのではなく縮める。docstring・JSDoc は関数やモジュールの説明を置く"
    "正しい場所なので、無くさない。\n"
    "  落とすのは、型注釈と引数名から読める Args・Returns・Parameters の羅列、"
    "処理を順番に言い直した段落、「〜について説明する」という書き出し。"
    "1 文で足りるなら 1 文にする。\n"
    "  コードを読めば分かることだけで出来ている塊は、縮めずに消す。"
)

CONFIG_RULE = (
    "  設定は値だけ見ても何のためか分からないので、残す基準はコードより緩い。\n"
    "  落とすのは、キー名を和訳しただけの行、既定値をそのまま書き写した行、"
    "節の見出しを繰り返す行。\n"
    "  残すのは、なぜこの値にしたか、変えると何が壊れるか、どこから来た値か、"
    "そのツールを入れた理由。"
)


class Sections(NamedTuple):
    code: list[str]
    block: list[str]
    config: list[str]


def section(title: str, rows: list[str], rule: str) -> str:
    shown = rows[:MAX_PER_SECTION]
    hidden = len(rows) - len(shown)
    listing = list(shown)
    if hidden:
        listing.append(f"  ほか {hidden} 件")
    return f"■ {title}（{len(rows)} 件）\n" + "\n".join(listing) + "\n" + rule


def build_message(sections: Sections) -> str:
    parts = ["このターンで足したコメントを、応答を終える前に見直すこと。", HISTORY_RULE]
    if sections.code:
        parts.append(section("コードのコメント", sections.code, CODE_RULE))
    if sections.block:
        parts.append(section("ブロックコメントと docstring", sections.block, BLOCK_RULE))
    if sections.config:
        parts.append(section("設定ファイルのコメント", sections.config, CONFIG_RULE))
    parts.append("消した件数・縮めた件数・書き換えた件数だけを 1 行で報告して終えること。")
    return "\n\n".join(parts)


def collect(
    added: dict[Path, set[str]], cwd: Path, reported: set[str]
) -> tuple[Sections, set[str]]:
    sections = Sections([], [], [])
    fresh: set[str] = set()

    for path in sorted(added):
        language = language_for(path)
        lines = read_lines(path) if language else None
        if language is None or lines is None:
            continue
        name = shown_path(path, cwd)

        for span in spans_in_file(language, lines, added[path]):
            length = span.end - span.start + 1
            key = f"{span.kind}\0{name}\0{span.heading}"
            if key in reported:
                continue
            fresh.add(key)
            if span.kind == "line":
                tail = f"（{length} 行）" if length > 1 else ""
                row = f"  {name}:{span.start}  {clip(span.heading)}{tail}"
                (sections.config if language.config else sections.code).append(row)
            else:
                label = KIND_LABELS[span.kind]
                sections.block.append(
                    f"  {name}:{span.start}  {clip(span.heading)}（{label}・{length} 行）"
                )

    return sections, fresh


def review(payload: dict) -> str | None:
    # 2 周目は黙って通す。これを見ないと、消さない判断をしたコメントで永久に止まる
    if payload.get("stop_hook_active"):
        return None

    transcript = Path(str(payload.get("transcript_path") or ""))
    if not transcript.is_file():
        return None
    cwd = Path(str(payload.get("cwd") or os.getcwd()))

    added = added_lines_by_file(edits_in_last_turn(transcript), cwd)
    if not added:
        return None

    store = state_path(str(payload.get("session_id") or ""))
    reported = load_reported(store)
    sections, fresh = collect(added, cwd, reported)
    if not fresh:
        return None

    save_reported(store, reported | fresh)
    return build_message(sections)


def main() -> int:
    if "--warm" in sys.argv[1:]:
        get_parser("python")
        return 0
    if os.environ.get("CLAUDE_SKIP_COMMENT_REVIEW") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0

    message = review(payload)
    if message is not None:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": payload.get("hook_event_name", "Stop"),
                        "additionalContext": message,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 何が起きても Claude の停止は妨げない
        print(f"review-new-comments: skipped ({exc!r})", file=sys.stderr)
        sys.exit(0)
