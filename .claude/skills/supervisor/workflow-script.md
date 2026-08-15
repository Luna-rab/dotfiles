# オーケストレーションスクリプトの骨組み（テンプレート）

リードが `Workflow` ツールに渡す dynamic workflow スクリプトの骨組み。**1 タスク = 1 ワークフロー**で、
範囲は「全レビューが決着し、PR 本文が書き上がるまで」。実装 → レビュー → 裁定 → 修正を回し、
承認したブランチと PR 本文のパスを返す。

**PR は作らない。** ワークフローが返したブランチを、リードが PR にしてから topic へ取り込む
（[integration.md](integration.md)。理由は [design-notes.md](design-notes.md)）。

各 `agent()` のプロンプトは、[implementation-prompt.md](implementation-prompt.md) /
[review-prompt.md](review-prompt.md) / [judge-prompt.md](judge-prompt.md) /
[escalation-prompt.md](escalation-prompt.md) / [pr-body-prompt.md](pr-body-prompt.md) の契約に従って
組み立てる。

## 目次

どの節に何が書いてあるか。必要な節だけ読めばよい。

- 守ること
- 呼び出し方
- 役割ごとのモデルと effort
- 骨組み
- プロンプト組み立て関数
- リード側の受け取り

## 守ること

- **`agent()` の入れ子はできない。** ループ・分岐・打ち切り条件はスクリプトの制御フローに書き、
  `agent()` には実務だけを載せる。
- **エージェントは再開できない。** `agent()` に再開の引数は無く、`SendMessage` の宛先にもならない。
  文脈は `<ベース>/notes/task<番号>/` の引き継ぎノートで運ぶ（[design-notes.md](design-notes.md)）。
- **ブランチを checkout する全エージェントに `isolation: 'worktree'` を付ける**（実装・修正・
  レビュー・裁定・再計画・PR 本文）。worktree の中は detached HEAD で作業させ、push は
  `git push origin HEAD:refs/heads/<ブランチ>` にする。
- **修正とレビューは必ず別の `agent()`**（修正の自己承認を防ぐ）。
- **すべての `agent()` に `model` を明示し、機械的な段階には `effort` を効かせる**
  （下の「役割ごとのモデルと effort」）。
- **GitHub には誰も投稿しない。** 指摘は `<ベース>/notes/task<番号>/review.json` に置き、
  `review.py` で読み書きする（[review-store.md](review-store.md)）。PR を作るのはリードである。
- **指摘の本文は返り値に含めない。** 本文は review.json にあり、返すのは件数と検証の要約だけである。
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
  topicPr: 100,                              // SKILL.md §4 で作った topic PR の番号
  resumeFrom: { branch, sha, transcriptDir } // 立て直しのときだけ。無ければ省く
}})
```

`light` を束ねたタスクは `task.dod` に束ねた全 DoD を並べ、`task.id` は代表の番号にする
（ブランチと PR は 1 本にまとめる）。

## 役割ごとのモデルと effort

`agent()` には**必ず `model` を明示する**（セッション既定の継承に頼らない）。機械的な段階には
`effort` を効かせる。

| 役割 | model | effort |
| --- | --- | --- |
| リード（`/supervisor` のセッション本体） | `opus` | — |
| 実装・修正（standard） | `opus` | 既定 |
| 実装・修正（light） | `sonnet` | `medium` |
| 実装の差し替え（impl-b） | `opus` | 既定 |
| 通常レビュー（standard） | `opus` | `medium` |
| 通常レビュー（light） | `sonnet` | `medium` |
| 敵対的レビュー（standard のみ） | `opus` | `medium` |
| doc だけの修正の再レビュー | `sonnet` | `low` |
| 裁定 | `opus` | `medium` |
| 再計画（エスカレーション） | `opus` | 既定 |
| PR 本文 | `opus` | 既定 |
| Explore 調査（リードの意思決定用） | `opus` | 既定 |

指定したモデルが使えなければ 1 つ下げる（`opus` → `sonnet`）。`sonnet` も使えなければ `model` を
省く。

**ワークフロー内のエージェントには `isolation: 'worktree'` を必ず付ける。**

## 骨組み

```javascript
export const meta = {
  name: 'supervisor-task',
  description: '1 タスクを実装→レビュー→裁定→修正で決着させ、ブランチと PR 本文を返す（PR 作成はリード）',
  phases: [
    { title: 'Implement' }, { title: 'Review' }, { title: 'Judge' },
    { title: 'Fix' }, { title: 'Escalation' }, { title: 'PR' },
  ],
}

