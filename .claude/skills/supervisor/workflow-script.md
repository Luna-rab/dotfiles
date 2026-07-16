# オーケストレーションスクリプトの骨組み（テンプレート）

監督者が Workflow ツールに渡す dynamic workflow スクリプトの骨組み。ステップを直列に
流し、各ステップは「並列タスク → エスカレーション管理 → マージ管理」の 3 フェーズで締める。
各 `agent()` プロンプトの中身は [implementation-prompt.md](implementation-prompt.md) /
[review-prompt.md](review-prompt.md) / [escalation-prompt.md](escalation-prompt.md) /
[merge-prompt.md](merge-prompt.md) の契約に従って組み立てる。

**重要な制約: subagent は subagent を起動できない**（`agent()` の入れ子は 1 レベルまで）。
「エスカレーション管理の中で fix/review を回す」「マージ管理の中で fix/review を回す」といった
入れ子は、そのままは書けない。**エスカレーション管理・マージ管理はスクリプト側の制御フロー**
（`for` ループ・`if`）として書き、fix・review・マージ準備などの実務だけを `agent()` にする。

## 原則

- **ステップは直列（`for` で await）**。前のステップのマージ管理が topic を前進させてから次のステップが始まる。
- **ステップ内タスクは並列（`parallel`）**。各タスクは `計画 → 実装 → レビュー ↔ 修正ループ` を
  回し、**自分ではマージしない**。3 回・無進捗で直せない must-fix はエスカレーションする。
- **standard タスクのレビューは通常＋敵対的を並列で走らせる**（`runReview`）。敵対的レビューは
  「加えられた変更はすべて誤りである」という前提で粗探しをする独立レビューで、通常レビューが
  見落とす前提の誤りを拾う。両方が承認したときだけ `approved: true`、指摘は両者を結合する。
  **light タスク（docs・機械的・影響小）は通常レビュー 1 本のみ**（敵対的を省いてコストを下げる）。
  タスクの `tier` で切り替える。
- **fix と review は必ず別 `agent()`**（修正の自己承認を防ぐ）。
- **マージは実装ブランチごとに逐次**。topic への並列マージはコンフリクトするので `for` で
  1 本ずつ。各回で最新 topic をタスクブランチ側に取り込み、コンフリクトを解消し、動作検証してから
  topic を前進させる。
- **コンフリクト解消は独立レビューを通してから topic に載せる**（整合者が書いたコードの
  自己承認を防ぐ。コンフリクトの無いブランチは新規コードを書かないので追加レビュー不要）。
- **動作検証は実装報告を鵜呑みにしない**。検証コマンド一式に加え、外形動作（アプリ・CLI の
  起動）をマージ管理が自分で駆動して確かめる（[merge-prompt.md](merge-prompt.md)）。
- **ステップ全体で止める**: 直せないエスカレーション・解けないコンフリクト・通らない動作検証は、
  ステップを未マージのまま返す。後続のステップは前のステップが未マージなので進まない。監督者がユーザーへ
  エスカレーションする。
- **すべての `agent()` に `model` を明示し、機械的な作業には `effort` で軽さを与える**
  （SKILL.md「役割ごとのモデルと effort」）。standard の実装・修正・マージ準備は `'opus'`、light の
  実装・修正は `'sonnet'`（effort `'medium'`）、レビューは effort `'medium'`、topic マージは
  `'sonnet'` + effort `'low'`、再計画は `'fable'`。指定したモデルが使えないときは 1 つ下
  （`'fable'` → `'opus'` → `'sonnet'`）に落とす。
- worktree 分離（`isolation: "worktree"`）で実装・修正・マージ（コンフリクト解消）の競合を
  防ぐ。worktree はデフォルトブランチから分岐するので、起点に取り込む topic をプロンプトで
  指定する。レビュー・topic マージは push 済みブランチを対象にする（worktree は他ステージから
  見えない）。
- **冪等にする**: resume で topic マージが再実行されても壊れないよう、topic を前進させる前に
  「そのブランチが既に topic に入っていないか」を確認させる（[merge-prompt.md](merge-prompt.md)）。
- **起動前に権限を解消する**。ファイル編集以外（検証コマンド・git・`gh`・外形動作の起動・
  許可リスト外の MCP）は許可リストに無いと権限プロンプトでワークフローを止める。
