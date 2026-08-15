# タスクを 1 本走らせる（同梱スクリプトの呼び方）

1 タスク = 1 ワークフロー。実装 → レビュー → 裁定 → 修正を回し、**全レビューが決着して PR 本文が
書き上がるまで**を面倒を見て、承認したブランチと PR 本文のパスを返す。

**リードはスクリプトを組み立てない。** オーケストレーションはスキルに同梱された
[`scripts/task-workflow.js`](scripts/task-workflow.js) に固定してあり、リードは `args` を渡して
呼ぶだけである。

**PR は作らない。** ワークフローが返したブランチを、リードが PR にしてから topic へ取り込む
（[integration.md](integration.md)。理由は [design-notes.md](design-notes.md)）。

## 目次

どの節に何が書いてあるか。必要な節だけ読めばよい。

- 呼び方
- `args` に入れるもの
- 契約はどうエージェントに届くか
- 役割ごとのモデルと effort
- 走らせずに文面を確かめる（`dryRun`）
- リード側の受け取り
- 骨組みを直すとき

## 呼び方

```
Workflow({
  scriptPath: "<スキル>/scripts/task-workflow.js",
  args: {
    task: { id: "task4", subject: "パーサの境界値を直す", tier: "standard",
            branch: "topic/<作業名>--task-4", dod: "…", acceptance: "…",
            scope: "…", entrypoints: "…", contracts: "…" },
    topic: "topic/<作業名>",
    base: "<ベース>",
    work: "<作業名>",
    topicPr: 100,
    skillDir: "<スキル>",
    resumeFrom: { branch: "…", sha: "…", transcriptDir: "…" }   // 立て直しのときだけ
  }
})
```

`<スキル>` は `SKILL.md`「起動前の確認」で確定した、このスキルのディレクトリの絶対パスである。

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
| `topic` | `topic/<作業名>`。実装の初回の起点 | `failed` で即返る |
| `base` | `place.py base-dir` が返した絶対パス | 同上 |
| `work` | 作業名 | 同上 |
| `topicPr` | topic PR の番号。タスク PR のタイトルに入る | 同上 |
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
| 実装・修正・impl-b | [implementation-prompt.md](implementation-prompt.md) | 初回は `origin/<topic>`、以降 `origin/<タスクブランチ>` |
| 通常・敵対的レビュー | [review-prompt.md](review-prompt.md) | `origin/<タスクブランチ>` |
| 裁定 | [judge-prompt.md](judge-prompt.md) | `origin/<タスクブランチ>` |
| 再計画 | [escalation-prompt.md](escalation-prompt.md) | `origin/<タスクブランチ>` |
| PR 本文 | [pr-body-prompt.md](pr-body-prompt.md) | `origin/<タスクブランチ>` |

骨組みが各プロンプトに入れるのは次の 4 つだけである。

1. **共通の前置き**（worktree の規律・前提資料・引き継ぎノートの読み書き）
2. **契約ファイルへの案内と読み替え表**——契約の中の `<スクリプト>` `<ベース>` `task<番号>`
   `<作業名>` `<タスクブランチ>` `<ラウンド>` `<起点>` `<自分の役割>` `<topicPR番号>` が、
   このタスクではどの値を指すか
3. **契約を読めなくても守る不変条件**（5〜10 行）——PR を作らない、status を動かさない、
   GitHub に投稿しない、`review.py init` を呼ぶ、など。**契約の要約ではなく、破ると
   取り返しがつかないものだけ**である
4. **このタスクの値**（`args.task` の中身）

3 を置いてあるのは、Read を飛ばされる余地があるためである。契約を 1 行も読めなかった場合でも、
安全側の規律は守られる。

**契約を読んだかどうかは返り値の `contractRead` で申告させる。** レビュアーが `false` を返したら
その 1 体は 1 回だけ振り直し、それでも `false` なら延べ数を `contractMisses` に載せてリードへ返す。
リードは 0 でなければ差分を自分で確かめてから取り込む。

