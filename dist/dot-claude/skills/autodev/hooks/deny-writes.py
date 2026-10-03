#!/usr/bin/env python3
"""ステージが書いてはいけない場所への書き込みと、`gh`・`git push` を止める PreToolUse フック。

何を止めるかは、driver がステージごとに環境変数 `AUTODEV_GUARD` で渡す Guard が決める（規則は
`autodevlib/domain/guard.py`、翻訳は `autodevlib/adapters/claude/guard.py`）。入力が読めない・設定が
渡っていない・読み込めないときは止める（終了コード 2）。
"""

from __future__ import annotations

import importlib.util
import os
import sys

HANDLER = "deny-writes.py"


def main() -> int:
    try:
        entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hook.py")
        spec = importlib.util.spec_from_file_location("autodev_hook_entry", entry)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        sys.stderr.write(f"ガードのフックを読み込めないので、呼び出しを止めました: {error}\n")
        return 2
    return module.main(HANDLER)


if __name__ == "__main__":
    raise SystemExit(main())
