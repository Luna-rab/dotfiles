"""応答を終える前に、この turn で足したコメントを一覧で見直させる。

足した行に重なるコメントを tree-sitter で取り出し、`additionalContext` で Claude に返す。
ファイルは読むだけで書き換えない。

**編集直後（PostToolUse）ではなく応答の終わり（Stop）に置いている。** 理由は 3 つ。

1. PostToolUse はツール 1 回ごとに発火するので、5 ファイルに書けば 15〜20 回割り込む。
2. コメントは次の編集の手がかりとして働く。書いた直後に消させると足場を毎回壊す。
3. 実装の途中で「別ファイルのコメントを消せ」と割り込むと、実装の筋が切れる。

`CLAUDE_SKIP_COMMENT_REVIEW=1` で丸ごと止まる。
"""

from __future__ import annotations

from turnreview.app.turn import added_in_turn, run
from turnreview.core import comment_message, comments, languages
from turnreview.core.comment_message import FileSpans
from turnreview.core.report import session_digest
from turnreview.core.review import Review
from turnreview.core.turn import shown_path
from turnreview.ports import files
from turnreview.ports.store import ReportStore
from turnreview.syntax import comments as syntax


def review(payload: dict) -> Review | None:
    added, cwd = added_in_turn(payload)
    candidates: list[FileSpans] = []
    for path in sorted(added):
        first = files.first_line(path) if languages.wants_shebang(path) else None
        language = languages.language_for(path, first)
        lines = files.read_lines(path) if language else None
        if language is None or lines is None:
            continue
        source = "\n".join(lines)
        spans = comments.spans_in_file(
            lines,
            added[path],
            syntax.comments(language.grammar, source),
            syntax.python_docstrings(source) if language.python else [],
            python=language.python,
        )
        candidates.append(FileSpans(shown_path(path, cwd), language.config, spans))
    if not candidates:
        return None

    store = ReportStore(
        "claude-comment-review", session_digest(str(payload.get("session_id") or ""))
    )
    reported = store.load()
    found, fresh = comment_message.review_of(candidates, reported)
    if not fresh:
        return None
    store.save(reported | fresh)
    return found


def main() -> int:
    return run(
        "review-new-comments", review, skip_env="CLAUDE_SKIP_COMMENT_REVIEW", warm=syntax.warm
    )