なぜ本文を `args` で渡さないか、なぜ骨組みに焼き込まないかは
[design-notes.md](design-notes.md)「なぜ契約をエージェントに Read させるか」にある。

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
| 敵対的レビュー（standard のみ） | `opus` | `medium` |
| doc だけの修正の再レビュー | `sonnet` | `low` |
| 裁定 | `opus` | `medium` |
| 再計画（エスカレーション） | `opus` | 既定 |
| PR 本文 | `opus` | 既定 |
| Explore 調査（リードの意思決定用） | `opus` | 既定 |

ワークフロー内の全エージェントに `isolation: 'worktree'` が付く。

## 走らせずに文面を確かめる（`dryRun`）

契約ファイルか骨組みを直したときは、**トークンを使わずに**各エージェントが受け取る文面を確かめ
られる。`args.dryRun` を `true` にすると、エージェントを 1 体も起動せずにプロンプトを返す。

```
Workflow({ scriptPath: "<スキル>/scripts/task-workflow.js",
           args: { …通常と同じ…, dryRun: true } })
```

読み替え表の値が埋まっているか、不変条件が入っているかを見る。走らせてから気づくと高くつく。

## リード側の受け取り

返り値を受け取ったら、**内容を鵜呑みにせず実地検証してから**取り込む
（[integration.md](integration.md) §1）。

| 返り値 | 意味と、リードがすること |
| --- | --- |
| `approved: true` | 決着した。§1 の 4 検査（run の生死・`verify.py`・`review.py list --require-empty`・レビュアーの体数）を通してから PR にする |
| `blocked: true` | `questions` をユーザーに上げ、答えを受けてタスクを組み直して起動し直す |
| `failed: true` | `reason` と review.json を見る。**PR は作られていない**ので GitHub 上には何も残らない |
| `reviewers` / `expectedReviewers` | 実際に結果を返した体数と、`tier` から決まる期待体数。**`reviewers` < `expectedReviewers` なら取り込まない** |
| `contractMisses` | 契約を読めずに走ったエージェントの延べ数。0 でなければ差分を自分で確かめる |
| `prBodyFile` が空 | PR 本文エージェントが 2 回とも起動しなかった。**PR は作る**が本文は最小限にしてユーザーに知らせる（[integration.md](integration.md) §2） |
| `notesDir` | 立て直しのとき、新しいワークフローの引き継ぎノートの置き場として同じパスを使う |

`failed` の `reason` が**「エージェントが起動しなかった」で始まるもの**は実装の欠陥ではない。
タスクを組み直さず、`resumeFrom` で同じ実装の続きから立て直す。

## 骨組みを直すとき

`scripts/task-workflow.js` を直接編集する。守ること:

- **`agent()` の入れ子はできない。** ループ・分岐・打ち切り条件はスクリプトの制御フローに書き、
  `agent()` には実務だけを載せる。
- **エージェントは再開できない。** `agent()` に再開の引数は無く、`SendMessage` の宛先にもならない。
  文脈は `<ベース>/notes/task<番号>/` の引き継ぎノートで運ぶ（[design-notes.md](design-notes.md)）。
- **ブランチを checkout する全エージェントに `isolation: 'worktree'` を付ける。** worktree の中は
  detached HEAD で作業させ、push は `git push origin HEAD:refs/heads/<ブランチ>` にする。
- **修正とレビューは必ず別の `agent()`**（修正の自己承認を防ぐ）。
- **GitHub には誰も投稿しない。** 指摘は `<ベース>/notes/task<番号>/review.json` に置き、
  `review.py` で読み書きする（[review-store.md](review-store.md)）。PR を作るのはリードである。
- **指摘の本文は返り値に含めない。** 本文は review.json にあり、返すのは件数と検証の要約だけである。
- **`import()` を書かない。** ワークフローのスクリプトはモジュールを読み込めず、書くと起動前に
  失敗する。ファイルの読み書きとシェルの実行もできない（それはエージェントの仕事である）。
- `Date.now()` / `Math.random()` / 引数なしの `new Date()` は使えない（起動が拒否される）。

直したら `dryRun` で文面を確かめる。
