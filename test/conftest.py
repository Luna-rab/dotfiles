"""`autodevlib` を import できるようにする。

インストールされるパッケージではない（`python3` 単体で動かすため、第三者パッケージも
`pip install` も要らない作りにしてある）ので、置き場を `sys.path` に足さないと import
できない。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 契約・スキーマ・テンプレート・フックと `SKILL.md` の置き場
SKILL_ROOT = REPO_ROOT / "dist" / "dot-claude" / "skills" / "autodev"
#: 入口（`autodev.py`）と `autodevlib` の置き場
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
CLAUDE_HOOKS = REPO_ROOT / "dist" / "dot-claude" / "hooks"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
