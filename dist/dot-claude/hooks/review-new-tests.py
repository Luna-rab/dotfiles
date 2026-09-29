#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["tree-sitter-language-pack>=0.13", "rich>=13"]
# ///
"""この turn で足したテストを見直させる Stop / SubagentStop hook の入口。中身は `turnreview/`。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ty は PEP 723 のスクリプトを別の環境で検査し、pyproject.toml の extra-paths を見ないので解決できない
from turnreview.app.testcases import main  # ty: ignore[unresolved-import]

if __name__ == "__main__":
    raise SystemExit(main())
