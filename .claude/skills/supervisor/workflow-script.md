# タスクを 1 本走らせる（同梱スクリプトの呼び方）

1 タスク = 1 ワークフロー。実装 → レビュー → 裁定 → 修正を回し、**全レビューが決着して PR 本文が
書き上がるまで**を面倒を見て、決着したブランチと PR 本文のパスを返す。

**リードはスクリプトを組み立てない。** オーケストレーションはスキルに同梱された
[`scripts/task-workflow.js`](scripts/task-workflow.js) に固定してあり、リードは `args` を渡して
呼ぶだけである。

## 目次

どの節に何が書いてあるか。必要な節だけ読めばよい。

- 呼び方
- `args` に入れるもの
- 契約はどうエージェントに届くか
- 役割ごとのモデルと effort
- 役目を終えた worktree を走行中に消す
- 走らせずに文面を確かめる（`dryRun`）
- リード側の受け取り
- 骨組みを直すとき

## 呼び方

```
Workflow({
  scriptPath: "<スキル>/scripts/task-workflow.js",
  args: {
    task: { id: "task4", subject: "パーサの境界値を直す", tier: "standard",
            branch: "stack/<作業名>--task-4", dod: "…", acceptance: "…",
            scope: "…", entrypoints: "…", contracts: "…" },
    parent: "<起動時の stacked PR の先頭>",
    base: "<ベース>",
    work: "<作業名>",
    stackPr: 100,
    skillDir: "<スキル>",
    resumeFrom: { branch: "…", sha: "…", transcriptDir: "…" }   // 立て直しのときだけ
  }
})
```

`<スキル>` は [lead-setup.md](lead-setup.md)「起動前の確認」で確定した、このスキルの
ディレクトリの絶対パスである。

- **`args` は実オブジェクトで渡す。** JSON 文字列で渡すと `args.task` などが全部 `undefined` に
  なる（スクリプトの冒頭が検査して `failed` で返す）。
- **返り値の `runId` を台帳に控える**（[ledger.md](ledger.md)）。ワークフローはバックグラウンドで
  走り、完了は通知で届く。走行中に部分結果は届かない。
- `light` を束ねたタスクは `task.dod` に束ねた全 DoD を並べ、`task.id` は代表の番号にする
  （ブランチと PR は 1 本にまとめる）。

## `args` に入れるもの

| キー | 中身 | 欠けるとどうなるか |
| --- | --- | --- |
| `task.id` | `task4` の形。引き継ぎノートと review.json の置き場（`<ベース>/notes/<task.id>`）になる | `failed` で即返る |
| `task.branch` | タスクブランチ名 | 同上 |
| `task.subject` / `dod` / `acceptance` / `scope` / `entrypoints` / `contracts` | 各エージェントのプロンプトの「このタスク」節に入る | `（未指定）`と書かれたまま走る |
| `task.tier` | `standard`（既定）/ `light`。レビュアーの体数とモデルを決める | `standard` 扱い |
| `parent` | 起動する瞬間の stacked PR の先頭（`stack.py show` の `branches` の最後。1 本も積んでいなければ `stack/<作業名>--task-0`）。実装の初回の起点で、作る PR の base でもある | `failed` で即返る |
| `base` | `place.py base-dir` が返した絶対パス | 同上 |
| `work` | 作業名 | 同上 |
| `stackPr` | stack PR（stacked PR の土台 task-0 の PR）の番号。タスク PR のタイトルに入る | 同上 |
| `skillDir` | このスキルのディレクトリの絶対パス。**契約ファイルの置き場である** | 同上 |
| `resumeFrom` | 立て直しのときだけ。`{ branch, sha, transcriptDir }` | 初回実装として走る |
| `dryRun` | `true` なら文面を返すだけで 1 体も起動しない（下記） | 通常どおり走る |

**解法を渡さない**（変更するファイルの一覧・行番号つきの手順）。理由は
[implementation-prompt.md](implementation-prompt.md) §0。

