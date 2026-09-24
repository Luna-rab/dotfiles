"""ステージの表。

**ステージを足すのは、この表に 1 行と `core/prompt.py` の `ROLE_KEY` に 1 行、そして
`contracts/<名>.md`・`schemas/<名>.json` の 2 ファイルである。** `ROLE_KEY` を足さないと、
ステージの起動時に `KeyError` で落ちる。

体数の計算も停滞の条件もここには無い（`core/review_policy.py` と driver にある）ので、
レビューステージを 1 体増やしても完了判定のコードに手が入らない。

ステージはどれも `claude -p` を 1 プロセス起動して走る。ステージごとに変えるのは 4 つである。

- **モデルと思考量**——読んで決めるステージは `opus`、文章を組むだけのステージは `sonnet`。
  思考量は計画・再計画・ジャッジだけ `high`、ほかは `medium`
- **書き込みの範囲**——`edits` が偽なら worktree の中を書き換えられない（フックが止める）
- **ターンの上限**——探索が終わらないまま回り続けるのを止める
- **渡す環境変数**——ジャッジトークンとテストの解禁は、それが要るステージにだけ渡す（driver が決める）

ステージへ渡す文面を組むのは `core/prompt.py` である。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    """ステージ 1 つ。`name` は表のキーで、ログとレビュー記録に出る名前でもある。"""

    name: str
    contract: str
    role: str
    #: `claude --model` に渡す別名。別名にしておくと新しいモデルに自動で乗る
    model: str = "opus"
    #: `claude --effort`（low / medium / high / xhigh / max）。判断が進行を左右する計画・
    #: 再計画・ジャッジだけ high にする
    effort: str = "medium"
    #: ターンの上限。**超えるとステージが失敗する**ので、探索の要るステージには多めに置く。
    #: 結果を返すステージでは、スキーマに合わなかったときの言い直しもここを食う
    max_turns: int = 80
    #: 構造化出力で結果を返すステージか（`--json-schema` を渡す）
    writes_result: bool = True
    #: 続けるセッションの id を置く、タスクのキー。None なら毎回新しいセッションで走る。
    #: 実装と修正は `implSession` を共有し、ジャッジは `judgeSession` で前のラウンドの経緯を覚えておく。
    #: レビューは毎ラウンドまっさらにする（前のラウンドの言い分に引きずられずに読み直させる）
    session: str | None = None
    #: `AUTODEV_JUDGE_TOKEN` を渡すか。**ジャッジだけ真**
    judge: bool = False
    #: テストファイルへの書き込みを許すか。**テスト作成だけ真**
    allow_tests: bool = False
    #: ソースを書き換えるステージか。**偽なら worktree の中への書き込みがフックで止まる**
    #: （`AUTODEV_READ_ONLY`）。ツールごと消さないのは、結果の JSON を書くのに
    #: `Write` が要るからである
    edits: bool = False
    timeout: int = 3600


TABLE: dict[str, Stage] = {
    "plan": Stage(
        name="plan",
        contract="plan",
        role="計画",
        effort="high",
        max_turns=150,
    ),
    "replan": Stage(
        name="replan",
        contract="replan",
        role="再計画",
        effort="high",
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
        session="implSession",
        edits=True,
    ),
    "fix": Stage(
        name="fix",
        contract="implementation",
        role="修正",
        max_turns=230,
        session="implSession",
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
        role="ジャッジ",
        effort="high",
        max_turns=130,
        session="judgeSession",
        judge=True,
    ),
    # 文章を組むだけのステージ。**state.json の値はテンプレートのマーカーに driver が埋める**ので、
    # ここで書かせるのは自由記述だけである。安いモデルで足りる
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
