export const meta = {
  name: 'supervisor-task',
  description: '1 タスクを実装→レビュー→裁定→修正で決着させ、ブランチと PR 本文を返す（PR 作成はリード）',
  phases: [
    { title: 'Implement' }, { title: 'Review' }, { title: 'Judge' },
    { title: 'Fix' }, { title: 'Escalation' }, { title: 'PR' },
  ],
}

// このファイルは supervisor スキルに同梱された固定のオーケストレーションスクリプトである。
// リードは組み立てず、次の形で呼ぶだけでよい（詳細は workflow-script.md）。
//
//   Workflow({ scriptPath: `${skillDir}/scripts/task-workflow.js`, args: { ... } })
//
// **各エージェントの契約（何をどう判断するか）はこのファイルに書かない。**
// スキル同梱の .md を、そのエージェント自身に Read させる。理由は 3 つある。
//
//   1. 契約 5 本は合計 51KB ある。args に載せるとリードが起動ごとに 51KB を打ち出すことになり、
//      「リードに長文を書き写させない」という目的が達成できない。
//   2. ここにテンプレート文字列として焼き込むと、同じ文章が .md と .js の 2 か所に増える。
//      片方だけ直す事故が起き、`check-skills.py` のリンク検査も JS 文字列の中までは見ない。
//   3. .md 側が唯一の出所なら、契約を直したときに全エージェントへ同時に効く。
//
// Read を飛ばされる余地は残るので、**破ると事故になる不変条件だけ**を各プロンプトに直書きする
// （下の INVARIANTS）。契約を 1 行も読めなかった場合でも、安全側の規律は守られる。

// --- args の防御パース（JSON 文字列で渡された場合に備える） ---
const input = typeof args === 'string' ? JSON.parse(args) : args
const task = input?.task
const TOPIC = input?.topic          // 例: topic/<作業名>
const BASE = input?.base            // place.py base-dir が返した絶対パス
const WORK = input?.work            // 作業名
const TOPIC_PR = input?.topicPr     // topic PR の番号。タスク PR のタイトルに入る
const SKILL_DIR = input?.skillDir   // このスキルのディレクトリ（絶対パス）
const RESUME = input?.resumeFrom    // { branch, sha, transcriptDir } | undefined

if (!task?.id || !task?.branch || !TOPIC || !BASE || !WORK || !TOPIC_PR || !SKILL_DIR)
  return { failed: true, reason:
    'args.task.id / args.task.branch / args.topic / args.base / args.work / args.topicPr / ' +
    'args.skillDir のいずれかが欠けている（実オブジェクトで渡すこと）' }

const SCRIPTS = `${SKILL_DIR}/scripts`
const LIGHT = task.tier === 'light'
const NOTES = `${BASE}/notes/${task.id}`         // 引き継ぎノートと review.json の置き場
const REVIEWER_ROLES = LIGHT ? ['review:normal'] : ['review:normal', 'review:adversarial']
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
  contractRead: { type: 'boolean' },            // 契約ファイルを実際に読んだか（下の noop 検査で使う）
  decisions: { type: 'array', items: { type: 'string' } },   // 自分の判断で変えた目標・DoD・スコープ
  deferrals: { type: 'array', items: { type: 'string' } },   // 先送り・対象外にした作業
}, required: ['branch', 'summary', 'contractRead'] }

// レビュアーは review.json に書くだけ。status を動かさないので verdict に承認は無い
const REVIEW = { type: 'object', properties: {
  verdict: { enum: ['reported', 'blocked'] },
  opened: { type: 'number' },               // このラウンドで立てた review の件数
  mustFix: { type: 'number' }, shouldFix: { type: 'number' }, nit: { type: 'number' },
  commented: { type: 'number' },            // 既存の open に付けたコメントの件数
  contractRead: { type: 'boolean' },
  notes: { type: 'string' },   // 実施した検証と、問題なしと判断した観点。空なら no-op を疑う
}, required: ['verdict', 'opened', 'notes', 'contractRead'] }

