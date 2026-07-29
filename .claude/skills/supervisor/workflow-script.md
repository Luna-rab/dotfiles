# オーケストレーションスクリプトの骨組み（テンプレート）

監督者が Workflow ツールに渡す dynamic workflow スクリプトの骨組み。**1 ステップ = 1 ワークフロー**で、
範囲は「レビュー承認まで」。ステップ内のタスクを並列に走らせ、各タスクは
`計画 → 実装 → レビュー ↔ 修正ループ → polish` を回して、承認済みタスクブランチの一覧を返す。
修正しきれないタスクはタスク単位でエスカレーションする。
**topic へのマージ・PR のクローズはスクリプトに載せない**——マージを指示されたサブエージェントは
安全クラシファイアに確率的に遮断される。統合はワークフロー完了後に監督者本体が行う
（[integration.md](integration.md)。理由は [design-notes.md](design-notes.md)）。

各 `agent()` プロンプトの中身は [implementation-prompt.md](implementation-prompt.md) /
[review-prompt.md](review-prompt.md) / [escalation-prompt.md](escalation-prompt.md) の契約に
従って組み立てる。

**重要な制約: subagent は subagent を起動できない**（`agent()` の入れ子は 1 レベルまで）。
「エスカレーションの中で fix/review を回す」といった入れ子は、そのままは書けない。
**エスカレーションはスクリプト側の制御フロー**として書き、fix・review・再計画などの実務だけを
`agent()` にする。

## 原則

- **タスクは並列（`parallel`）**。各タスクは独立したパイプラインで、他タスクの完了を待たない。
- **standard タスクのレビューは通常＋敵対的を並列で走らせる**（`runReview`）。敵対的レビューは
  「加えられた変更はすべて誤りである」という前提で粗探しをする独立レビューで、通常レビューが
  見落とす前提の誤りを拾う。両方が承認したときだけ `approved: true`、指摘は両者を結合する。
  **light タスク（docs・機械的・影響小）は通常レビュー 1 本のみ**（敵対的を省いてコストを下げる）。
  タスクの `tier` で切り替える。
- **fix と review は必ず別 `agent()`**（修正の自己承認を防ぐ）。
- **承認時に should-fix が残っていれば polish を 1 回だけ回す**（素通り防止。無限に回すと
  再レビューが毎回新しい should-fix を見つけて収束しないので 1 回で止める。残った should-fix は
  findings として返し、監督者が統合後に回収する。[integration.md](integration.md) §4）。
- **エスカレーションはタスク単位**。あるタスクの修正ループが未承認で返ったら、そのタスクのパイプラインの
  中で再計画（`agent`, `fable`, コードは書かない）→ fix↔review を回す。直れば承認済みとして返し、
  直らなければそのタスクを失敗として返す。
- **`agent()` の null・no-op は 1 回だけリトライする**（`agentRetry`）。throw は null になり、
  実作業ゼロの定型文応答が正常終了扱いになることもある。schema があれば定型文は弾かれやすいが、
  null は必ず拾う。
- **すべての `agent()` に `model` を明示し、機械的な作業には `effort` で軽さを与える**
  （SKILL.md「役割ごとのモデルと effort」）。standard の実装・修正は `'opus'`、light の
  実装・修正は `'sonnet'`（effort `'medium'`）、レビューは effort `'medium'`、再計画は `'fable'`。
  指定したモデルが使えないときは 1 つ下（`'fable'` → `'opus'` → `'sonnet'`）に落とす。
- **worktree はブランチを checkout する全エージェントに付ける**（実装・修正・レビュー・再計画）。
  worktree なしで checkout させるとメイン作業ツリーを汚染する。worktree 内は detached HEAD で
  作業させ、push は `git push origin HEAD:refs/heads/<ブランチ>` にする（ブランチ掴み防止。
  [implementation-prompt.md](implementation-prompt.md) §1）。ステージ間の受け渡しは push 済みの
  タスクブランチ経由で行う（worktree は他ステージから見えない）。
- **起動前に権限を解消する**。ファイル編集以外（検証コマンド・git・`gh`・外形動作の起動・
  許可リスト外の MCP）は許可リストに無いと権限プロンプトでワークフローを止める。
- **`args` は実オブジェクトで渡し、冒頭で防御パースする**（JSON 文字列で渡すと全フィールド
  undefined になる既知の落とし穴）。
- `Date.now()` / `Math.random()` / `new Date()` は使えない（resume を壊すため）。

## 骨組み

