"""段の表。

**段を足すのは、この表に 1 行と `core/prompt.py` の `ROLE_KEY` に 1 行、そして
`contracts/<名>.md`・`schemas/<名>.json` の 2 ファイルである。** `ROLE_KEY` を足さないと、
段の起動時に `KeyError` で落ちる。

体数の計算も打ち切りの条件もここには無い（`core/review_policy.py` と driver にある）ので、
レビュアーを 1 体増やしても完了判定のコードに手が入らない。

段はどれも `claude -p` を 1 プロセス起動して走る。段ごとに変えるのは 4 つである。

- **モデルと思考量**——読んで決める段は `opus`、文章を組むだけの段は `sonnet`
- **書き込みの範囲**——`edits` が偽なら worktree の中を書き換えられない（フックが止める）
- **往復の上限**——探索が終わらないまま回り続けるのを止める
- **渡す環境変数**——裁定の鍵とテストの解禁は、それが要る段にだけ渡す（driver が決める）

段へ渡す文面を組むのは `core/prompt.py` である。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    """段 1 つ。`name` は表の鍵で、ログとレビュー記録に出る名前でもある。"""

    name: str
    contract: str
    role: str
    #: `claude --model` に渡す別名。別名にしておくと新しいモデルに自動で乗る
    model: str = "opus"
    #: `claude --effort`（low / medium / high / xhigh / max）
    effort: str = "high"
    #: 往復の上限。**超えると段が失敗する**ので、探索の要る段には多めに置く。
    #: 結果を返す段では、スキーマに合わなかったときの言い直しもここを食う
    max_turns: int = 80
    #: 構造化出力で結果を返す段か（`--json-schema` を渡す）
    writes_result: bool = True
    #: セッションを続けられるか。**実装と修正だけ真**（レビューは毎ラウンドまっさらにする）
    resumable: bool = False
    #: `AUTODEV_JUDGE_TOKEN` を渡すか。**裁定だけ真**
    judge: bool = False
    #: テストファイルへの書き込みを許すか。**テスト作成だけ真**
    allow_tests: bool = False
    #: ソースを書き換える段か。**偽なら worktree の中への書き込みがフックで止まる**
    #: （`AUTODEV_READ_ONLY`）。ツールごと消さないのは、結果の JSON を書くのに
    #: `Write` が要るからである
    edits: bool = False
    timeout: int = 3600


TABLE: dict[str, Stage] = {
    "plan": Stage(
        name="plan",
        contract="plan",
        role="計画",
        max_turns=150,
    ),
    "testgen": Stage(
        name="testgen",
        contract="testgen",
        role="テスト作成",
        max_turns=180,
        allow_tests=True,
        edits=True,
    ),
    "impl": Stage(
        name="impl",
        contract="implementation",
        role="実装",
        max_turns=280,
        resumable=True,
        edits=True,
    ),
    "fix": Stage(
        name="fix",
        contract="implementation",
        role="修正",
        max_turns=230,
        resumable=True,
        edits=True,
    ),
    "review:normal": Stage(
        name="review:normal",
        contract="review",
        role="通常レビュー",
        max_turns=120,
        writes_result=False,
    ),
    "review:adversarial": Stage(
        name="review:adversarial",
        contract="review-adversarial",
        role="敵対的レビュー",
        max_turns=120,
        writes_result=False,
    ),
    "judge": Stage(
        name="judge",
        contract="judge",
        role="裁定",
        effort="medium",
        max_turns=130,
        judge=True,
    ),
    # 文章を組むだけの段。**state.json の値はテンプレートのマーカーに driver が埋める**ので、
    # ここで書かせるのは散文だけである。安いモデルで足りる
    "pr-body": Stage(
        name="pr-body",
        contract="pr-body",
        role="PR 本文",
        model="sonnet",
        effort="medium",
        max_turns=60,
    ),
    "summary": Stage(
        name="summary",
        contract="summary",
        role="まとめ",
        model="sonnet",
        effort="medium",
        max_turns=60,
    ),
}
