# オーケストレーションスクリプトの骨組み（テンプレート）

リードが `Workflow` ツールに渡す dynamic workflow スクリプトの骨組み。**1 タスク = 1 ワークフロー**で、
範囲は「レビュー承認まで」。実装 → レビュー → 裁定 → 修正を回し、承認したブランチと PR 番号を返す。
**topic へのマージと PR のクローズはスクリプトに載せない**（[integration.md](integration.md) の
とおりリード本体が行う。理由は [design-notes.md](design-notes.md)）。

各 `agent()` のプロンプトは、[implementation-prompt.md](implementation-prompt.md) /
[review-prompt.md](review-prompt.md) / [judge-prompt.md](judge-prompt.md) /
[escalation-prompt.md](escalation-prompt.md) の契約に従って組み立てる。

## 守ること

- **`agent()` の入れ子はできない。** ループ・分岐・打ち切り条件はスクリプトの制御フローに書き、
  `agent()` には実務だけを載せる。
- **エージェントは再開できない。** `agent()` に再開の引数は無く、`SendMessage` の宛先にもならない。
  文脈は `<ベース>/notes/task<番号>/` の引き継ぎノートで運ぶ（[design-notes.md](design-notes.md)）。
- **ブランチを checkout する全エージェントに `isolation: 'worktree'` を付ける**（実装・修正・
  レビュー・裁定・再計画）。worktree の中は detached HEAD で作業させ、push は
  `git push origin HEAD:refs/heads/<ブランチ>` にする。
- **修正とレビューは必ず別の `agent()`**（修正の自己承認を防ぐ）。
- **すべての `agent()` に `model` を明示し、機械的な段階には `effort` を効かせる**
  （`SKILL.md` の「役割ごとのモデルと effort」）。
- **指摘の本文は返り値に含めない。** 本文は GitHub のレビュースレッドにあり、返すのは件数と
  検証の要約だけ（[github-comments.md](github-comments.md)）。
- **`args` は実オブジェクトで渡す。** JSON 文字列で渡すとスクリプト側で全フィールドが
  `undefined` になるので、冒頭で防御パースと必須フィールドの検査を行う。
- `Date.now()` / `Math.random()` / 引数なしの `new Date()` は使えない（起動が拒否される）。

## 呼び出し方

```
Workflow({ script: <下の骨組み>, args: {
  task: { id, subject, tier, branch, dod, acceptance, scope, entrypoints, contracts },
  topic: "topic/<作業名>",
  base:  "<place.py base-dir が返した絶対パス>",
  work:  "<作業名>",
  resumeFrom: { branch, sha, pr, transcriptDir }   // 立て直しのときだけ。無ければ省く
}})
```

`light` を束ねたタスクは `task.dod` に束ねた全 DoD を並べ、`task.id` は代表の番号にする
（ブランチと PR は 1 本にまとめる）。

## 骨組み

