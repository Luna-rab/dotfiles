# オーケストレーションスクリプトの骨組み（テンプレート）

監督者が Workflow ツールに渡す dynamic workflow スクリプトの骨組み。実装・レビュー・
修正の fan-out をここに載せる。各 `agent()` プロンプトの中身は
[implementation-prompt.md](implementation-prompt.md) と [review-prompt.md](review-prompt.md)
の契約に従って組み立てる。

## 原則

- **1 タスク = 1 本の pipeline**（`実装 → レビュー → 修正ループ`）。独立タスクは
  `parallel()`、依存タスクは `pipeline()` で順に流す。
- worktree 分離（`isolation: "worktree"`）で実装・修正エージェントの競合を防ぐ。
- レビューは push 済み feature ブランチを対象にする（worktree はランタイムが管理し、
  他ステージからは直接見えない。ステージ間の受け渡しは push 済みブランチで行う）。
- **マージと最終 PR はスクリプトに載せない**。スクリプトは各タスクの
  `{ pr, branch, verified }` を返し、監督者が実測確認してから手でマージする
  （理由は [SKILL.md](SKILL.md) の「オーケストレーションの仕組み」）。
- **起動前に権限を解消する**。サブエージェントはファイル編集のみ自動承認する。検証
  コマンド・git・`gh`・許可リスト外の MCP は、許可リストに無いと実行中に権限プロンプトを
  出してワークフローを止める。使うコマンドを起動前に許可リストへ入れておく。
- `agent()` は throw すると `null` を返す。結果は `.filter(Boolean)` で除いてから使う。
- `Date.now()` / `Math.random()` / `new Date()` は使えない（resume を壊すため）。
  タイムスタンプや乱数が要るときは `args` で渡すか、index でプロンプトを変える。

## 骨組み

```javascript
export const meta = {
  name: 'supervisor-run',
  description: '実装→レビュー→修正を並列/依存で回し、各タスクの PR を返す',
  phases: [{ title: 'Implement' }, { title: 'Review' }, { title: 'Fix' }],
}

// レビューの構造化出力スキーマ（review-prompt.md「報告形式」に対応）
const REVIEW = {
  type: 'object',
  properties: {
    approved: { type: 'boolean' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { enum: ['must-fix', 'should-fix', 'nit'] },
          file: { type: 'string' }, line: { type: 'number' },
          summary: { type: 'string' }, rationale: { type: 'string' },
        },
      },
    },
  },
  required: ['approved'],
}
const IMPL = { /* PR 番号・branch・検証結果を返させるスキーマ */ }

// 1 タスクを 実装→レビュー→修正ループ で回す
async function runTask(task) {
  const impl = await agent(implPrompt(task),
    { label: `impl:${task.id}`, phase: 'Implement', model: 'opus',
      isolation: 'worktree', schema: IMPL })
  if (!impl) return null

  let review = await agent(reviewPrompt(impl.branch, task),
    { label: `review:${task.id}`, phase: 'Review', schema: REVIEW })

  let round = 0
  while (review && !review.approved && round < 3) {
    round++
    const fix = await agent(fixPrompt(impl.branch, review.findings, task),
      { label: `fix:${task.id}#${round}`, phase: 'Fix', model: 'opus',
        isolation: 'worktree', schema: IMPL })
    review = await agent(reviewPrompt(fix.branch, task),
      { label: `review:${task.id}#${round}`, phase: 'Review', schema: REVIEW })
  }
  // 上限超過は未承認のまま返し、監督者がエスカレーションを判断する
  return { task: task.id, pr: impl.pr, branch: impl.branch, approved: review?.approved }
}

// --- タスク配置 ---
// 独立タスク群は parallel、依存関係は pipeline / await の順序で表す。
phase('Implement')
const independent = [/* タスク定義… */]
const results = await parallel(independent.map(t => () => runTask(t)))

// 依存タスク（前段の feature ブランチを起点にする）は前段の結果を渡して直列化:
// const base = await runTask(taskA)
// const next = await runTask({ ...taskB, startFrom: base.branch })

return results.filter(Boolean)
```

`implPrompt` / `reviewPrompt` / `fixPrompt` は、監督者が「プロジェクト前提の解決」で
特定した契約（DoD・検証コマンド一式・不可侵パス・ブランチ規約・起点の指定）を封入して
組み立てる。解法（変更ファイルの列挙・行番号つき手順）は封入しない
（implementation-prompt.md の §0）。
