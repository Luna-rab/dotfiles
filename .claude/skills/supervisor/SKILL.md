---
name: supervisor
description: >-
  dynamic workflow で開発作業を並列に進め、結果を stacked PR（互いに積み重なる PR の並び）に
  並べる監督者ワークフロー。リードは大きな作業をタスクに割り、タスクごとに dynamic workflow を
  1 本ずつ起動する。ワークフローは worktree 付きのサブエージェントで
  実装・レビュー・裁定・修正を回し、指摘が全件決着するまで面倒を見る。
  Claude のレビューは GitHub ではなく追跡しないファイル（review.json）に記録する。
  PR はすべてリードが作る（stacked PR の土台・タスク PR・残件回収）——create-pr スキルに
  引数で作らせる。
  1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。起動直後に base ブランチから
  stack/<作業名>--task-0（空コミット 1 つ）を切って draft の PR を作り、全体の計画と進行状況を
  その本文に書く。決着したブランチは gh stack（GitHub 公式の拡張）でその上へ 1 本ずつ積み、
  積むたびに本文を更新する。**リードはマージしない**——タスク PR は人間のレビューを待って
  open のまま残り、マージはユーザーが gh stack merge で下から行う。
  進捗はリード専用の worktree（スタックツリー）の中の追跡しない台帳ファイルに残すので、
  git の履歴を汚さずに、セッションが落ちても続きから再開できる。
when_to_use: >-
  ユーザーが `/supervisor` と明示的に打ったときだけ起動する。多数のエージェントを
  起動する高コストなワークフローのため、自動では発動しない
  （`disable-model-invocation` を付けてある）。
argument-hint: "[作業内容]"
disable-model-invocation: true
allowed-tools:
  - Bash(git fetch *)
  - Bash(git log *)
  - Bash(git status *)
  - Bash(git diff *)
  - Bash(git show *)
  - Bash(git pull)
  - Bash(git pull *)
  - Bash(git checkout *)
  - Bash(git switch *)
  - Bash(git merge *)
  - Bash(git merge-base *)
  - Bash(git commit *)
  - Bash(git push *)
  - Bash(git rev-parse *)
  - Bash(git ls-remote *)
  - Bash(git worktree *)
  - Bash(git branch *)
  - Bash(gh pr create *)
  - Bash(gh pr edit *)
  - Bash(gh pr ready *)
  - Bash(gh pr view *)
  - Bash(gh pr list *)
  - Bash(gh pr close *)
  - Bash(gh pr comment *)
  - Bash(gh repo view *)
  - Bash(gh stack *)
  - Bash(gh extension list)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/review.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/place.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/verify.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/worktree.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/stack.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/state.py *)
  - EnterWorktree
  - ExitWorktree
  - Skill(create-pr)
---

# supervisor（dynamic workflow による並列開発の統括）

作業対象: $ARGUMENTS

あなたは**リード**である。次の 6 つに専念する。

- 大きな作業をタスクに割り、DoD（完了条件＝達成すべき状態）を確定する
- stack PR（stacked PR の土台 task-0 の PR）を作り、計画と進行状況を本文に書き続ける
- タスクごとに dynamic workflow を 1 本起動する（同時 3 本まで）
- 決着したブランチを 1 本ずつ stacked PR の先頭へ積む（`stack.py append`）
- state.json を更新し、台帳と stack PR 本文を書き出す
- ユーザーと話す

**マージはしない。** タスク PR は人間がレビューするまで stacked PR の上で open のまま残り、
マージはユーザーが `gh stack merge` で下から行う。

実装・レビュー・裁定・修正はワークフローの中のサブエージェントが行う。**リードは指摘の
本文を読まない。** ワークフローが返すのは件数と検証の要約だけで、指摘の本文は review.json に
ある。指摘の本文がリードの画面に流れてきたら、この境界が壊れている。

**指摘は `<ベース>/notes/task<番号>/review.json` に集まる**（[review-store.md](review-store.md)）。
Claude のレビュアーはそこに立て、**Claude の指摘は GitHub には出ない。**

## 用語

- **タスク**: 1 本のワークフローが完結させる作業単位。合否が一意に判定できる大きさに割り、
  リスク階層 `tier`（`light` / `standard`）を付ける。
  **1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。**
- **ベースディレクトリ**: ベース 3 ファイル・引き継ぎノート・`state.json`・`prose/` の置き場。
  スタックツリーの中の `.claude/supervisor/` で、自分を無視する `.gitignore` が入るので
  **git の追跡対象に入らず、PR の差分にも出ない**。**パスは自分で組み立てず `place.py base-dir`
  から 1 行受け取る**（[lead-setup.md](lead-setup.md) §2）。以降 `<ベース>` はその絶対パス。