- `agent()` は throw すると `null` を返す。結果は `.filter(Boolean)` で除いてから使う。
- `Date.now()` / `Math.random()` / `new Date()` は使えない（resume を壊すため）。

## 骨組み

```javascript
export const meta = {
  name: 'supervisor-run',
  description: 'ステップを直列に回し、ステップ内で実装→レビュー→修正を並列化し、エスカレーション管理とマージ管理で締める',
  phases: [
    { title: 'Implement' }, { title: 'Review' }, { title: 'Fix' },
    { title: 'Escalation' }, { title: 'Merge' },
  ],
}

// レビュー結果（review-prompt.md「報告形式」に対応）
const REVIEW = {
  type: 'object',
  properties: {
    approved: { type: 'boolean' },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { enum: ['must-fix', 'should-fix', 'nit'] },
      file: { type: 'string' }, line: { type: 'number' },
      summary: { type: 'string' }, rationale: { type: 'string' },
      confirmedBy: { type: 'string' },  // どう裏づけたか（実行したテスト・検証、または「差分の精読のみ」）
    } } },
  },
  required: ['approved'],
}
// 実装/修正の結果（PR 番号・タスクブランチ・DoD 要約・検証結果を返させる）
const IMPL = { type: 'object', properties: {
  pr: { type: 'number' }, branch: { type: 'string' },
  summary: { type: 'string' }, verified: { type: 'boolean' },
  changeKind: { enum: ['docs', 'logic'] },  // 修正が doc/コメントのみか（再レビューの軽重に使う）
  blocked: { type: 'boolean' },             // DoD が曖昧で実装に入れない（計画段階で判定）
  questions: { type: 'string' },            // blocked のとき監督者に確認したい点
  decisions: { type: 'array', items: { type: 'string' } },  // 自分の判断で変えた目標・DoD・スコープ
  deferrals: { type: 'array', items: { type: 'string' } },  // 先延ばし・対象外にした作業
}, required: ['branch'] }
// 再計画（escalation-prompt.md に対応）
const PLAN = { type: 'object', properties: { plan: { type: 'string' } }, required: ['plan'] }
// マージ管理の 1 ブランチ分（merge-prompt.md「報告形式」に対応）
const MERGE = { type: 'object', properties: {
  branch: { type: 'string' },
  hadConflict: { type: 'boolean' },   // 最新 topic の取り込みでコンフリクトが出たか
  verified: { type: 'boolean' },      // 検証コマンド一式 + 外形動作が通ったか
  merged: { type: 'boolean' },        // topic を前進できたか（コンフリクト無 + 検証通過のとき true）
  reason: { type: 'string' },         // 未マージ・未検証なら理由
}, required: ['branch'] }

const mustFixCount = r => (r?.findings || []).filter(f => f.severity === 'must-fix').length
// ステップが失敗して返るときも decisions/deferrals は取りこぼさず tasks から集約する
const fail = (step, tasks, reason) => ({ step, merged: false, reason, tasks,
  decisions: (tasks || []).flatMap(t => t.decisions || []),
  deferrals: (tasks || []).flatMap(t => t.deferrals || []) })
// タスク階層から実装/修正の model・effort を決める（light は下位モデル・低めの effort でコストを下げる）
const implOpts = task => task?.tier === 'light' ? { model: 'sonnet', effort: 'medium' } : { model: 'opus' }
// findings を file:line:severity:summary で重複除去（通常＋敵対的の同旨指摘を fix に二重に渡さない）
const dedupeFindings = fs => {
  const seen = new Set()
  return (fs || []).filter(f => {
    const k = `${f.file}:${f.line}:${f.severity}:${f.summary}`
    return seen.has(k) ? false : (seen.add(k), true)
  })
}

// 1 回のレビュー。standard タスクは通常＋敵対的を並列で起動して結合、light タスクは通常レビュー 1 本。
// 敵対的レビューは「加えられた変更はすべて誤り」という前提で粗探しをする（adversarialReviewPrompt）。
// approved は走らせた全レビューが承認したときだけ true、findings は結合して重複を除く。
async function runReview({ id, branch, task, phase = 'Review',
    prompt = reviewPrompt, adversarialPrompt = adversarialReviewPrompt,
    adversarial = task?.tier !== 'light', effort = 'medium' }) {
  const normalModel = task?.tier === 'light' ? 'sonnet' : 'opus'
  const runs = [
    () => agent(prompt(branch, task), { label: `review:${id}`, phase, model: normalModel, effort, schema: REVIEW }),
  ]
  if (adversarial) runs.push(
    () => agent(adversarialPrompt(branch, task), { label: `review-adv:${id}`, phase, model: 'opus', effort, schema: REVIEW }))
  const done = (await parallel(runs)).filter(Boolean)
  return {
    approved: done.length === runs.length && done.every(r => r.approved),  // null（失敗）が 1 つでもあれば未承認
    findings: dedupeFindings(done.flatMap(r => r.findings || [])),
  }
}

// fix↔review ループ（タスク内・エスカレーション・マージ検証失敗で共通）。
// review が approve するまで（または 3 回・無進捗で）fix→review を回す。
async function fixReviewLoop({ id, startBranch, review, task, maxRounds = 3 }) {
  let branch = startBranch, round = 0, prevMustFix = mustFixCount(review)
  while (review && !review.approved && round < maxRounds) {
    round++
    const fix = await agent(fixPrompt(branch, review.findings, task),
      { label: `fix:${id}#${round}`, phase: 'Fix', ...implOpts(task), isolation: 'worktree', schema: IMPL })
    if (!fix) break
    branch = fix.branch
    // doc・コメントのみの修正は通常レビュー 1 本・effort 低で再確認、ロジック修正はタスク階層どおり
    review = fix.changeKind === 'docs'
      ? await runReview({ id: `${id}#${round}`, branch, task, adversarial: false, effort: 'low' })
      : await runReview({ id: `${id}#${round}`, branch, task })
    const mf = mustFixCount(review)
    if (mf >= prevMustFix) break   // 無進捗 = スタック
    prevMustFix = mf
  }
  return { branch, approved: !!review?.approved, review }
}