## 契約はどうエージェントに届くか

各エージェントの契約（何をどう判断するか）は `.md` に 1 本ずつ置いてある。**骨組みは契約の本文を
持たず、エージェント自身に Read させる。**

| 役割 | 契約 | 起点 |
| --- | --- | --- |
| 実装・修正・impl-b | [implementation-prompt.md](implementation-prompt.md) | 初回は `origin/<parent>`、以降 `origin/<タスクブランチ>` |
| 通常・敵対的レビュー | [review-prompt.md](review-prompt.md) | `origin/<タスクブランチ>` |
| 裁定 | [judge-prompt.md](judge-prompt.md) | `origin/<タスクブランチ>` |
| 再計画 | [escalation-prompt.md](escalation-prompt.md) | `origin/<タスクブランチ>` |
| PR 本文 | [pr-body-prompt.md](pr-body-prompt.md) | `origin/<タスクブランチ>` |

骨組みが各プロンプトに入れるのは次の 4 つだけである。

1. **共通の前置き**（worktree の規律・前提資料・引き継ぎノートの読み書き）。前提資料は
   `<ベース>/brief.md` と `<ベース>/map.md` の 2 つで、**台帳は配らない**——他タスクとの関係は
   4 の「隣接タスクとの契約」で渡してあり、台帳はタスクが増えるほど太って全エージェントの
   入力に毎回乗る
2. **契約ファイルへの案内と読み替え表**——契約の中の `<スクリプト>` `<ベース>` `task<番号>`
   `<作業名>` `<タスクブランチ>` `<起点ブランチ>` `<ラウンド>` `<起点>` `<自分の役割>` `<stackPR番号>` が、
   このタスクではどの値を指すか。**あわせて「この場面で読む節」を指定する**（下記）
3. **契約を読めなくても守る不変条件**（5〜10 行）——PR を作らない、status を動かさない、
   GitHub に投稿しない、`review.py init` を呼ぶ、など。**契約の要約ではなく、破ると
   取り返しがつかないものだけ**である
4. **このタスクの値**（`args.task` の中身）

3 を置いてあるのは、Read を飛ばされる余地があるためである。契約を 1 行も読めなかった場合でも、
安全側の規律は守られる。

### 場面ごとに読む節を絞る

`implementation-prompt.md` は 16KB・`review-prompt.md` は 15KB ある。全文を開くと 1 体あたり
4k トークン級が入力に乗るが、場面によって当たらない節がある。骨組みの `SECTIONS`
（[scripts/task-workflow.js](scripts/task-workflow.js)）が場面ごとに読む節を指定し、
エージェントは `grep -n '^## '` で節の開始行を取ってその範囲だけを Read する。

| 場面 | 読む節 | 外す節 |
| --- | --- | --- |
| 初回の実装 | §1〜§6・§9〜§11 | §0（スキルを直す人向け）・§7（修正ラウンド）・§8（impl-b） |
| 修正ラウンド | §1・§4〜§7・§9〜§11 | §0・§2（何を作るか）・§3（着手前の計画）・§8 |
| 実装の差し替え（impl-b） | §1〜§6・§8〜§11 | §0・§7 |
| 通常レビュー（1 巡目） | §1〜§3・§5・§6・§8〜§10 | §4（敵対的の前提）・§7（再レビュー） |
| 通常レビュー（2 巡目以降） | §1〜§3・§5〜§10 | §4 |
| 敵対的レビュー | §1〜§6・§8〜§10 | §7 |

**行番号を焼き込まない。** 契約に 1 行足すだけでずれるので、位置はエージェントに grep させる。
grep 1 回（数百バイト）で、読まずに済む節の分（3〜6KB）が浮く。裁定・再計画・PR 本文の契約は
節を絞らない（全節がそのラウンドに当たる）。

**契約を読んだかどうかは返り値の `contractRead` で申告させる。** レビュアーが `false` を返したら
その 1 体は 1 回だけ振り直し、それでも `false` なら延べ数を `contractMisses` に載せてリードへ返す。
リードは 0 でなければ差分を自分で確かめてから積む。