- **ベース 3 ファイル**: ベースディレクトリに置く `brief.md`（検証コマンド・不可侵パス・
  ブランチ規約）、`map.md`（コードベースの入口）、`ledger.md`（台帳）。
  **サブエージェントが読むのは `brief.md` と `map.md` の 2 つだけ**で、`ledger.md` はリードが
  読み書きする（他タスクとの関係は `args.task.contracts` で渡すので、台帳を配る必要が無い）。
- **引き継ぎノート**: `<ベース>/notes/task<番号>/<役割>-r<ラウンド>.md`。各サブエージェントが
  読んだ箇所・実行した検証と結果・構造の要点を残し、次のラウンドの同じ役割が先に読む。
- **state.json と台帳**: 進行状態の出所は `<ベース>/state.json` で、`state.py` で読み書きする。
  台帳（`<ベース>/ledger.md`）と stack PR 本文の下書きは `state.py render` がそこから書き出す
  ——**台帳を手で書かない**（[ledger.md](ledger.md)）。セッションが落ちたときは state.json から
  再開する。**commit しない**ので、ユーザーに見せる記録は stack PR 本文に載せる
  （[finish.md](finish.md) §9）。
- **review.json**: タスクごとのレビュー記録。`<ベース>/notes/task<番号>/review.json` に置き、
  `review.py` で読み書きする（[review-store.md](review-store.md)）。
- **stacked PR**: base ブランチの上に積み重なった PR の並び。GitHub 公式の拡張 `gh stack`
  （github/gh-stack）が組み立て、GitHub は PR の merge box にその地図を描く。
  下から `stack/<作業名>--task-0` ← `--task-<番号>` ← … と続き、各 PR の base は 1 つ下の
  ブランチである。**積むのは決着した順で、タスク番号の順とは一致しない。**
- **積み替え**: 決着したブランチを、いまの stacked PR の先頭の上へ載せ直すこと
  （`gh stack rebase --no-trunk` ＋ force push ＋ PR の base の張り替え）。起動した時点の先頭から、
  決着するまでに別のタスクが積まれて先頭が動いているときに要る。`stack.py append` が中でまとめて行う。
- **stack PR**: stacked PR の土台 `stack/<作業名>--task-0`（空コミット 1 つ）に付けた PR。
  [lead-setup.md](lead-setup.md) §4 で draft として作り、全体の計画とタスクの進行状況を本文に持つ。
  積むたびに本文を更新し、[finish.md](finish.md) §8 で最終版にする（[stack-pr.md](stack-pr.md)）。
  **ユーザーが GitHub 上で読める記録はこの本文である。**
  **計画をファイルにしてコミットしない**——土台に載るのは空コミット 1 つだけである。
- **スタックツリー**: stacked PR の先頭を載せたリード専用の worktree。
  `.claude/worktrees/supervisor-<作業名>` に作る（[lead-setup.md](lead-setup.md) §1）。
  以降 `<スタックツリー>` はこの絶対パス。
  **積み替えの git 操作と `gh stack` はすべてここで行い、ユーザーの作業ツリーに触らない**
  （[integration.md](integration.md)）。`gh stack` の追跡情報は worktree ごとに別なので、
  **ここ以外で `gh stack` を叩くと stacked PR が見えない**。

## 起動前の確認と立ち上げ

**[lead-setup.md](lead-setup.md) を Read してその手順に従う。** 起動前の 7 項目（gh 認証と
`gh stack` 拡張・スキルの置き場・実行ビット・自分のモデル・分岐元・作業名・作業名の重複）と
§1〜§6 がそこにある。

**ユーザーの作業ツリーの状態は問わない**（カレントブランチも未コミットの変更も）。リードは
§1 で作るスタックツリー（`.claude/worktrees/supervisor-<作業名>`）の中だけで動く。

## 全体フロー

| # | 段 | 手順の置き場 |
| --- | --- | --- |
| 1 | stacked PR の土台（task-0）とスタックツリーを作り、その中へ移って `stack.py init` を通す | [lead-setup.md](lead-setup.md) §1 |
| 2 | 前提を集めて `brief.md` を書く | [lead-setup.md](lead-setup.md) §2 |
| 3 | Explore に調査させて `map.md` を書く | [lead-setup.md](lead-setup.md) §3 |
| 4 | タスクを設計して state.json に入れ、stack PR を作る | [lead-setup.md](lead-setup.md) §4 |
| 5 | 権限を先に通す | [lead-setup.md](lead-setup.md) §5 |
| 6 | タスクを `TaskCreate` で登録する | [lead-setup.md](lead-setup.md) §6 |
| 7 | **ループ**: 空き枠にワークフローを起動し、完了通知を受けたら stacked PR へ積んで stack PR を更新する | 下の「7. 回す」 |
| 8 | 全タスク完了後に trunk と同期してフル検証し、stack PR を仕上げてマージの手順をユーザーに渡す | [finish.md](finish.md) §8 |

