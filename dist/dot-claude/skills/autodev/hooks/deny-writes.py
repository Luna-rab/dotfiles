#!/usr/bin/env python3
"""ステージが書いてはいけないファイルへの書き込みを止める PreToolUse フック。

**権限の確認を飛ばす設定でもフックは走る**（実測。`calc_test.py` の作成がこの形で拒否される
ことを確かめた）。driver は `<ランディレクトリ>/guard.json` にここを指す設定を書き、`claude --settings` で
渡す。**worktree にはファイルを置かない**（置くと commit に混ざる危険がある）。

止めるものは 2 つあり、どちらも**worktree の中だけ**を見る。ステージは結果の JSON を
`<ランディレクトリ>/` の下——worktree の外——へ書くので、そこは通す。

    AUTODEV_READ_ONLY=1   worktree の中への書き込みを全部止める（読むだけのステージ）
    AUTODEV_TEST_GLOBS    テストのパス。ここへの書き込みを止める（実装ステージ・修正ステージ）
    AUTODEV_ALLOW_TESTS=1 テストへの書き込みを許す（テスト作成ステージだけ）

`--disallowedTools` ではなくフックで止めるのは、**結果の JSON を書くのに `Write` が要る**
からである。ツールごと消すと、読むだけのステージが自分の結果を書けなくなる。フックなら宛先で
分けられ、Bash のリダイレクトも同じ 1 か所で見られる。

終了コード 2 で拒否し、標準エラーに書いた理由がモデルに渡る。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

#: `autodevlib` の置き場。**階層を数えて上らない**（`config/paths.py` の `skill_root()` と
#: 同じ規則で、`SKILL.md` がある場所を探して `scripts/` を足す）
_root = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_root, "SKILL.md")):
    _parent = os.path.dirname(_root)
    if _parent == _root:
        sys.exit("deny-writes: SKILL.md が見つからない（autodev の置き場が壊れている）")
    _root = _parent
sys.path.insert(0, os.path.join(_root, "scripts"))

from autodevlib.core import globs  # noqa: E402  上の sys.path より後でしか import できない

# Bash 越しの書き込みを拾う。完全な検出はできない（完了チェック⑤の差分照合が最後の砦）が、
# リダイレクトと定番のコマンドはここで止める。
#
# **正規表現でコマンド行をなめず、`shlex` でトークンに割る。** 引用の中の `>` と `rm` は
# シェルに届かないので、リダイレクトや削除として数えてはいけない。`->` を含む Python の
# 一行スクリプトや、`->` を含む指摘の本文がそれに当たる。
#
# **リダイレクトの宛先とコマンド行の全パスも混ぜない。** 混ぜると
# `grep -rn x src README.md 2>/dev/null` が「README.md へ書く」になる。
#: `>` `>>` `2>` と、間に空白の無い `>file` / `2>/dev/null` の形
REDIRECT = re.compile(r"\d*>>?(.*)")
#: 引数のファイルを書き換えるコマンド。**トークンとして現れたときだけ**見る
#: （引用の中の "rm" に当たらない）
MUTATORS = frozenset({"tee", "mv", "cp", "rm", "truncate", "dd", "patch"})
PATHLIKE = re.compile(r"[\w./\-]*[\w\-]+\.[A-Za-z0-9]+")

TEST_DENIED = (
    "テストファイルはこのステージから変更できません: {path}\n"
    "テストを書けるのはテスト作成ステージだけです。テストが仕様と矛盾していると判断したら、"
    "直さずに結果の JSON の testConflict に書いて終えてください。"
)
TREE_DENIED = (
    "このステージは worktree の中を書き換えられません: {path}\n"
    "読んで判定するだけのステージです。結果は `StructuredOutput` ツールで返してください"
    "（ファイルに書く必要はありません）。"
)


def paths_from(tool: str, tool_input: dict) -> list[str]:
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        found = [tool_input.get("file_path") or tool_input.get("notebook_path") or ""]
        for edit in tool_input.get("edits") or []:
            if isinstance(edit, dict) and edit.get("file_path"):
                found.append(edit["file_path"])
        return [p for p in found if p]
    if tool == "Bash":
        command = tool_input.get("command") or ""
        tokens = _tokens(command)
        found = _redirect_targets(tokens)
        if _mutates(tokens):
            found += PATHLIKE.findall(command)
        return found
    return []


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        # 引用が閉じていない。素朴に割って、締める方向に倒す
        return command.split()


def _redirect_targets(tokens: list[str]) -> list[str]:
    """`>` の宛先だけを返す。`/dev/null` と fd の複製（`2>&1`）は外す。"""
    targets: list[str] = []
    pending = False
    for token in tokens:
        if pending:
            targets.append(token)
            pending = False
            continue
        matched = REDIRECT.fullmatch(token)
        if not matched:
            continue
        if matched.group(1):
            targets.append(matched.group(1))
        else:
            pending = True  # `> file` のように空白で離れている
    return [t for t in targets if t and not t.startswith(("/dev/", "&"))]


def _mutates(tokens: list[str]) -> bool:
    """引数のファイルを書き換えるコマンドが在るか。"""
    if MUTATORS & set(tokens):
        return True
    if "sed" in tokens and "-i" in tokens:
        return True
    return "git" in tokens and bool({"checkout", "restore"} & set(tokens))


def refuse(template: str, path: str) -> int:
    print(template.format(path=path), file=sys.stderr)
    return 2


def main() -> int:
    read_only = os.environ.get("AUTODEV_READ_ONLY") == "1"
    allow_tests = os.environ.get("AUTODEV_ALLOW_TESTS") == "1"
    if not read_only and allow_tests:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 読めない入力で作業を止めない

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    patterns = globs.parse_env(os.environ.get("AUTODEV_TEST_GLOBS"))
    root = payload.get("cwd") or os.getcwd()

    for raw in paths_from(tool, tool_input):
        rel = os.path.relpath(raw, root) if os.path.isabs(raw) else raw
        if rel.startswith(".."):
            continue  # worktree の外は対象外（結果の JSON はここに書く）
        if read_only:
            return refuse(TREE_DENIED, rel)
        if not allow_tests and globs.matches_any(rel, patterns):
            return refuse(TEST_DENIED, rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