// --- 1 タスク: 計画→実装→レビュー↔修正ループ（マージしない） ---
async function runTask(task) {
  const impl = await agent(implPrompt(task),
    { label: `impl:${task.id}`, phase: 'Implement', ...implOpts(task), isolation: 'worktree', schema: IMPL })
  if (!impl) return null
  const carry = { decisions: impl.decisions || [], deferrals: impl.deferrals || [] }
  // DoD が曖昧で計画段階で止まったら、実装・レビューに進まず blocked で返す（監督者がユーザーに確認）
  if (impl.blocked) return { task: task.id, branch: impl.branch, blocked: true, questions: impl.questions, ...carry }
  const review = await runReview({ id: task.id, branch: impl.branch, task })   // standard は通常＋敵対的、light は通常のみ
  const r = await fixReviewLoop({ id: task.id, startBranch: impl.branch, review, task })
  return { task: task.id, pr: impl.pr, branch: r.branch, dod: task.dod,
           approved: r.approved, findings: r.review?.findings || [], ...carry }
}

// --- 1 ステップ: 並列タスク → エスカレーション管理 → マージ管理 ---
async function runStep(stepNo, tasks, topic) {
  // フェーズ1: 並列タスク
  const results = (await parallel(tasks.map(t => () => runTask({ ...t, startFrom: topic })))).filter(Boolean)

  // DoD が曖昧で blocked のタスクがあれば、実装せずステップを止めて監督者に確認を上げる（§3 の確認方針）
  const blocked = results.filter(r => r.blocked)
  if (blocked.length) return fail(stepNo, results,
    `DoD の確認が必要: ${blocked.map(b => `${b.task}（${b.questions}）`).join('; ')}`)

  // フェーズ2: エスカレーション管理（未承認タスクがあれば）
  const escalated = results.filter(r => !r.approved)
  if (escalated.length) {
    const plan = await agent(replanPrompt(stepNo, escalated),
      { label: `replan:step${stepNo}`, phase: 'Escalation', model: 'fable', schema: PLAN })
    for (const r of escalated) {
      const task = tasks.find(t => t.id === r.task)
      const res = await fixReviewLoop({ id: `esc:${r.task}`, startBranch: r.branch,
        review: { approved: false, findings: r.findings },
        task: { ...task, replan: plan?.plan } })
      r.branch = res.branch; r.approved = res.approved
      if (!res.approved) return fail(stepNo, results, `エスカレーション未解決: ${r.task}`)  // ステップを止める
    }
  }

  // フェーズ3: マージ管理（実装ブランチごとに逐次）
  for (const r of results) {
    // 最新 topic をタスクブランチ側に取り込み、コンフリクト解消 + 動作検証。コンフリクト無 +
    // 検証通過なら topic を前進させて merged:true で返す（新規コードが無いので独立レビュー不要）。
    const m = await agent(mergePrompt(stepNo, r, topic),
      { label: `merge:${r.task}`, phase: 'Merge', model: 'opus', isolation: 'worktree', schema: MERGE })
    if (!m) return fail(stepNo, results, `マージ起動失敗: ${r.task}`)
    if (m.merged) continue
    let branch = m.branch

    // コンフリクト解消があれば独立レビュー（通常 + 敵対的を並列） → 未承認なら fix ループ
    if (m.hadConflict) {
      const cr = await runReview({ id: `merge:${r.task}`, branch, task: tasks.find(t => t.id === r.task),
        phase: 'Merge', prompt: b => conflictReviewPrompt(stepNo, r, b),
        adversarialPrompt: b => adversarialConflictReviewPrompt(stepNo, r, b) })
      if (!cr?.approved) {
        const res = await fixReviewLoop({ id: `merge-fix:${r.task}`, startBranch: branch, review: cr, task: tasks.find(t => t.id === r.task) })
        if (!res.approved) return fail(stepNo, results, `コンフリクト解消が承認されない: ${r.task}`)
        branch = res.branch
      }
    }
    // 動作検証が通っていなければ fix→review→（fix 内で再検証）
    if (!m.verified) {
      const res = await fixReviewLoop({ id: `verify-fix:${r.task}`, startBranch: branch,
        review: { approved: false, findings: [{ severity: 'must-fix', summary: m.reason || '動作検証が通らない' }] },
        task: tasks.find(t => t.id === r.task) })
      if (!res.approved) return fail(stepNo, results, `動作検証が通らない: ${r.task}`)
      branch = res.branch
    }
    // topic マージ: 別セッションが（コンフリクト解消は上でレビュー済み）topic へマージする
    const topicMerge = await agent(topicMergePrompt(stepNo, r, branch, topic),
      { label: `topic-merge:${r.task}`, phase: 'Merge', model: 'sonnet', effort: 'low', schema: MERGE })
    if (!topicMerge?.merged) return fail(stepNo, results, topicMerge?.reason || `topic への取り込み失敗: ${r.task}`)
  }

  // 成功時も目標変更・先延ばしを集約して返す（監督者が最終 PR・報告にまとめる。SKILL.md §7）
  return { step: stepNo, merged: true, verified: true, tasks: results,
           decisions: results.flatMap(r => r.decisions || []),
           deferrals: results.flatMap(r => r.deferrals || []) }
}