```javascript
export const meta = {
  name: 'supervisor-task',
  description: '1 タスクを実装→レビュー→裁定→修正で承認まで進め、ブランチと PR を返す（統合はリード）',
  phases: [
    { title: 'Implement' }, { title: 'Review' }, { title: 'Judge' },
    { title: 'Fix' }, { title: 'Escalation' }, { title: 'Polish' },
  ],
}

// --- args の防御パース（JSON 文字列で渡された場合に備える） ---
const input = typeof args === 'string' ? JSON.parse(args) : args
const task = input?.task
const TOPIC = input?.topic          // 例: topic/<作業名>
const BASE = input?.base            // place.py base-dir が返した絶対パス
const RESUME = input?.resumeFrom    // { branch, sha, pr, transcriptDir } | undefined
if (!task?.id || !task?.branch || !TOPIC || !BASE)
  return { failed: true, reason: 'args.task / args.topic / args.base が不正（実オブジェクトで渡すこと）' }

const LIGHT = task.tier === 'light'
const NOTES = `${BASE}/notes/${task.id}`                       // 引き継ぎノートの置き場
const ROLES = LIGHT ? ['review:normal'] : ['review:normal', 'review:adversarial']
const implOpts = LIGHT ? { model: 'sonnet', effort: 'medium' } : { model: 'opus' }

// --- schema（返すのは判断に要る値だけ。指摘の本文は GitHub のスレッドにある） ---
const IMPL = { type: 'object', properties: {
  branch: { type: 'string' }, pr: { type: 'number' },
  commits: { type: 'array', items: { type: 'string' } },
  changeKind: { enum: ['docs', 'logic'] },     // 再レビューの軽重を決める
  summary: { type: 'string' },
  verified: { type: 'boolean' }, verification: { type: 'string' },
  blocked: { type: 'boolean' },                 // DoD が曖昧で実装に入れない（計画の段階で判定）
  questions: { type: 'string' },                // blocked のときリードに確認したい点
  decisions: { type: 'array', items: { type: 'string' } },   // 自分の判断で変えた目標・DoD・スコープ
  deferrals: { type: 'array', items: { type: 'string' } },   // 先送り・対象外にした作業
}, required: ['branch', 'summary'] }

const REVIEW = { type: 'object', properties: {
  verdict: { enum: ['approved', 'changes-requested', 'blocked'] },
  mustFix: { type: 'number' }, shouldFix: { type: 'number' }, nit: { type: 'number' },
  resolved: { type: 'number' }, stillOpen: { type: 'number' },   // 再レビューのとき
  reviewUrl: { type: 'string' },
  notes: { type: 'string' },   // 実施した検証と、問題なしと判断した観点。空なら no-op を疑う
}, required: ['verdict', 'mustFix', 'notes'] }

const JUDGE = { type: 'object', properties: {
  remainingMustFix: { type: 'number' },      // 裁定を通したあとに残る must-fix
  remainingShouldFix: { type: 'number' },
  overruled: { type: 'number' }, upheld: { type: 'number' },
  unresolved: { type: 'number' },            // 裁定後に未解決で残っているスレッド数
  gateClean: { type: 'boolean' },            // must-fix も should-fix も 0 のとき gate を叩いた結果
  notes: { type: 'string' },
  decisions: { type: 'array', items: { type: 'string' } },
  deferrals: { type: 'array', items: { type: 'string' } },
}, required: ['remainingMustFix', 'unresolved', 'notes'] }

const PLAN = { type: 'object', properties: {
  plan: { type: 'string' }, unsolvable: { type: 'boolean' }, reason: { type: 'string' },
}, required: ['plan'] }

// --- 共通の前置き（worktree の規律・前提資料・引き継ぎノート） ---
// startPoint はこのエージェントが detached で載る先。実装の初回だけ topic、以降はタスクブランチ。
const preamble = (role, round, startPoint) => `
カレントディレクトリ（割り当てられた worktree）で作業する。他のディレクトリのチェックアウトに
触れない。リポジトリの絶対パスはプロンプトに書かれていない。

1. \`git fetch origin\` を実行する。
2. \`git checkout --detach ${startPoint}\` で起点に載る（ブランチ名を checkout しない）。
3. 前提資料を読む（git の追跡対象外なので checkout では作業ツリーに現れない）:
   ${BASE}/brief.md（検証コマンド・外形動作の手順・不可侵パス・規約）
   ${BASE}/map.md（コードベースの入口）
   ${BASE}/ledger.md（他タスクとの関係）
4. **コードを読む前に引き継ぎノートを読む**: ${NOTES}/${role}-*.md（あるものすべて）。
   前のラウンドの同じ役割が、読んだ箇所・実行した検証と結果・構造の要点を残している。
5. 終える前に \`mkdir -p ${NOTES}\` して ${NOTES}/${role}-r${round}.md を書く。
   中身は「読んだファイルとその要点」「実行したコマンドとその結果」「まだ確かめていない箇所」。
   会話をそのまま貼らない。次のエージェントがコードを読み直さずに済む要約にする。
6. push は \`git push origin HEAD:refs/heads/${task.branch}\` で行う（リモートにだけ作る）。
`

// --- agent() の起動失敗（null）と no-op を 1 回だけリトライする ---
// isNoop: 結果はあるが実作業の痕跡が無い応答（定型文）を検出する述語（省略可）
async function agentRetry(prompt, opts, isNoop) {
  let r = await agent(prompt, opts)
  if (!r || (isNoop && isNoop(r)))
    r = await agent(prompt, { ...opts, label: `${opts.label}:retry` })
  return r
}

// --- 1 ラウンドのレビュー。standard は通常＋敵対的を並列、light は通常 1 本 ---
// 走らせた全レビューが approved を返したときだけ approved。null が 1 つでもあれば未承認。
async function runReview({ round, adversarial = !LIGHT, effort = 'medium' }) {
  const noop = r => !r.notes                       // 検証の記録が無い応答は no-op を疑う
  const runs = [
    () => agentRetry(reviewPrompt('review:normal', round),
      { label: `review:${task.id}#${round}`, phase: 'Review',
        model: LIGHT ? 'sonnet' : 'opus', effort, isolation: 'worktree', schema: REVIEW }, noop),
  ]
  if (adversarial) runs.push(
    () => agentRetry(reviewPrompt('review:adversarial', round),
      { label: `review-adv:${task.id}#${round}`, phase: 'Review',
        model: 'opus', effort, isolation: 'worktree', schema: REVIEW }, noop))
  const done = await parallel(runs)
  const ok = done.filter(Boolean)
  return {
    approved: ok.length === runs.length && ok.every(r => r.verdict === 'approved'),
    blocked: ok.some(r => r.verdict === 'blocked'),
    mustFix: ok.reduce((n, r) => n + (r.mustFix || 0), 0),
    shouldFix: ok.reduce((n, r) => n + (r.shouldFix || 0), 0),
    missing: runs.length - ok.length,             // 起動できなかったレビュー
  }
}