なぜ本文を `args` で渡さないか、なぜ骨組みに焼き込まないかは
design-notes.md「なぜ契約をエージェントに Read させるか」にある。

## 役割ごとのモデルと effort

スクリプトが決めるので、リードは指定しない。コストを見積もるための表である。

| 役割 | model | effort |
| --- | --- | --- |
| リード（`/supervisor` のセッション本体） | `opus` | — |
| 実装・修正（standard） | `opus` | 既定 |
| 実装・修正（light） | `sonnet` | `medium` |
| 実装の差し替え（impl-b） | `opus` | 既定 |
| 通常レビュー（standard） | `opus` | `medium` |
| 通常レビュー（light） | `sonnet` | `medium` |
| 敵対的レビュー（standard の 1 巡目だけ） | `opus` | `medium` |
| doc だけの修正の再レビュー | `sonnet` | `low` |
| 裁定 | `opus` | `medium` |
| 再計画（エスカレーション） | `opus` | 既定 |
| PR 本文 | `opus` | 既定 |
| worktree の後始末（1 コマンド叩くだけ・worktree なし） | `sonnet` | `low` |
| Explore 調査（リードの意思決定用） | `opus` | 既定 |

**`isolation: 'worktree'` が付かないのは 1 つだけである**——worktree の後始末レーン。
checkout をしないので worktree が要らず、自分に worktree が付くと消す対象の中に自分がいて、
自分の足元を抜くことになる。

## 役目を終えた worktree を走行中に消す

1 タスクが作る worktree は、最少でも 4 個（light が 1 巡で決着: 実装・レビュー・裁定・PR 本文）、
エスカレーションまで回ると 20 個を超える。**どれもリポジトリの完全なチェックアウトである。**
Claude Code が自動で消すのは中身が変わっていない worktree だけなので、commit を積んだ実装・
修正の分は残る。

そこで**役目を終えた worktree を、run が終わるのを待たずにワークフロー自身が消す。**

| 区切り | そこで消える worktree |
| --- | --- |
| 実装が push を終えた | 実装 |
| そのラウンドの裁定が結果を返した | そのラウンドのレビュアー・裁定と、前のラウンドの修正 |
| impl-b が push を終えた | 再計画・impl-b |
| PR 本文が書き上がった | PR 本文 |

- **消せるのは「消しても失われるものが無い」ものだけである。** 判定は
  `worktree.py remove --role-done --branch <タスクブランチ>` が持ち、locked（まだ生きている）・
  dirty（未コミットの変更がある）・unpushed（この worktree にしか無いコミットがある）・
  名前が `wf_` で始まらないものを拒む。
- **打ち切って返る経路では 1 つも消さない**（`blocked` / `failed`）。落ちた run の worktree は
  リードが中身を見て立て直す材料になる（理由は design-notes.md「なぜ worktree をワークフローの
  中で消すか」）。
- **消し残しは失敗ではない。** 返り値の `worktreesKept` に載って上がるので、リードが run の後に
  `worktree.py remove --run <runId> --merged` で片づける（[integration.md](integration.md) §4）。
- パスはエージェントの自己申告（返り値の `worktree`）で集める。ワークフローのスクリプトからは
  自分の `runId` が読めないので、これが唯一の経路である。

## 走らせずに文面を確かめる（`dryRun`）

契約ファイルか骨組みを直したときは、**トークンを使わずに**各エージェントが受け取る文面を確かめ
られる。`args.dryRun` を `true` にすると、エージェントを 1 体も起動せずにプロンプトを返す。

```
Workflow({ scriptPath: "<スキル>/scripts/task-workflow.js",
           args: { …通常と同じ…, dryRun: true } })
```

読み替え表の値が埋まっているか、不変条件が入っているかを見る。走らせてから気づくと高くつく。
**各プロンプトに「返り値の `worktree` に入れる」の指示が入っていることも見る**——落ちると
worktree が消えずに積み上がる。