補助ファイルは次のとおり。**その段に入る直前に対応するファイルを Read する**（コンパクションで
本文が失われても取り直せる）。

**契約 5 本（実装・レビュー・裁定・再計画・PR 本文）はリードが読まなくてよい。** 各エージェントが
`args.skillDir` から自分で読む（[workflow-script.md](workflow-script.md)「契約はどうエージェントに
届くか」）。リードが読むのは、ワークフローの返り値を解釈するときと、契約を直すときだけである。

- リードの立ち上げ（起動前の確認と §1〜§6）: [lead-setup.md](lead-setup.md)
- リードの仕上げ（§8）と自律判断の記録（§9）: [finish.md](finish.md)
- ワークフローの呼び方と `args`: [workflow-script.md](workflow-script.md)
- 実装・修正エージェントの契約: [implementation-prompt.md](implementation-prompt.md)
- レビューエージェントの契約: [review-prompt.md](review-prompt.md)
- 裁定エージェントの契約: [judge-prompt.md](judge-prompt.md)
- 再計画エージェントの契約: [escalation-prompt.md](escalation-prompt.md)
- PR 本文エージェントの契約: [pr-body-prompt.md](pr-body-prompt.md)
- レビュー記録（review.json）の手順: [review-store.md](review-store.md)
- stack PR の作り方・本文の書式・更新: [stack-pr.md](stack-pr.md)
- リードの積み替えレーン: [integration.md](integration.md)
- 状態の持ち方（state.json）・台帳の書式・復旧手順: [ledger.md](ledger.md)
- 設計の理由と失敗の実績: [design-notes.md](design-notes.md)
  ——**リードは読まない。** 74KB あり、開くとコンテキストを 1 割前後使う。他のファイルからは
  リンクを張らず `design-notes.md「<節名>」` と地の文で指すだけにしてある。読むのはスキル自体を
  直すときだけである。

## エージェントの構成

1 タスク = 1 ワークフローで、中は直列（実装 → レビュー → 裁定 → 修正 → … → PR 本文）。同時に
走るのはレビューだけである（standard の **1 巡目だけ**通常レビューと敵対的レビューの 2 体。
2 巡目以降は通常レビュー 1 体）。
**同時 3 本を増やさない**（全体図と理由は design-notes.md の
「全体の流れ」と「なぜ同時 3 本までか」）。

## 7. 回す

**同時に走らせるワークフローは 3 本まで。** 枠が空いたら、`blockedBy` が解けているタスクのうち
番号が小さいものから起動する。

### 起動する

**スクリプトを組み立てない。** オーケストレーションはスキル同梱の
`<スキル>/scripts/task-workflow.js` に固定してある。`args` を**実オブジェクトで**渡して呼ぶだけ
である（JSON 文字列で渡すとスクリプト側で全フィールドが `undefined` になり、`failed` で即返る）。

```
Workflow({ scriptPath: "<スキル>/scripts/task-workflow.js", args: {
  task: { id: "task4", subject: "...", tier: "standard",
          branch: "stack/<作業名>--task-4", dod: "...", acceptance: "...",
          scope: "...", entrypoints: "...", contracts: "..." },
  parent: "<起動時の stacked PR の先頭>", base: "<ベース>", work: "<作業名>", stackPr: 100,
  skillDir: "<スキル>"
}})
```

**`parent` は起動する瞬間の stacked PR の先頭である**（`stack.py show --tree <スタックツリー>` の
`branches` の最後。まだ 1 本も積んでいなければ `stack/<作業名>--task-0`）。実装はここから
ブランチを切り、この値を PR の base にする。**同じ値を `state.py set --parent` で控える**
——`stack.py precheck` がこれを使う。

各フィールドの意味と、欠けたときに何が起きるかは
[workflow-script.md](workflow-script.md)「`args` に入れるもの」にある。
**各エージェントの契約（`implementation-prompt.md` など）は封入しない**——スクリプトが
`skillDir` から読ませる。

起動したら `state.py` に控える（`runId` は worktree の後始末と再実行に要る。
[ledger.md](ledger.md)）。

```bash
<スクリプト>/state.py set --base <ベース> --task <番号> --status running \
  --run-id <返り値の runId> --parent <起動時の stacked PR の先頭>
```

ワークフローはバックグラウンドで走り、完了は通知で届く。**走行中に部分結果は届かない。**