// --- 状態（ループとエスカレーションで共有する） ---
const carry = { decisions: [], deferrals: [] }
const collect = r => {
  carry.decisions.push(...(r?.decisions || []))
  carry.deferrals.push(...(r?.deferrals || []))
}
let branch = task.branch, pr = RESUME?.pr, changeKind = 'logic'

// 途中で止まったときの共通の返し方
const fail = reason => ({ task: task.id, branch, pr, failed: true,
                          reason, notesDir: NOTES, ...carry })

// --- レビュー → 裁定 → 修正のループ（初回とエスカレーション後で共通） ---
// 打ち切りは 2 つ: ラウンド上限に達した / 無進捗（must-fix が前ラウンド以上）
async function reviewFixLoop({ tag = '', maxRounds = 3 }) {
  let prevMustFix = Infinity
  for (let i = 1; ; i++) {
    const round = `${tag}${i}`
    const review = await runReview({ round,
      adversarial: !LIGHT && changeKind !== 'docs',
      effort: changeKind === 'docs' ? 'low' : 'medium' })
    if (review.blocked)
      return { approved: false, blocked: true, round,
               reason: 'タスクブランチかベース資料が見つからない' }

    const judge = await agentRetry(judgePrompt(round),
      { label: `judge:${task.id}#${round}`, phase: 'Judge',
        model: 'opus', effort: 'medium', isolation: 'worktree', schema: JUDGE })
    if (!judge)
      return { approved: false, round, reason: '裁定エージェントが起動しなかった（2 回）' }
    collect(judge)

    if (judge.remainingMustFix === 0)
      return { approved: true, round, shouldFix: judge.remainingShouldFix ?? review.shouldFix,
               unresolved: judge.unresolved, gateClean: judge.gateClean }
    if (i >= maxRounds)
      return { approved: false, round, reason: `ラウンド上限（must-fix ${judge.remainingMustFix} 件）` }
    if (judge.remainingMustFix >= prevMustFix)
      return { approved: false, round, reason: `無進捗（must-fix ${judge.remainingMustFix} 件）` }
    prevMustFix = judge.remainingMustFix

    const fix = await agentRetry(fixPrompt(`${tag}${i + 1}`),
      { label: `fix:${task.id}#${tag}${i + 1}`, phase: 'Fix',
        ...implOpts, isolation: 'worktree', schema: IMPL })
    if (!fix)
      return { approved: false, round, reason: '修正エージェントが起動しなかった（2 回）' }
    collect(fix)
    branch = fix.branch || branch
    changeKind = fix.changeKind || 'logic'
  }
}

// --- 1. 実装（resumeFrom があれば続きから） ---
const impl = await agentRetry(implPrompt('impl', 0, RESUME),
  { label: `impl:${task.id}`, phase: 'Implement', ...implOpts, isolation: 'worktree', schema: IMPL })
if (!impl)
  return { task: task.id, failed: true, reason: '実装エージェントが起動しなかった（2 回）' }
collect(impl)
if (impl.blocked)
  return { task: task.id, branch: impl.branch, pr: impl.pr, blocked: true,
           questions: impl.questions, ...carry }
branch = impl.branch || branch
pr = impl.pr || pr
changeKind = impl.changeKind || 'logic'
if (!pr)
  return { task: task.id, branch, failed: true, reason: '実装が PR 番号を返さなかった', ...carry }

// --- 2. レビュー → 裁定 → 修正（3 ラウンドまで） ---
let r = await reviewFixLoop({ tag: '', maxRounds: 3 })

// レビューが起点に載れなかった（ブランチが push されていない・ベース資料が無い）。
// エスカレーションしても同じ壁に当たるので、ここで返す
if (r.blocked) return fail(`レビューが起点に載れなかった: ${r.reason}`)