const JUDGE = { type: 'object', properties: {
  openTotal: { type: 'number' },        // 裁定を通した後に open で残っている件数（counts.open）
  openMustFix: { type: 'number' },      // そのうち rating が must-fix のもの（counts.open_must_fix）
  closed: { type: 'number' },           // このラウンドで closed にした件数
  rejected: { type: 'number' },         // このラウンドで rejected にした件数
  reopened: { type: 'number' },         // 再オープンした件数
  contractRead: { type: 'boolean' },
  notes: { type: 'string' },
  decisions: { type: 'array', items: { type: 'string' } },
  deferrals: { type: 'array', items: { type: 'string' } },
}, required: ['openTotal', 'openMustFix', 'notes', 'contractRead'] }

const PLAN = { type: 'object', properties: {
  plan: { type: 'string' }, unsolvable: { type: 'boolean' }, reason: { type: 'string' },
  contractRead: { type: 'boolean' },
}, required: ['plan', 'contractRead'] }

const PRBODY = { type: 'object', properties: {
  title: { type: 'string' },             // リードが gh pr create --title にそのまま渡す
  bodyFile: { type: 'string' },          // 書いた本文の絶対パス
  behaviorChange: { type: 'boolean' },   // 挙動が変わるか
  contractRead: { type: 'boolean' },
  notes: { type: 'string' },
}, required: ['title', 'bodyFile', 'contractRead'] }

// --- 共通の前置き（worktree の規律・前提資料・引き継ぎノート） ---
// startPoint はこのエージェントが detached で載る先。実装の初回だけ topic、以降はタスクブランチ。
// **リポジトリの絶対パスを書かない**——書くと、worktree 付きのエージェントでもメインツリー側で
// `git checkout --detach` を実行して HEAD を外す事故が起きた（design-notes.md「worktree の注意」）
// canPush は「このエージェントがタスクブランチを前進させる役か」。実装・修正・impl-b だけが true。
// 役割で分けるのは、読み取り専用の役（レビュー・裁定・再計画・PR 本文）に push の仕方を教えると、
// 同じプロンプトの中で不変条件「push しない」と食い違うためである。矛盾した指示は、どちらが
// 効くかがモデル任せになる
// 引き継ぎノートのファイル名に使う役割名。`review:normal` のコロンを落とす。
// review.py に渡す役割タグ（`--reviewer review:normal`）とファイル名を分けるのは、コロンが
// Windows のファイル名に使えず、シェルのグロブでも扱いにくいためである
// （review-prompt.md §8 が「役割はファイル名に使えるよう review-normal と書く」と定めている）。
const noteName = role => role.replace(/:/g, '-')

const preamble = (role, round, startPoint, canPush) => `
カレントディレクトリ（割り当てられた worktree）で作業する。他のディレクトリのチェックアウトに
触れない。リポジトリの絶対パスはプロンプトに書かれていない。

1. \`git fetch origin\` を実行する。
2. \`git checkout --detach ${startPoint}\` で起点に載る（ブランチ名を checkout しない）。
3. 前提資料を読む（git の追跡対象外なので checkout では作業ツリーに現れない）:
   ${BASE}/brief.md（検証コマンド・外形動作の手順・不可侵パス・規約）
   ${BASE}/map.md（コードベースの入口）
   ${BASE}/ledger.md（他タスクとの関係）
4. **コードを読む前に引き継ぎノートを読む**: ${NOTES}/${noteName(role)}-*.md（あるものすべて）。
   前のラウンドの同じ役割が、読んだ箇所・実行した検証と結果・構造の要点を残している。
5. 終える前に \`mkdir -p ${NOTES}\` して ${NOTES}/${noteName(role)}-r${round}.md を書く。
   中身は「読んだファイルとその要点」「実行したコマンドとその結果」「まだ確かめていない箇所」。
   会話をそのまま貼らない。次のエージェントがコードを読み直さずに済む要約にする。
6. ${canPush
  ? `push は \`git push origin HEAD:refs/heads/${task.branch}\` で行う（リモートにだけ作る）。`
  : `**push しない。** あなたはこのブランチを前進させる役ではない。commit も push も行わず、
   見たこと・判断したことだけを返す。`}
`

// --- 契約ファイルへの案内（全役割に共通） ---
// 契約は .md に 1 つだけ置き、各エージェントが自分で読む。読み替え表を添えるのは、契約の中の
// プレースホルダ（<ベース> など）がこのタスクのどの値を指すのかを、契約を読む前に確定させるため。
const contractPointer = (file, role, round, startPoint) => `
【あなたの契約】

**コードを読む前に、まず ${SKILL_DIR}/${file} を Read せよ。** これがあなたの契約であり、
何をどう判断するかはそこに書いてある。このプロンプトは契約を要約していない。

