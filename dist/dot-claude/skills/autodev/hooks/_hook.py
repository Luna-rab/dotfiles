"""ガードのフック（deny-writes.py・park-on-ask.py）の共通の入口。

autodevlib を読み込み、標準入力と環境変数を `adapters/guard.run_hook` に渡して、返事を書き出す。
読み込めないときは止める（終了コード 2）。フックが黙って落ちると、ガードが消えたまま走る。
"""

from __future__ import annotations

import os
import sys

DENY_EXIT = 2


def main(handler: str) -> int:
    try:
        # 置き場は階層を数えて上らず、SKILL.md を探して決める（ファイルを動かしても黙ってずれない）
        root = os.path.dirname(os.path.abspath(__file__))
        while not os.path.isfile(os.path.join(root, "SKILL.md")):
            parent = os.path.dirname(root)
            if parent == root:
                raise ImportError("SKILL.md が見つからない（autodev の置き場が壊れている）")
            root = parent
        sys.path.insert(0, os.path.join(root, "scripts"))
        from autodevlib.adapters.claude import guard  # noqa: PLC0415
    except Exception as error:
        sys.stderr.write(f"ガードのフックを読み込めないので、呼び出しを止めました: {error}\n")
        return DENY_EXIT
    reply = guard.run_hook(handler, sys.stdin.read(), os.environ)
    if reply.stdout:
        sys.stdout.write(reply.stdout + "\n")
    if reply.stderr:
        sys.stderr.write(reply.stderr + "\n")
    return reply.exit_code
