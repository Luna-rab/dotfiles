"""応答を終える前に、この turn で足したテストを 1 本ずつ見直させる。

`CLAUDE_SKIP_TEST_REVIEW=1` で丸ごと止まる。
"""

from __future__ import annotations

from turnreview.app.turn import added_in_turn, run
from turnreview.core import testcases
from turnreview.core.report import session_digest
from turnreview.core.review import Review
from turnreview.core.testcases import FileTests
from turnreview.core.turn import shown_path
from turnreview.ports import files
from turnreview.ports.store import ReportStore
from turnreview.syntax import testcases as syntax


def review(payload: dict) -> Review | None:
    added, cwd = added_in_turn(payload)
    candidates: list[FileTests] = []
    for path in sorted(added):
        suite = testcases.suite_of(path)
        lines = files.read_lines(path) if suite else None
        if suite is None or lines is None:
            continue
        declared = syntax.declared(suite, "\n".join(lines))
        candidates.append(FileTests(shown_path(path, cwd), lines, added[path], declared))
    if not candidates:
        return None

    store = ReportStore("claude-test-review", session_digest(str(payload.get("session_id") or "")))
    reported = store.load()
    found, fresh = testcases.review_of(candidates, reported)
    if not fresh:
        return None
    store.save(reported | fresh)
    return found


def main() -> int:
    return run("review-new-tests", review, skip_env="CLAUDE_SKIP_TEST_REVIEW", warm=syntax.warm)