// --- args の防御パース（JSON 文字列で渡された場合に備える） ---
const input = typeof args === 'string' ? JSON.parse(args) : args
const task = input?.task
const TOPIC = input?.topic          // 例: topic/<作業名>
const BASE = input?.base            // place.py base-dir が返した絶対パス
const TOPIC_PR = input?.topicPr     // topic PR の番号。タスク PR のタイトルに入る
const RESUME = input?.resumeFrom    // { branch, sha, transcriptDir } | undefined
if (!task?.id || !task?.branch || !TOPIC || !BASE || !TOPIC_PR)
  return { failed: true, reason: 'args.task / args.topic / args.base / args.topicPr が不正（実オブジェクトで渡すこと）' }

const LIGHT = task.tier === 'light'
const NOTES = `${BASE}/notes/${task.id}`         // 引き継ぎノートと review.json の置き場
const REVIEWERS = LIGHT ? ['review:normal'] : ['review:normal', 'review:adversarial']
const implOpts = LIGHT ? { model: 'sonnet', effort: 'medium' } : { model: 'opus' }

// --- schema（返すのは判断に要る値だけ。指摘の本文は review.json にある） ---
const IMPL = { type: 'object', properties: {
  branch: { type: 'string' },
  commits: { type: 'array', items: { type: 'string' } },
  changeKind: { enum: ['docs', 'logic'] },     // 再レビューの軽重を決める
  summary: { type: 'string' },
  verified: { type: 'boolean' }, verification: { type: 'string' },
  blocked: { type: 'boolean' },                 // DoD が曖昧で実装に入れない（計画の段階で判定）
  questions: { type: 'string' },                // blocked のときリードに確認したい点
  answered: { type: 'number' },                 // 修正ラウンドで返信した review の件数
  decisions: { type: 'array', items: { type: 'string' } },   // 自分の判断で変えた目標・DoD・スコープ
  deferrals: { type: 'array', items: { type: 'string' } },   // 先送り・対象外にした作業
}, required: ['branch', 'summary'] }

// レビュアーは review.json に書くだけ。status を動かさないので verdict に承認は無い
const REVIEW = { type: 'object', properties: {
  verdict: { enum: ['reported', 'blocked'] },
  opened: { type: 'number' },               // このラウンドで立てた review の件数
  mustFix: { type: 'number' }, shouldFix: { type: 'number' }, nit: { type: 'number' },
  commented: { type: 'number' },            // 既存の open に付けたコメントの件数
  notes: { type: 'string' },   // 実施した検証と、問題なしと判断した観点。空なら no-op を疑う
}, required: ['verdict', 'opened', 'notes'] }

const JUDGE = { type: 'object', properties: {
  openTotal: { type: 'number' },        // 裁定を通した後に open で残っている件数
  openMustFix: { type: 'number' },      // そのうち rating が must-fix のもの
  closed: { type: 'number' },           // このラウンドで closed にした件数
  rejected: { type: 'number' },         // このラウンドで rejected にした件数
  reopened: { type: 'number' },         // 再オープンした件数
  notes: { type: 'string' },
  decisions: { type: 'array', items: { type: 'string' } },
  deferrals: { type: 'array', items: { type: 'string' } },
}, required: ['openTotal', 'openMustFix', 'notes'] }