契約の中のプレースホルダは、このタスクでは次の値を指す。読み替えて実行せよ。

| 契約の表記 | このタスクでの値 |
| --- | --- |
| \`<スクリプト>\` | ${SCRIPTS} |
| \`<ベース>\` | ${BASE} |
| \`task<番号>\` | ${task.id} |
| \`<ベース>/notes/task<番号>\` | ${NOTES} |
| \`<作業名>\` | ${WORK} |
| \`topic/<作業名>\` | ${TOPIC} |
| \`<タスクブランチ>\` | ${task.branch} |
| \`<ラウンド>\` | ${round} |
| \`<起点>\` | ${startPoint} |
| \`<自分の役割>\` | ${role} |${noteName(role) === role ? '' : `
| \`<自分の役割>\`（ファイル名に使うとき） | ${noteName(role)}（引き継ぎノートはこの名前で書く。\`review.py --reviewer\` に渡すタグは上の行のまま） |`}
| \`<topicPR番号>\` | ${TOPIC_PR} |

返り値の \`contractRead\` には、この契約ファイルを実際に Read したかを入れる。読めなかった
（ファイルが無い・権限が無い）場合は false にし、\`notes\` にその旨を書く。偽らない——
リードが「契約なしで走った」ことを検出するための値である。
`

// --- 契約を 1 行も読めなくても守る規律（役割ごと） ---
// contractPointer が案内する .md を読み飛ばされても事故にならないよう、破ると取り返しが
// つかないものだけをここに直書きする。**契約の要約ではない。**
const INVARIANTS = {
  impl: `
【契約を読めなくても必ず守ること】
- **PR を作らない・マージしない。** \`gh pr create\` / \`gh pr merge\` を使わない（PR は全レビューが
  決着してからリードが作る）。
- **review.json の status を動かさない。** \`${SCRIPTS}/review.py status\` を呼ばない
  （動かせるのは裁定だけで、スクリプトも拒む）。直した報告は \`review.py comment\` で残す。
- **メイン作業ツリー・他のディレクトリのチェックアウトに触れない。**
- **push は \`git push origin HEAD:refs/heads/${task.branch}\` だけ。** 他のブランチを動かさない。`,

  review: `
【契約を読めなくても必ず守ること】
- **GitHub には何も投稿しない。** \`gh pr create\` / \`gh pr comment\` / \`gh pr review\` を使わない。
- **ラウンドの先頭で \`${SCRIPTS}/review.py init --dir ${NOTES}\` を呼ぶ。** 指摘が 0 件で終わる
  場合も省かない（呼ばないと、リードの取り込み前検査が「レビューが 1 度も走っていない」と判定する）。
- **指摘は \`${SCRIPTS}/review.py new\` で立てる。** review.json を直接編集しない。
- **status を動かさない。** \`review.py status\` を呼ばない（動かすのは裁定）。
- **コードを書かない・push しない。**`,

  judge: `
【契約を読めなくても必ず守ること】
- **status を動かせるのはあなただけである。** \`${SCRIPTS}/review.py status --commenter judge\` を
  使い、**コメントを必ず付ける**（コメント無しはスクリプトが拒む）。
- **コードを書かない・ファイルを編集しない・push しない・PR を作らない。**
- **\`openTotal\` と \`openMustFix\` は目で数えない。** 裁定を全件終えたあとに
  \`${SCRIPTS}/review.py list --dir ${NOTES} --all\` を叩き、出力の \`counts.open\` と
  \`counts.open_must_fix\` をそのまま写す（この 2 つがワークフローの打ち切り判定に直結する）。
- **rating を書き換えない**（review.json を直接編集しない）。`,

  replan: `
【契約を読めなくても必ず守ること】
- **コードを書かない・ファイルを編集しない・push しない。** 立てるのは方針だけである。
- **review.json を書き換えない**（\`review.py new\` / \`comment\` / \`status\` を呼ばない）。
- **PR を作らない。**`,

  prBody: `
【契約を読めなくても必ず守ること】
- **PR を作らない・push しない・コードを編集しない。** \`gh pr create\` / \`gh pr edit\` を使わない。
- **書いてよいのは ${NOTES}/pr-body.md 1 つだけ。** リポジトリのファイルを編集しない。
- **review.json を書き換えない。** 読むだけである。
- **「正しく」「正常に」「適切に」を使わない。** 観測できる事象で書く。`,
}

