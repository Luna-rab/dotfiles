"""色と記号。

**Nerd Font を前提にしない。** 既定のフォントにもある文字（`│` `━` `╾` `─` `┃` `✔` `◼` `◻` `✘`）
だけを使う。
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

DIM = Style(color="#6c7086")
ACCENT = Style(color="#89b4fa", bold=True)
MAUVE = Style(color="#cba6f7")
GREEN = Style(color="#a6e3a1")
YELLOW = Style(color="#f9e2af")
RED = Style(color="#f38ba8")
BLUE = Style(color="#74c7ec")
MARK = Style(color="#cdd6f4", bold=True)
BOLD = Style(bold=True)

#: 同じ行に並べる部品の間
GAP = "   "
#: 左の statusline と右のタスクリストの間
SIDE = Text("  │  ", style=DIM)
#: ステージの並びの間
ARROW = Text(" › ", style=DIM)

#: タスクの状態ごとの記号・記号の色・件名の色。表に無い状態（pending）は ◻
STATUS_MARK: dict[str, tuple[str, Style, Style]] = {
    "stacked": ("✔", GREEN, DIM),
    "running": ("◼", ACCENT, BOLD),
    "blocked": ("✘", RED, RED),
    "failed": ("✘", RED, RED),
    "dropped": ("–", DIM, DIM),
}
PENDING_MARK = ("◻", DIM, Style())
STATUS_LABEL = {
    "stacked": "スタック済み",
    "running": "実行中",
    "pending": "未着手",
    "dropped": "取り下げ",
    "blocked": "要確認",
    "failed": "失敗",
}
RATING_STYLE = {"must-fix": RED, "should-fix": YELLOW, "nit": DIM}
#: 指摘の状態の表示名（`autodev/GLOSSARY.md`）
FINDING_LABEL = {"open": "未解決", "closed": "解決済み", "rejected": "却下", "moved": "移管"}


def status_mark(status: str) -> tuple[str, Style, Style]:
    return STATUS_MARK.get(status, PENDING_MARK)
