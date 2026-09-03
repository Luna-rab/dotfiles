"""外部コマンドの実行と、標準出力・エラー出力の書式。

エラー行の頭に付ける名前は `sys.argv[0]` から取る（`verify.py: ...` の形）。付属スクリプトが
4 本に分かれているので、名前を各スクリプトに書き写すのではなく実行中のファイル名から
取ることで、どれが止めたのかが出力だけで分かり、書き写し漏れも起きない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Any, NoReturn


def prog() -> str:
    """エラー行の頭に付ける名前。"""
    return os.path.basename(sys.argv[0]) or "supervisor"


def die(message: str) -> NoReturn:
    """理由を stderr に書いて終了コード 1 で止める。"""
    print(f"{prog()}: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message: str) -> None:
    """止めずに理由だけを stderr に書く。呼び出し元が終了コードを決める場合に使う。"""
    print(f"{prog()}: {message}", file=sys.stderr)


def run(cmd: Sequence[str], allow_fail: bool = False) -> tuple[int, str]:
    """外部コマンドを実行し、(終了コード, 標準出力) を返す。

    `allow_fail=True` は「失敗も答えのうち」の呼び出し（`git ls-remote --exit-code` で
    ブランチの有無を調べるなど）に使う。既定では失敗した時点で止める。
    """
    # check=False は意図的である。失敗を例外にせず終了コードとして返し、`allow_fail` で
    # 呼び出し元に分岐させる（`git ls-remote --exit-code` のように失敗が答えの呼び出しがある）
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not allow_fail:
        die(f"{' '.join(cmd)} が失敗しました\n{proc.stderr.strip()}")
    return proc.returncode, proc.stdout.strip()


def out(cmd: Sequence[str]) -> str:
    """成功を前提に標準出力だけを返す。"""
    return run(cmd)[1]


def git(tree: str, argv: Sequence[str], allow_fail: bool = False) -> tuple[int, str]:
    """`tree` の中で git を叩く。`-C` を必ず付け、(終了コード, 標準出力+エラー出力) を返す。

    出力を結合して返すのは、呼び出し元が失敗の中身を 1 つの文字列で報告に載せるためである。
    stack.py と worktree.py の両方が使う——同じラッパを各スクリプトに書き写すと、
    片方だけ直したときに挙動がずれる。
    """
    proc = subprocess.run(["git", "-C", tree, *argv], capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not allow_fail:
        die(f"git -C {tree} {' '.join(argv)} が失敗しました\n{proc.stderr.strip()}")
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def emit(payload: Any, pretty: bool = False) -> None:
    """結果を JSON 1 件として stdout に出す。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
    # このあと stderr に理由を書く呼び出し元がある。先に流さないと端末上で順序が入れ替わり、
    # 「何が起きたか」より「どうすべきか」が先に見える
    sys.stdout.flush()