### 完了通知を受けたら

返り値は 3 種類ある。**どれも鵜呑みにせず、実物で確かめてから動く**（[integration.md](integration.md) §1）。

| 返り値 | どうするか |
| --- | --- |
| `approved: true` | `stack.py precheck` を通し、タスク PR を作り、worktree を外してから stacked PR へ積む（[integration.md](integration.md) §1・§2） |
| `blocked: true` | `questions` をユーザーに上げ、答えを受けてタスクを組み直し、起動し直す |
| `failed: true` | `reason` と review.json を見て、立て直すか、ユーザーに上げる（下記） |

`stackPr` は [lead-setup.md](lead-setup.md) §4 で控えた stack PR の番号で、タスク PR のタイトルに
入る（[stack-pr.md](stack-pr.md)「タイトルの接頭辞」）。**タスク PR はリードが `create-pr` スキルに
作らせる**（[integration.md](integration.md) §2）。返ってきた番号を `state.py set --pr` で入れる。

**タスク PR の本文は `state.py task-body` に通してから載せる**（stacked PR の案内が先頭に付く）。
**1 本積んだ直後に、`state.py set --status stacked` → `state.py render` → `gh pr edit` で台帳と
stack PR（task-0 の PR）の本文を差し替える**（[integration.md](integration.md) §2 の手順 4・5）。
溜めずに 1 本ごとに差し替え、そのあと空いた枠に次のタスクを起動する。

### 失敗したワークフローを立て直す

**やり直しではなく続きから始める。** push 済みのコミット・review.json・引き継ぎノートが
残っているので、それを起点にする。手順（`resumeFromRunId` と `resumeFrom` の使い分け・
打ち切りの条件）は [ledger.md](ledger.md)「落ちたワークフローの扱い」にある。
**立て直す直前にそこを Read する。**

**タスクを `blocked` / `failed` で打ち切ったら、状態と理由を入れて書き出す。** `render` が
「残課題」に理由を 1 行足す（[stack-pr.md](stack-pr.md)）。

```bash
<スクリプト>/state.py set --base <ベース> --task <番号> --status blocked --reason "<理由>"
<スクリプト>/state.py render --base <ベース>
gh pr edit <stackPR番号> --body-file <ベース>/stack-pr-body.md
```

### 質問・blocked を受けたら

- **自分で答えられるなら答えて、タスクを組み直して起動し直す**（タスク設計の意図・ベース資料・
  他タスクとの整合）。
- **ユーザーに上げるのは次の 4 つだけ**: 作業範囲の解釈が割れる / 規模が当初想定から大きく
  増減する / 後戻りしにくい設計上の取引が要る / ユーザーの指示が既存の DoD や設計文書と矛盾する。
- **確認を待つ間も走行中のワークフローは完走させ、積むのは進める。** 答え次第で無駄に
  なりそうなタスクだけ、新しく起動するのを止める。

### 完了の根拠

**タスクリストの `completed` を完了の根拠にしない。** subagent が終了すると harness が spawn 元の
タスクを自動で `completed` にすることがある。完了の根拠は次の 2 つだけである。

1. ワークフローの返り値が `approved: true` であること
2. `stack.py precheck` の終了コードが 0 であること。6 検査の中身と、落ちたときの読み方は
   [integration.md](integration.md) §1 にある

**`approved` だけで積まない。** それはワークフローの自己申告で、実物を見るのは `precheck` で
ある（design-notes.md「なぜ完了通知を実地検証するか」）。

## 8. 仕上げる・9. 自律判断を記録する

**[finish.md](finish.md) を Read してその手順に従う。** 全タスクが `stacked` か `blocked` に
なったらここへ来る。

**§9 はループの最中にも当てはまる。** ユーザーに確認せず自分で決めたことは、そのつど
`state.py set --decision` / `--deferral` か `<ベース>/prose/decisions.md` に残す
（[finish.md](finish.md) §9）。とくに次の 2 つは**必ず**残す。

- 最終目標・DoD・スコープを自分の判断で変えた
- やる予定だった作業を先送りにした、または対象外にした

## 10. 運用上の注意

- ユーザーの指示（削除・仕様変更）が既存の DoD や設計文書と矛盾するときは、黙って従わず
  **事実と帰結を先に示す**（例: 消す対象が別の成果物を兼ねている、検査が任意に格下げになる）。
  示したうえで判断が明らかなら、妥当な解釈で進めてよい。
- ワークフローの `blocked` 報告・失敗報告を鵜呑みにしない。`git show` やテストの実行で
  確かめてから裁く。
- 進捗の報告は「どのタスク・どの PR がどの状態か」を軸に短くまとめる。
