#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13"]
# ///
"""Claude Code の statusline の入口。中身は `hud/`（`hud/__init__.py` に層の説明がある）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ty は PEP 723 のスクリプトを別の環境で検査し、pyproject.toml の extra-paths を見ないので解決できない
from hud.app.statusline import main  # ty: ignore[unresolved-import]

if __name__ == "__main__":
    raise SystemExit(main())