## リード側の受け取り

返り値を受け取ったら、**内容を鵜呑みにせず実地検証してから**積む
（[integration.md](integration.md) §1）。

| 返り値 | 意味と、リードがすること |
| --- | --- |
| `approved: true` | 決着した。`stack.py precheck` の 6 検査（既に積んでいないか・run の生死・ブランチとコミット・レビューの決着・レビュアーの体数・敵対的が走ったか）を通し、タスク PR を作り、worktree を外してから stacked PR へ積む（[integration.md](integration.md) §1・§2） |
| `blocked: true` | `questions` をユーザーに上げ、答えを受けてタスクを組み直して起動し直す |
| `failed: true` | `reason` と review.json を見る |
| `reviewers` / `expectedReviewers` | **決着したラウンドで**実際に結果を返した体数と、そのラウンドで走らせるはずだった体数。**`reviewers` < `expectedReviewers` なら積まない**。敵対的レビューは 1 巡目だけ走るので、2 巡目以降で決着したタスクはどちらも 1 になる |
| `adversarialRan` | このタスクで敵対的レビューが 1 度でも結果を返したか。**`tier` が standard なのに false なら積まない**（`light` では常に false） |
| `contractMisses` | 契約を読めずに走ったエージェントの延べ数。0 でなければ差分を自分で確かめる |
| `prBodyFile` が空 | PR 本文エージェントが 2 回とも起動しなかった。**リードが自分で本文を書いてタスク PR を作り**、そのことをユーザーに知らせる（[integration.md](integration.md) §2） |
| `notesDir` | 立て直しのとき、新しいワークフローの引き継ぎノートの置き場として同じパスを使う |
| `worktreesRemoved` / `worktreesKept` | 走行中に消した worktree の数と、残ったパス。残った分は run の後に片づける（[integration.md](integration.md) §4）。**`failed` / `blocked` では 1 つも消していない** |

`failed` の `reason` が**「エージェントが起動しなかった」で始まるもの**は実装の欠陥ではない。
タスクを組み直さず、`resumeFrom` で同じ実装の続きから立て直す。

## 骨組みを直すとき

`scripts/task-workflow.js` を直接編集する。守ること:

- **`agent()` の入れ子はできない。** ループ・分岐・打ち切り条件はスクリプトの制御フローに書き、
  `agent()` には実務だけを載せる。
- **エージェントは再開できない。** `agent()` に再開の引数は無く、`SendMessage` の宛先にもならない。
  文脈は `<ベース>/notes/task<番号>/` の引き継ぎノートで運ぶ
  （design-notes.md「ワークフローのエージェントは再開できない」）。
- **ブランチを checkout する全エージェントに `isolation: 'worktree'` を付ける。** worktree の中は
  detached HEAD で作業させ、push は `git push origin HEAD:refs/heads/<ブランチ>` にする。
- **修正とレビューは必ず別の `agent()`**（修正の自己承認を防ぐ）。
- **どのエージェントも GitHub に書かない。** 指摘は `<ベース>/notes/task<番号>/review.json` に
  置き、`review.py` で読み書きする（[review-store.md](review-store.md)）。タスク PR を作るのは
  リードである（[integration.md](integration.md) §2）。
- **指摘の本文は返り値に含めない。** 本文は review.json にあり、返すのは件数と検証の要約だけである。
- **worktree を消すのは、打ち切らずに次の段へ進んだ経路だけにする。** 打ち切って返る経路（`blocked` / `failed`）に `sweep()` を置かない（残った worktree は立て直す材料である）。
- **`import()` を書かない。** ワークフローのスクリプトはモジュールを読み込めず、書くと起動前に
  失敗する。ファイルの読み書きとシェルの実行もできない（それはエージェントの仕事である）。
- `Date.now()` / `Math.random()` / 引数なしの `new Date()` は使えない（起動が拒否される）。

直したら `dryRun` で文面を確かめる。
