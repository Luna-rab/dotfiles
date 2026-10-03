#!/usr/bin/env python3
"""計画ステージの ask を、回答のファイルが置かれるまで止める PreToolUse フック。

回答のファイル `answers/<tool_use_id>.json` が無ければ `defer` で止める。claude は終了コード 0 で
終わり、result に `stop_reason: tool_deferred` と止まった呼び出しが載る。`claude -p --resume` すると
同じ呼び出しでこのフックがもう一度走り、今度はファイルがあるので通す。`defer` が効くのは、そのターンの
ツール呼び出しが 1 つだけのとき（指示書で ask を単独で呼ばせる）。

当て方は `autodevlib/adapters/claude/guard.py` の `park`。入力が読めない・設定が渡っていない・読み込めない
ときは止める（終了コード 2）。
"""

from __future__ import annotations

import importlib.util
import os
import sys

HANDLER = "park-on-ask.py"


def main() -> int:
    try:
        entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hook.py")
        spec = importlib.util.spec_from_file_location("autodev_hook_entry", entry)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        sys.stderr.write(f"ask のフックを読み込めないので、呼び出しを止めました: {error}\n")
        return 2
    return module.main(HANDLER)


if __name__ == "__main__":
    raise SystemExit(main())