// --- このタスクの値（全役割に共通で渡す） ---
const taskBlock = `
【このタスク】

- タスク: ${task.id}（${task.subject ?? '件名なし'}） / tier: ${task.tier ?? 'standard'}
- DoD（達成すべき状態）: ${task.dod ?? '（未指定）'}
- 受け入れ基準と検証: ${task.acceptance ?? '（未指定）'}
- スコープ境界: ${task.scope ?? '（未指定）'}
- 調査の入口: ${task.entrypoints ?? '（未指定）'}
- 隣接タスクとの契約: ${task.contracts ?? '（なし）'}
- タスクブランチ: ${task.branch}
- 起点の topic ブランチ: ${TOPIC}
`

// --- プロンプト組み立て（骨組みに固定。リードは組み立てない） ---
const buildPrompt = ({ file, role, round, startPoint, invariants, canPush = false, extra = '' }) =>
  preamble(role, round, startPoint, canPush) +
  contractPointer(file, role, round, startPoint) +
  invariants + '\n' + taskBlock + extra

const implPrompt = (role, round, resume, plan) => buildPrompt({
  file: 'implementation-prompt.md',
  role, round,
  startPoint: resume ? `origin/${task.branch}` : `origin/${TOPIC}`,
  invariants: INVARIANTS.impl,
  canPush: true,
  extra: (resume ? `
【続きから始める】
前回の run が途中で落ちている。やり直さず、push 済みの ${resume.sha ?? 'タスクブランチ先端'} を
起点に続きを実装する。引き継ぎノート（${NOTES}）に前回どこまで進んだかが残っている。
` : '') + (plan ? `
【封入された方針（再計画エージェントが立てたもの）】
前の実装は決着に至らなかった。次の方針で作り直す。

${plan}
` : ''),
})

const fixPrompt = round => buildPrompt({
  file: 'implementation-prompt.md',
  role: 'impl', round,
  startPoint: `origin/${task.branch}`,
  invariants: INVARIANTS.impl,
  canPush: true,
  extra: `
【この起動は修正ラウンドである】
契約の「修正ラウンド」の節に従う。open のレビューを
\`${SCRIPTS}/review.py list --dir ${NOTES}\` で取り、直したものに \`review.py comment\` で
返信する。closed / rejected は見えない（見る必要がない）。
`,
})

const reviewPrompt = (role, round) => buildPrompt({
  file: 'review-prompt.md',
  role, round,
  startPoint: `origin/${task.branch}`,
  invariants: INVARIANTS.review,
  extra: `
【この起動の役割】
\`review.py new --reviewer\` に渡す役割タグは **${role}** である。
${role === 'review:adversarial' ? `
あなたは敵対的レビュアーである。契約の「敵対的レビュー」の節に従い、**実装の説明も
通常レビューの結論も持ち込まない**。差分だけを見て、壊れる入力と状態を自分で探す。
` : ''}`,
})

const judgePrompt = round => buildPrompt({
  file: 'judge-prompt.md',
  role: 'judge', round,
  startPoint: `origin/${task.branch}`,
  invariants: INVARIANTS.judge,
  extra: `
【このラウンドで走ったレビュアー】
${REVIEWER_ROLES.join(' / ')}
（引き継ぎノートにこれを書く。次のラウンドの自分が、誰の指摘を裁いたかを追えるようにする）
`,
})

const replanPrompt = reason => buildPrompt({
  file: 'escalation-prompt.md',
  role: 'replan', round: 0,
  startPoint: `origin/${task.branch}`,
  invariants: INVARIANTS.replan,
  extra: `
【なぜあなたが呼ばれたか】
修正ループが決着に至らなかった。打ち切りの理由: ${reason}

あなたが返す \`plan\` は、次に立つ実装エージェント（impl-b）のプロンプトへ封入される。
`,
})

const prBodyPrompt = () => buildPrompt({
  file: 'pr-body-prompt.md',
  role: 'pr-body', round: 0,
  startPoint: `origin/${task.branch}`,
  invariants: INVARIANTS.prBody,
  extra: `
【書き先】
${NOTES}/pr-body.md

【タイトルの接頭辞に入れる topic PR の番号】
${TOPIC_PR}
`,
})