// --- ステップの連鎖 ---
const TOPIC = 'topic/<作業名>'
const steps = [
  { no: 1, tasks: [/* ステップ1のタスク定義… */] },
  { no: 2, tasks: [/* ステップ2のタスク定義… */] },
]
const report = []
let ok = true
for (const s of steps) {
  if (!ok) { report.push({ step: s.no, merged: false, reason: '前のステップが未マージのためスキップ' }); continue }
  const r = await runStep(s.no, s.tasks, TOPIC)   // 各ステップの起点は前のステップが前進させた topic
  report.push(r)
  if (!r.merged) ok = false                        // ステップが未マージなら以降を止める
}
return report
```

各 `task` オブジェクトには `id` / `dod` に加え `tier`（`"light"` | `"standard"`）と構造化仕様
（受け入れ基準・スコープ境界・調査の入口・隣接タスクとの契約。SKILL.md §4）を持たせる。`tier` が
`implOpts` と `runReview` の厳格さ（モデル・effort・敵対的レビューの有無）を決める。

`implPrompt` / `reviewPrompt` / `adversarialReviewPrompt` / `fixPrompt` / `replanPrompt` /
`mergePrompt` / `conflictReviewPrompt` / `adversarialConflictReviewPrompt` / `topicMergePrompt` は、
監督者が「プロジェクト前提の解決」で特定した契約
（DoD・検証コマンド一式・外形動作の確認手順・不可侵パス・ブランチ規約・起点の指定）を封入
して組み立てる。解法（変更ファイルの列挙・行番号つき手順）は封入しない
（[implementation-prompt.md](implementation-prompt.md) の §0）。`implPrompt` / `fixPrompt` は返り値に
`changeKind`（doc のみか）・`blocked`＋`questions`（DoD が曖昧なら実装せず返す）・`decisions`
（変えた目標）・`deferrals`（先延ばし）を含めるよう指示する。
