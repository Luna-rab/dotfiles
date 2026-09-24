#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.80"]
# ///
"""autodev の run を別のタブで見る画面の入口。中身は `hud/app/watch.py`。

~/.claude/scripts/autodev-watch.py [作業名]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ty は PEP 723 のスクリプトを別の環境で検査し、pyproject.toml の extra-paths を見ないので解決できない
from hud.app.watch import main  # ty: ignore[unresolved-import]

if __name__ == "__main__":
    raise SystemExit(main())