// --- args.dryRun: 組み立てたプロンプトを返すだけで、エージェントを 1 体も起動しない ---
// 契約ファイルを直したときや骨組みを直したときに、**トークンを使わずに**各エージェントが
// 実際に受け取る文面を確かめるための口である。読み替え表の値が正しく埋まっているか、
// 不変条件が入っているかは、走らせてからでは高くつく
if (input?.dryRun)
  return {
    dryRun: true, task: task.id, scripts: SCRIPTS, notes: NOTES,
    reviewerRoles: REVIEWER_ROLES,
    prompts: {
      impl: implPrompt('impl', 0, null),
      fix: fixPrompt('2'),
      reviewNormal: reviewPrompt('review:normal', '1'),
      reviewAdversarial: reviewPrompt('review:adversarial', '1'),
      judge: judgePrompt('1'),
      replan: replanPrompt('（打ち切り理由の例）'),
      prBody: prBodyPrompt(),
    },
  }

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
  // 検証の記録が無い、または契約を読んでいない応答は no-op を疑って 1 回だけ振り直す
  const noop = r => !r.notes || r.contractRead === false
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
    // 契約を読めなかったレビュアーの数。0 でなければリードに知らせる（品質が落ちている）
    contractMisses: ok.filter(r => r.contractRead === false).length,
  }
}

// --- 状態（ループとエスカレーションで共有する） ---
const carry = { decisions: [], deferrals: [], contractMisses: 0 }
const collect = r => {
  carry.decisions.push(...(r?.decisions || []))
  carry.deferrals.push(...(r?.deferrals || []))
  if (r?.contractRead === false) carry.contractMisses += 1
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
    carry.contractMisses += review.contractMisses
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
// （escalation-prompt.md / implementation-prompt.md は「前の実装を捨てて作り直す」契約である）。
// リードが resumeFrom を組み立てて同じ実装の続きから立て直すのが正しい
if (r.infra) return fail(`エージェントが起動しなかった: ${r.reason}`)

// --- 3. 直しきれなければ 再計画 → impl-b → 新規レビューでやり直す ---
if (!r.done) {
  const plan = await agentRetry(replanPrompt(r.reason),
    { label: `replan:${task.id}`, phase: 'Escalation',
      model: 'opus', isolation: 'worktree', schema: PLAN })
  if (!plan) return fail(`再計画エージェントが起動しなかった（2 回）。直前: ${r.reason}`)
  collect(plan)
  if (plan.unsolvable) return fail(`解決不能と判断された: ${plan.reason}`)

  const implB = await agentRetry(implPrompt('impl-b', 0, null, plan.plan),
    { label: `impl-b:${task.id}`, phase: 'Escalation',
      model: 'opus', isolation: 'worktree', schema: IMPL })
  if (!implB) return fail(`impl-b が起動しなかった（2 回）。直前: ${r.reason}`)
  collect(implB)
  branch = implB.branch || branch
  changeKind = implB.changeKind || 'logic'

  r = await reviewFixLoop({ tag: 'b', maxRounds: 3 })   // レビューは新規に立て直す
  if (r.infra) return fail(`エージェントが起動しなかった: ${r.reason}`)
  if (!r.done) return fail(`impl-b でも決着に至らなかった: ${r.reason}`)
}

// --- 4. PR 本文を書く（PR を作るのはリード） ---
const prBody = await agentRetry(prBodyPrompt(),
  { label: `pr-body:${task.id}`, phase: 'PR',
    model: 'opus', isolation: 'worktree', schema: PRBODY })
collect(prBody)

// --- 5. 返す（PR 作成と取り込みはリードが行う） ---
return {
  task: task.id, tier: task.tier, branch,
  approved: true,
  // 決着したラウンドで**実際に結果を返した**レビュアーの体数と、tier から決まる期待体数。
  // 2 つを別々に返すのは、片方だけでは食い違いを検出できないためである（tier から導いた
  // 値だけを返すと、何が起きても tier と一致してリードの突き合わせが空振りする）
  reviewers: r.reviewers, expectedReviewers: r.expected,
  reviewerRoles: REVIEWER_ROLES,   // 走らせるはずだった役割（記録用）
  // 契約ファイルを読めずに走ったエージェントの延べ数。0 でなければ品質が落ちているので、
  // リードは差分を自分で確かめてから取り込む
  contractMisses: carry.contractMisses,
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
