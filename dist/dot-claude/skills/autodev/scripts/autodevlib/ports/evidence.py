"""完了チェックの証拠を git とコマンドから集める。

**合否の文言をここで組まない。** だから完了チェックを試すときは `Evidence` を手で組んで
`core/verdict.py` の `judge()` に渡せばよく、git も claude も要らない。

`Evidence` と `VerifyResult` の形は `core/verdict.py` にある。判断する側が形を持つので、
フィールド名の打ち間違いが型検査に出る。
"""

from __future__ import annotations

from typing import Any

from ..core.verdict import Evidence, VerifyResult
from . import proc, repo, review_store


def collect(
    *,
    stage_ok: bool,
    stage_detail: str,
    tree: str,
    parent: str,
    branch: str,
    review_path: str,
    tests_since: str | None,
) -> Evidence:
    """①〜⑤の証拠を集める。**⑥は含まない**（`run_verify()` で別に流す）。"""
    review = review_store.read(review_path)
    return Evidence(
        stage_ok=stage_ok,
        stage_detail=stage_detail,
        parent=parent,
        branch=branch,
        commits=repo.commit_count(tree, parent, branch),
        tests_since=tests_since,
        # 基準のコミットが無ければ⑤は判定できないので、差分も取らない
        changed_since_tests=(
            tuple(repo.changed_files(tree, tests_since, branch)) if tests_since else ()
        ),
        review_path=review_path,
        review=review,
        reviewers_by_round=_reviewers_by_round(review),
        adversarial_ran=review is not None and review_store.adversarial_ran(review),
        review_runs=len(review.get("runs", [])) if review is not None else 0,
    )


def _reviewers_by_round(review: dict[str, Any] | None) -> dict[str, tuple[str, ...]]:
    """ラウンドごとに走り終えたレビューステージ。キーはラウンドの名前。"""
    if review is None:
        return {}
    labels = dict.fromkeys(str(run["round"]) for run in review.get("runs", []))
    return {label: tuple(review_store.reviewers_seen(review, label)) for label in labels}


def run_verify(tree: str, commands: list[str], timeout: int = 3600) -> tuple[VerifyResult, ...]:
    """検証コマンド一式を driver が流す。**1 本落ちたらそこで止める。**

    **`bash -lc` で流す。** コマンドはリポジトリ固有の設定（config.json）から来るもので、
    パイプやリダイレクトを含みうる。指示の文面が入る余地は無い。
    """
    out: list[VerifyResult] = []
    for command in commands:
        got = proc.run(["bash", "-lc", command], cwd=tree, timeout=timeout)
        out.append(
            VerifyResult(command=command, ok=got.ok, code=got.code, out=got.out, err=got.err)
        )
        if not got.ok:
            break
    return tuple(out)
