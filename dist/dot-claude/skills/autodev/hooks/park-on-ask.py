#!/usr/bin/env python3
"""段が `autodev ask` を呼んだら、答えが置かれるまで段を止める PreToolUse フック。

**止め方は `defer` である。** プロセスは終了コード 0 で終わり、`result` イベントに
`stop_reason: tool_deferred` と `deferred_tool_use` が載る。あとで
`claude -p --resume <セッション id>` すると**同じツール呼び出しで PreToolUse が再発火**し、
そのとき答えのファイルが在れば通る。プロンプトを渡し直す必要はない（実測）。

    1 回目   答えが無い      → defer。質問を `<run>/questions/<鍵>.json` に書く
    2 回目   答えが在る      → allow。`autodev ask` が答えを標準出力に出す

**答えのファイルの実在だけを見る。** 中身を解釈しないので、何度再開しても同じ判断になる。

`defer` が効くのは**そのターンのツール呼び出しが 1 つだけのとき**である。ほかのツールと
一緒に呼ばれると通常の権限評価に落ち、`autodev ask` がそのまま走って終了コード 3 で
「単独で呼べ」と返す（`autodevlib/cli.py` の `cmd_ask`）。段はそれを読んで呼び直せる。

要る環境変数は `AUTODEV_RUN_DIR`（driver が段ごとに渡す）だけである。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

#: `autodev ask --id <鍵>` の鍵。無ければ 1 つにまとめる
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

#: 入口のファイル名（`config/paths.py` の `launcher()` が返す末尾）。**ここが合わないと
#: フックは何も止めず、段は `ask` の終了コード 3 を受けて `blocked` を返す**
LAUNCHER_NAME = "autodev.py"


def decision(kind: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind}},
        ensure_ascii=False,
    )


def asked(command: str) -> tuple[str, str] | None:
    """`autodev ask` の呼び出しなら（鍵, 質問）を返す。"""
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        return None
    if "ask" not in tokens or not any(t.endswith(LAUNCHER_NAME) for t in tokens):
        return None
    key, question = "ask", ""
    for index, token in enumerate(tokens[:-1]):
        if token == "--id":
            key = tokens[index + 1]
        elif token == "--question":
            question = tokens[index + 1]
    return (key if KEY.match(key) else "ask"), question


def main() -> int:
    run_dir = os.environ.get("AUTODEV_RUN_DIR")
    if not run_dir:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    found = asked((payload.get("tool_input") or {}).get("command") or "")
    if not found:
        return 0
    key, question = found

    if os.path.exists(os.path.join(run_dir, "answers", f"{key}.json")):
        print(decision("allow"))
        return 0

    path = os.path.join(run_dir, "questions", f"{key}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"id": key, "question": question}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print(decision("defer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