```javascript
export const meta = {
  name: 'supervisor-step<N>',
  description: 'ステップ<N>: タスクを並列に実装→レビュー→修正し、承認済みブランチ一覧を返す（統合は監督者）',
  phases: [
    { title: 'Implement' }, { title: 'Review' }, { title: 'Fix' },
    { title: 'Escalation' }, { title: 'Polish' },
  ],
}

// args の防御パース（JSON 文字列で渡された場合に備える）
const input = typeof args === 'string' ? JSON.parse(args) : args
const tasks = input?.tasks
const TOPIC = input?.topic
if (!Array.isArray(tasks) || !tasks.length || !TOPIC)
  return { failed: true, reason: 'args.tasks / args.topic が不正（実オブジェクトで渡すこと）' }

// レビュー結果（review-prompt.md「報告形式」に対応）。severity は required
// （欠落した指摘が polish 分岐をすり抜けた実績があるため）。
const REVIEW = {
  type: 'object',
  properties: {
    approved: { type: 'boolean' },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { enum: ['must-fix', 'should-fix', 'nit'] },
      file: { type: 'string' }, line: { type: 'number' },
      summary: { type: 'string' }, rationale: { type: 'string' },
      confirmedBy: { type: 'string' },  // どう裏づけたか（実行したテスト・検証、または「差分の精読のみ」）
    }, required: ['severity', 'summary'] } },
    notes: { type: 'string' },  // 実施した検証と問題なしと判断した観点の要約（空なら no-op を疑う）
  },
  required: ['approved', 'notes'],
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

const mustFixCount = r => (r?.findings || []).filter(f => f.severity === 'must-fix').length
const shouldFix = r => (r?.findings || []).filter(f => f.severity === 'should-fix')
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

// agent() の起動失敗（null）と no-op を 1 回だけリトライする。
// isNoop: 結果はあるが実作業の痕跡が無い応答（定型文）を検出する述語（省略可）。
async function agentRetry(prompt, opts, isNoop) {
  let r = await agent(prompt, opts)
  if (!r || (isNoop && isNoop(r)))
    r = await agent(prompt, { ...opts, label: `${opts.label}:retry` })
  return r
}

// 1 回のレビュー。standard タスクは通常＋敵対的を並列で起動して結合、light タスクは通常レビュー 1 本。
// レビューはブランチを checkout するので worktree を付ける（メインツリー汚染防止）。
// approved は走らせた全レビューが承認したときだけ true、findings は結合して重複を除く。
async function runReview({ id, branch, task, phase = 'Review',
    prompt = reviewPrompt, adversarialPrompt = adversarialReviewPrompt,
    adversarial = task?.tier !== 'light', effort = 'medium' }) {
  const normalModel = task?.tier === 'light' ? 'sonnet' : 'opus'
  const noop = r => !(r.findings || []).length && !r.notes   // 指摘も検証記録も無い応答は no-op
  const runs = [
    () => agentRetry(prompt(branch, task),
      { label: `review:${id}`, phase, model: normalModel, effort, isolation: 'worktree', schema: REVIEW }, noop),
  ]
  if (adversarial) runs.push(
    () => agentRetry(adversarialPrompt(branch, task),
      { label: `review-adv:${id}`, phase, model: 'opus', effort, isolation: 'worktree', schema: REVIEW }, noop))
  const done = (await parallel(runs)).filter(Boolean)
  return {
    approved: done.length === runs.length && done.every(r => r.approved),  // null（失敗）が 1 つでもあれば未承認
    findings: dedupeFindings(done.flatMap(r => r.findings || [])),
  }
}

// fix↔review ループ（タスク内・エスカレーションで共通）。
// review が approve するまで（または 3 回・無進捗で）fix→review を回す。
// fix の decisions/deferrals も carry に集める（取りこぼし防止）。
async function fixReviewLoop({ id, startBranch, review, task, carry, maxRounds = 3 }) {
  let branch = startBranch, round = 0, prevMustFix = mustFixCount(review)
  while (review && !review.approved && round < maxRounds) {
    round++
    const fix = await agentRetry(fixPrompt(branch, review.findings, task),
      { label: `fix:${id}#${round}`, phase: 'Fix', ...implOpts(task), isolation: 'worktree', schema: IMPL })
    if (!fix) break
    branch = fix.branch
    carry.decisions.push(...(fix.decisions || [])); carry.deferrals.push(...(fix.deferrals || []))
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

