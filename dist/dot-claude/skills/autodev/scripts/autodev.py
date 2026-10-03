#!/usr/bin/env python3
"""autodev の入口。中身は `autodevlib/cli.py`。

PATH には無いので、絶対パスで起動する（LEDGER CL-01）。このファイルの置き場が `sys.path` の先頭に
入るので、隣の `autodevlib` をそのまま import できる。
"""

from __future__ import annotations

from autodevlib.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