const PLAN = { type: 'object', properties: {
  plan: { type: 'string' }, unsolvable: { type: 'boolean' }, reason: { type: 'string' },
}, required: ['plan'] }

const PRBODY = { type: 'object', properties: {
  title: { type: 'string' },             // リードが gh pr create --title にそのまま渡す
  bodyFile: { type: 'string' },          // 書いた本文の絶対パス
  behaviorChange: { type: 'boolean' },   // 挙動が変わるか
  notes: { type: 'string' },
}, required: ['title', 'bodyFile'] }

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
// レビュアーは review.json に指摘を立てるだけで、status は動かさない（動かすのは裁定）。
// ここが返すのは件数と「全員が結果を返したか」だけである。
async function runReview({ round, adversarial = !LIGHT, effort = 'medium' }) {
  const noop = r => !r.notes                       // 検証の記録が無い応答は no-op を疑う
  const reviews = [
    () => agentRetry(reviewPrompt('review:normal', round),
      { label: `review:${task.id}#${round}`, phase: 'Review',
        model: LIGHT ? 'sonnet' : 'opus', effort, isolation: 'worktree', schema: REVIEW }, noop),
  ]
  if (adversarial) reviews.push(
    () => agentRetry(reviewPrompt('review:adversarial', round),
      { label: `review-adv:${task.id}#${round}`, phase: 'Review',
        model: 'opus', effort, isolation: 'worktree', schema: REVIEW }, noop))

  const ok = (await parallel(reviews)).filter(Boolean)
  return {
    blocked: ok.some(r => r.verdict === 'blocked'),
    opened: ok.reduce((n, r) => n + (r.opened || 0), 0),
    // 起動できなかったレビュー。review.json を見ても「走ったが指摘 0 件」と区別が付かないので、
    // 「2 体が走った」ことを担保するのはこの検査だけである。0 でなければ裁定に進ませない
    missing: reviews.length - ok.length,
    // 実際に結果を返した体数。tier から導いた期待値ではなく実測値なので、リードが
    // 「standard なのに 1 体しか走っていない」を検出できる（SKILL.md §7「完了の根拠」）
    reviewers: ok.length,
    expected: reviews.length,
  }
}

// --- 状態（ループとエスカレーションで共有する） ---
const carry = { decisions: [], deferrals: [] }
const collect = r => {
  carry.decisions.push(...(r?.decisions || []))
  carry.deferrals.push(...(r?.deferrals || []))
}
let branch = task.branch, changeKind = 'logic'

// 途中で止まったときの共通の返し方
const fail = reason => ({ task: task.id, branch, failed: true,
                          reason, notesDir: NOTES, reviewFile: `${NOTES}/review.json`, ...carry })