// --- 3. 直しきれなければ 再計画 → impl-b → 新規レビューでやり直す ---
if (!r.approved) {
  const plan = await agentRetry(replanPrompt(r.reason),
    { label: `replan:${task.id}`, phase: 'Escalation',
      model: 'opus', isolation: 'worktree', schema: PLAN })
  if (!plan) return fail(`再計画エージェントが起動しなかった（2 回）。直前: ${r.reason}`)
  if (plan.unsolvable) return fail(`解決不能と判断された: ${plan.reason}`)

  const implB = await agentRetry(implPrompt('impl-b', 0, null, plan.plan),
    { label: `impl-b:${task.id}`, phase: 'Escalation',
      model: 'opus', isolation: 'worktree', schema: IMPL })
  if (!implB) return fail(`impl-b が起動しなかった（2 回）。直前: ${r.reason}`)
  collect(implB)
  branch = implB.branch || branch
  changeKind = implB.changeKind || 'logic'

  r = await reviewFixLoop({ tag: 'b', maxRounds: 3 })   // レビューは新規に立て直す
  if (!r.approved) return fail(`impl-b でも承認に至らなかった: ${r.reason}`)
}

// --- 4. polish: 承認済みでも should-fix が残っていれば 1 回だけ清掃する ---
if ((r.shouldFix || 0) > 0) {
  const fix = await agentRetry(polishPrompt(),
    { label: `polish:${task.id}`, phase: 'Polish', ...implOpts, isolation: 'worktree', schema: IMPL })
  if (fix) {
    collect(fix)
    branch = fix.branch || branch
    changeKind = fix.changeKind || 'logic'
    const after = await reviewFixLoop({ tag: 'p', maxRounds: 2 })
    // polish は最小の清掃なので must-fix を持ち込まないはずだが、持ち込んだら回収する。
    // 回収できなければ失敗として返す（commit は push 済みで、承認前の状態には戻せない）
    if (!after.approved)
      return fail(`polish が持ち込んだ指摘を回収できなかった: ${after.reason}`)
    r = after
  }
}

// --- 5. 返す（統合はリードが行う） ---
return {
  task: task.id, tier: task.tier, branch, pr,
  approved: true,
  requireRoles: ROLES,          // リードが gate --require-roles に渡す
  shouldFix: r.shouldFix || 0,  // 畳んだが直していない残件（threads --all で拾える）
  gateClean: r.gateClean,       // 裁定が最後に叩いた門の結果。リードは自分でも叩き直す
  lastRound: r.round,
  notesDir: NOTES,
  decisions: carry.decisions,
  deferrals: carry.deferrals,
}
```

## プロンプト組み立て関数

骨組みが呼んでいる 6 つの関数は、契約ファイルに従ってリードが組み立てる。どれも先頭に
`preamble(役割, ラウンド, 起点)` を置く。

| 関数 | 契約 | 起点 | 役割タグ |
| --- | --- | --- | --- |
| `implPrompt(role, round, resume, plan)` | [implementation-prompt.md](implementation-prompt.md) | 初回は `origin/${TOPIC}`、`resume` があれば `origin/${task.branch}` | `impl:a` / `impl:b` |
| `fixPrompt(round)` | [implementation-prompt.md](implementation-prompt.md) の「修正ラウンド」 | `origin/${task.branch}` | `impl:a`（impl-b の後は `impl:b`） |
| `polishPrompt()` | 同上。**残っている should-fix だけを直し、範囲を広げない** | `origin/${task.branch}` | 同上 |
| `reviewPrompt(role, round)` | [review-prompt.md](review-prompt.md) | `origin/${task.branch}` | `review:normal` / `review:adversarial` |
| `judgePrompt(round)` | [judge-prompt.md](judge-prompt.md) | `origin/${task.branch}` | `judge:task<番号>` |
| `replanPrompt(reason)` | [escalation-prompt.md](escalation-prompt.md) | `origin/${task.branch}` | 投稿しない（読み取りのみ） |

どのプロンプトにも次を封入する。

- タスクの DoD・受け入れ基準と検証・スコープ境界・調査の入口・隣接タスクとの契約・`tier`
- タスクブランチ名（`${task.branch}`）、base ブランチ名（`${TOPIC}`）、PR 番号（作られた後）
- ベース資料のパス（`${BASE}`）と引き継ぎノートのパス（`${NOTES}`）
- **解法は封入しない**（変更するファイルの一覧・行番号つきの手順。理由は
  [implementation-prompt.md](implementation-prompt.md) §0）

## リード側の受け取り

返り値（`approved` / `blocked` / `failed` / `branch` / `pr` / `requireRoles` / `shouldFix` /
`decisions` / `deferrals`）を受け取ったら、**内容を鵜呑みにせず実地検証してから**
（[integration.md](integration.md) §1 の `verify.py` と `gh-review.py gate`）取り込む。

- `blocked` — `questions` をユーザーに上げ、答えを受けてタスクを組み直して起動し直す
- `failed` — `reason` と PR のスレッドを見て、ユーザーに上げるか、`resumeFrom` を組み立てて
  起動し直す
- `notesDir` — 立て直しのとき、新しいワークフローの引き継ぎノートの置き場として同じパスを使う