// --- 1 タスクの全ライフサイクル: 計画→実装→レビュー↔修正→(未承認ならエスカレーション)→polish ---
async function runTask(task) {
  const impl = await agentRetry(implPrompt(task),
    { label: `impl:${task.id}`, phase: 'Implement', ...implOpts(task), isolation: 'worktree', schema: IMPL })
  if (!impl) return { task: task.id, failed: true, reason: `実装起動失敗: ${task.id}` }
  const carry = { decisions: impl.decisions || [], deferrals: impl.deferrals || [] }
  // DoD が曖昧で計画段階で止まったら、実装・レビューに進まず blocked で返す（監督者がユーザーに確認）
  if (impl.blocked) return { task: task.id, branch: impl.branch, blocked: true, questions: impl.questions, ...carry }

  const review = await runReview({ id: task.id, branch: impl.branch, task })   // standard は通常＋敵対的、light は通常のみ
  let r = await fixReviewLoop({ id: task.id, startBranch: impl.branch, review, task, carry })

  // タスク内ループで直しきれなければ、このタスク単体でエスカレーション（再計画 → fix↔review）
  if (!r.approved) {
    const esc = { task: task.id, branch: r.branch, pr: impl.pr, dod: task.dod, findings: r.review?.findings || [] }
    const plan = await agentRetry(replanPrompt(esc),
      { label: `replan:${task.id}`, phase: 'Escalation', model: 'fable', isolation: 'worktree', schema: PLAN })
    r = await fixReviewLoop({ id: `esc:${task.id}`, startBranch: r.branch,
      review: { approved: false, findings: esc.findings },
      task: { ...task, replan: plan?.plan }, carry })
    if (!r.approved)
      return { ...esc, branch: r.branch, findings: r.review?.findings || [],
               failed: true, reason: `エスカレーション未解決: ${task.id}`, ...carry }
  }

  // polish: 承認済みでも should-fix が残っていれば 1 回だけ清掃する（素通り防止）。
  // 再レビューが新規の should-fix を出しても回し続けない（収束しないため）。残りは findings として返す。
  const sfx = shouldFix(r.review)
  if (sfx.length) {
    const fix = await agentRetry(fixPrompt(r.branch, sfx, task),
      { label: `polish:${task.id}`, phase: 'Polish', ...implOpts(task), isolation: 'worktree', schema: IMPL })
    if (fix) {
      carry.decisions.push(...(fix.decisions || [])); carry.deferrals.push(...(fix.deferrals || []))
      const re = fix.changeKind === 'docs'
        ? await runReview({ id: `polish:${task.id}`, branch: fix.branch, task, adversarial: false, effort: 'low' })
        : await runReview({ id: `polish:${task.id}`, branch: fix.branch, task })
      // polish が must-fix を持ち込んだ場合だけ、通常の fix ループで回収する
      const rec = re.approved ? { branch: fix.branch, approved: true, review: re }
        : await fixReviewLoop({ id: `polish-fix:${task.id}`, startBranch: fix.branch, review: re, task, carry })
      if (rec.approved) r = rec   // 回収できなければ polish 前の承認済みブランチを保持する
    }
  }

  return { task: task.id, pr: impl.pr, branch: r.branch, dod: task.dod, approved: true,
           findings: r.review?.findings || [], ...carry }
}

// --- ステップ本体: 全タスクを並列に起動し、承認済みブランチ一覧を返す（統合は監督者） ---
const results = (await parallel(tasks.map(t => () => runTask({ ...t, startFrom: TOPIC })))).filter(Boolean)

return {
  topic: TOPIC,
  tasks: results,
  approved: results.filter(r => r.approved).map(r => ({ task: r.task, branch: r.branch, pr: r.pr })),
  blocked: results.filter(r => r.blocked).map(r => ({ task: r.task, questions: r.questions })),
  failed: results.filter(r => r.failed).map(r => ({ task: r.task, reason: r.reason, findings: r.findings })),
  decisions: results.flatMap(r => r.decisions || []),
  deferrals: results.flatMap(r => r.deferrals || []),
}
```

各 `task` オブジェクトには `id` / `dod` に加え `tier`（`"light"` | `"standard"`）と構造化仕様
（受け入れ基準・スコープ境界・調査の入口・隣接タスクとの契約。SKILL.md §4）を持たせ、Workflow ツールの
`args` に **実オブジェクトとして** `{ topic, tasks }` を渡す。`tier` が `implOpts` と `runReview` の
厳格さ（モデル・effort・敵対的レビューの有無）を決める。

`implPrompt` / `reviewPrompt` / `adversarialReviewPrompt` / `fixPrompt` / `replanPrompt` は、
監督者が「プロジェクト前提の解決」で特定した契約
（DoD・検証コマンド一式・外形動作の確認手順・不可侵パス・ブランチ規約・起点の指定）を封入
して組み立てる。解法（変更ファイルの列挙・行番号つき手順）は封入しない
（[implementation-prompt.md](implementation-prompt.md) の §0）。`implPrompt` / `fixPrompt` は返り値に
`changeKind`（doc のみか）・`blocked`＋`questions`（DoD が曖昧なら実装せず返す）・`decisions`
（変えた目標）・`deferrals`（先延ばし）を含めるよう指示する。

## 監督者側の受け取り

ワークフローの返り値（`approved` / `blocked` / `failed` / `findings` / `decisions` / `deferrals`）を
受け取ったら、**内容を鵜呑みにせず実地検証してから**（[integration.md](integration.md) §1）、
統合レーンを回す。`blocked` / `failed` があればユーザーへのエスカレーション材料にする（承認済み分の
統合は進めてよい）。