// --- レビュー → 裁定 → 修正のループ（初回とエスカレーション後で共通） ---
// 決着 = review.json の open が 0 件（全件が closed か rejected）。
// 打ち切りは 2 つ: ラウンド上限に達した / 無進捗。
// 無進捗は「open の総数」と「open の must-fix」の**両方が前ラウンド以上**で判定する。
// must-fix だけで見ると、must-fix が 0 で should-fix が残っている局面（どちらも前ラウンドと
// 同じ 0 件）が無進捗に見えて、直せるはずの should-fix を残したまま打ち切る。
//
// 戻り値の `infra: true` は「エージェントが起動しなかった」＝実装の欠陥ではない打ち切りである。
// 呼び出し元はこれを再計画レーンに流さない（「3.」の直前で分岐している）
async function reviewFixLoop({ tag = '', maxRounds = 3 }) {
  let prevTotal = Infinity, prevMustFix = Infinity
  for (let i = 1; ; i++) {
    const round = `${tag}${i}`
    const review = await runReview({ round,
      adversarial: !LIGHT && changeKind !== 'docs',
      effort: changeKind === 'docs' ? 'low' : 'medium' })
    if (review.blocked)
      return { done: false, blocked: true, round,
               reason: 'タスクブランチかベース資料が見つからない' }
    if (review.missing > 0)
      return { done: false, infra: true, round,
               reason: `レビューが ${review.missing} 本起動しなかった（2 回試行）` }

    const judge = await agentRetry(judgePrompt(round),
      { label: `judge:${task.id}#${round}`, phase: 'Judge',
        model: 'opus', effort: 'medium', isolation: 'worktree', schema: JUDGE })
    if (!judge)
      return { done: false, infra: true, round,
               reason: '裁定エージェントが起動しなかった（2 回）' }
    collect(judge)

    // 決着したこのラウンドの実測値を返す。リードがこれと expected を突き合わせる
    if (judge.openTotal === 0)
      return { done: true, round, closed: judge.closed, rejected: judge.rejected,
               reviewers: review.reviewers, expected: review.expected }
    if (i >= maxRounds)
      return { done: false, round,
               reason: `ラウンド上限（open ${judge.openTotal} 件 / must-fix ${judge.openMustFix} 件）` }
    if (judge.openTotal >= prevTotal && judge.openMustFix >= prevMustFix)
      return { done: false, round, reason: `無進捗（open ${judge.openTotal} 件）` }
    prevTotal = judge.openTotal
    prevMustFix = judge.openMustFix

    const fix = await agentRetry(fixPrompt(`${tag}${i + 1}`),
      { label: `fix:${task.id}#${tag}${i + 1}`, phase: 'Fix',
        ...implOpts, isolation: 'worktree', schema: IMPL })
    if (!fix)
      return { done: false, infra: true, round,
               reason: '修正エージェントが起動しなかった（2 回）' }
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
  return { task: task.id, branch: impl.branch, blocked: true,
           questions: impl.questions, ...carry }
branch = impl.branch || branch
changeKind = impl.changeKind || 'logic'

// --- 2. レビュー → 裁定 → 修正（3 ラウンドまで） ---
let r = await reviewFixLoop({ tag: '', maxRounds: 3 })

// レビューが起点に載れなかった（ブランチが push されていない・ベース資料が無い）。
// エスカレーションしても同じ壁に当たるので、ここで返す
if (r.blocked) return fail(`レビューが起点に載れなかった: ${r.reason}`)

// エージェントが起動しなかった打ち切り。**実装の欠陥ではないので再計画レーンに流さない。**
// 流すと、まだ 1 度も裁定を受けていない実装を impl-b が方針から作り直すことになる
// （replanPrompt / implPrompt('impl-b') は「前の実装を捨てて作り直す」契約である）。
// リードが resumeFrom を組み立てて同じ実装の続きから立て直すのが正しい
if (r.infra) return fail(`エージェントが起動しなかった: ${r.reason}`)

// --- 3. 直しきれなければ 再計画 → impl-b → 新規レビューでやり直す ---
if (!r.done) {
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
  if (!r.done) return fail(`impl-b でも決着に至らなかった: ${r.reason}`)
}

// --- 4. PR 本文を書く（PR を作るのはリード） ---
const prBody = await agentRetry(prBodyPrompt(),
  { label: `pr-body:${task.id}`, phase: 'PR',
    model: 'opus', isolation: 'worktree', schema: PRBODY })

// --- 5. 返す（PR 作成と取り込みはリードが行う） ---
return {
  task: task.id, tier: task.tier, branch,
  approved: true,
  // 決着したラウンドで**実際に結果を返した**レビュアーの体数と、tier から決まる期待体数。
  // 2 つを別々に返すのは、片方だけでは食い違いを検出できないためである（tier から導いた
  // 値だけを返すと、何が起きても tier と一致してリードの突き合わせが空振りする）
  reviewers: r.reviewers, expectedReviewers: r.expected,
  reviewerRoles: REVIEWERS,      // 走らせるはずだった役割（記録用）
  reviewFile: `${NOTES}/review.json`,   // リードが --require-empty で確かめる
  closed: r.closed, rejected: r.rejected,
  prTitle: prBody?.title,        // 無ければリードが最小限の本文で PR を作る
  prBodyFile: prBody?.bodyFile,
  behaviorChange: prBody?.behaviorChange,
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
| `reviewPrompt(role, round)` | [review-prompt.md](review-prompt.md) | `origin/${task.branch}` | `review:normal` / `review:adversarial` |
| `judgePrompt(round)` | [judge-prompt.md](judge-prompt.md) | `origin/${task.branch}` | `judge`（**status を動かせるのはここだけ**） |
| `replanPrompt(reason)` | [escalation-prompt.md](escalation-prompt.md) | `origin/${task.branch}` | 読み取りのみ |
| `prBodyPrompt()` | [pr-body-prompt.md](pr-body-prompt.md) | `origin/${task.branch}` | 書くのは `pr-body.md` だけ |

**6 つすべてに `<スクリプト>` の絶対パスを封入する。** `SKILL.md` の「起動前の確認」で確定した
値である。サブエージェントは `${CLAUDE_SKILL_DIR}` を持たない（あの置換はリードが読む
`SKILL.md` のレンダリング時にだけ起きる）ので、封入し忘れると `review.py` を呼べずに落ちる。
契約ファイルの中で `<スクリプト>/` と書かれている箇所が、この絶対パスに置き換わる想定である。

加えて次を封入する。

- タスクの DoD・受け入れ基準と検証・スコープ境界・調査の入口・隣接タスクとの契約・`tier`
- タスクブランチ名（`${task.branch}`）、起点のブランチ名（`${TOPIC}`）
- **topic PR の番号（`${TOPIC_PR}`）**——`prBodyPrompt` がタイトルの接頭辞
  `[supervisor #<topicPR番号> task<番号>]` に入れる（[topic-pr.md](topic-pr.md)「タイトルの接頭辞」）
- ベース資料のパス（`${BASE}`）と引き継ぎノートのパス（`${NOTES}`）
- **`review.py` に渡す `--dir`**（`${NOTES}`）。全員がここを見る
- **解法は封入しない**（変更するファイルの一覧・行番号つきの手順。理由は
  [implementation-prompt.md](implementation-prompt.md) §0）

役割ごとに次も封入する。

- `reviewPrompt`: **自分の役割タグ**（`--reviewer` に渡す）と、このラウンドの名前
- `judgePrompt`: **このラウンドで走ったレビュアーの役割**（引き継ぎノートに書かせる）
- `prBodyPrompt`: **本文の書き先**（`${NOTES}/pr-body.md`）

## リード側の受け取り

返り値（`approved` / `blocked` / `failed` / `branch` / `reviewFile` / `reviewers` /
`expectedReviewers` / `prTitle` / `prBodyFile` / `decisions` / `deferrals`）を受け取ったら、
**内容を鵜呑みにせず実地検証してから**（[integration.md](integration.md) §1 の `verify.py`・
`review.py list --require-empty`・レビュアーの体数）取り込む。

- `blocked` — `questions` をユーザーに上げ、答えを受けてタスクを組み直して起動し直す
- `failed` — `reason` と review.json を見て、ユーザーに上げるか、`resumeFrom` を組み立てて
  起動し直す。**PR は作られていない**ので、GitHub 上には何も残らない。`reason` が
  「エージェントが起動しなかった」で始まるものは実装の欠陥ではないので、**タスクを組み直さず
  `resumeFrom` で同じ実装の続きから立て直す**
- `reviewers` < `expectedReviewers` — 走るはずのレビュアーが欠けたまま決着している。
  **取り込まない**（[integration.md](integration.md) §1 手順 4）
- `prBodyFile` が空 — PR 本文エージェントが 2 回とも起動しなかった場合である。**PR は作る**
  （成果を GitHub に出す）が、本文は最小限にしてユーザーに知らせる（[integration.md](integration.md) §2）
- `notesDir` — 立て直しのとき、新しいワークフローの引き継ぎノートの置き場として同じパスを使う
