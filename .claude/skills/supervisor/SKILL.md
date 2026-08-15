---
name: supervisor
description: >-
  dynamic workflow で開発作業を並列に進める監督者ワークフロー。リードは大きな作業をタスクに割り、
  タスクごとに dynamic workflow を 1 本ずつ起動する。ワークフローは worktree 付きのサブエージェントで
  実装・レビュー・裁定・修正を回し、指摘が全件決着するまで面倒を見る。
  レビューは GitHub ではなく追跡しないファイル（review.json）に記録し、決着してから PR を作る。
  1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。起動直後に base ブランチから topic ブランチを
  切って draft の PR を作り、全体の計画と進行状況をその本文に書く。
  決着したブランチの topic への取り込みはリード本体が 1 本ずつ行い、取り込むたびに本文を
  更新する（最終マージはユーザー）。
  進捗は統合ツリーの中の追跡しない台帳ファイルに残すので、git の履歴を汚さずに、
  セッションが落ちても続きから再開できる。
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
  - Bash(${CLAUDE_SKILL_DIR}/scripts/review.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/place.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/verify.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/worktree.py *)
  - EnterWorktree
  - ExitWorktree
---

# supervisor（dynamic workflow による並列開発の統括）

作業対象: $ARGUMENTS

あなたは**リード**である。次の 6 つに専念する。

- 大きな作業をタスクに割り、DoD（完了条件＝達成すべき状態）を確定する
- topic PR を作り、計画と進行状況を本文に書き続ける
- タスクごとに dynamic workflow を 1 本起動する（同時 3 本まで）
- 決着したブランチを 1 本ずつ PR にして topic へ取り込む
- 台帳を更新する
- ユーザーと話す

実装・レビュー・裁定・修正はワークフローの中のサブエージェントが行う。**リードは指摘の
本文を読まない。** ワークフローが返すのは件数と検証の要約だけで、指摘の本文は review.json に
ある。指摘の本文がリードの画面に流れてきたら、この境界が壊れている。

**レビューが走っている間、GitHub には何も出ない。** 指摘は `<ベース>/notes/task<番号>/review.json`
に記録し（[review-store.md](review-store.md)）、全件が closed か rejected になってから PR を作る。
**PR を作るのはリードだけである。**

## 用語

- **タスク**: 1 本のワークフローが完結させる作業単位。合否が一意に判定できる大きさに割り、
  リスク階層 `tier`（`light` / `standard`）を付ける。
  **1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。**
- **ベースディレクトリ**: ベース 3 ファイルと引き継ぎノートの置き場。統合ツリーの中の
  `.claude/supervisor/` で、自分を無視する `.gitignore` が入るので **git の追跡対象に入らず、
  PR の差分にも出ない**。**パスは自分で組み立てず `place.py base-dir` から 1 行受け取る**（§2）。
  以降 `<ベース>` はその絶対パス。
- **ベース 3 ファイル**: 全サブエージェントが毎回読む前提資料。ベースディレクトリに
  `brief.md`（検証コマンド・不可侵パス・ブランチ規約）、`map.md`（コードベースの入口）、
  `ledger.md`（台帳）の 3 つを置く。
- **引き継ぎノート**: `<ベース>/notes/task<番号>/<役割>-r<ラウンド>.md`。各サブエージェントが
  読んだ箇所・実行した検証と結果・構造の要点を残し、次のラウンドの同じ役割が先に読む。
- **台帳**: ベースディレクトリの `ledger.md`。タスク分解・進行状態・自律判断を残す。
  セッションが落ちたときはここから再開する（[ledger.md](ledger.md)）。**commit しない**ので、
  ユーザーに見せる記録は topic PR 本文に書く（§9）。
- **review.json**: タスクごとのレビュー記録。`<ベース>/notes/task<番号>/review.json` に置き、
  `review.py` で読み書きする（[review-store.md](review-store.md)）。**GitHub には出ない。**
- **topic PR**: base ブランチへ向けた `topic/<作業名>` の PR。§4 で draft として作り、全体の計画と
  タスクの進行状況を本文に持つ。取り込むたびに本文を更新し、§8 で最終版にする
  （[topic-pr.md](topic-pr.md)）。**ユーザーが GitHub 上で読める記録はこの本文である。**
  **計画をファイルにしてコミットしない**——topic に載るのは §1 の空コミットと各タスクの
  マージコミットだけである。
- **統合ツリー**: `topic/<作業名>` を載せたリード専用の worktree。`.claude/worktrees/supervisor-<作業名>`
  に作る（§1）。以降 `<統合ツリー>` はこの絶対パス。**統合レーンの git 操作はすべてここで行い、
  ユーザーの作業ツリーに触らない**（[integration.md](integration.md)）。

## 起動前の確認

1. git リポジトリであること、`gh auth status` が通ることを確認する。
2. **スクリプトの置き場を確定する。** 以降 `<スクリプト>` はこの絶対パスを指す。

   ```
   <スクリプト> = ${CLAUDE_SKILL_DIR}/scripts
   ```

   この行はスキルの読み込み時に絶対パスへ展開されている。**自分で組み立てず、展開された値を
   そのまま使う。サブエージェントはこの値を知らないので、各プロンプトに封入する**
   （[workflow-script.md](workflow-script.md) の「プロンプト組み立て関数」）。
3. `scripts/review.py`・`scripts/place.py`・`scripts/verify.py`・`scripts/worktree.py` の 4 本に
   実行ビットがあることを確かめ（`ls -l <スクリプト>`）、欠けていれば `chmod +x` する。
4. **自分がどのモデルで動いているかをユーザーに申告する。** `opus` でなければ、タスク設計に
   入る前に `/model` での切り替えを提案する（役割ごとのモデルは
   [workflow-script.md](workflow-script.md) の表が `opus` を前提にコストを見積もっている）。
5. **分岐元（＝topic PR の base）を確定する。** 候補を列挙し、`release/*` が
   1 つ以上あればどこから切るかをユーザーに尋ねる。無ければデフォルトブランチで確定して尋ねない。
   以降 `<base>` はこのブランチ名を指す。

   ```bash
   git branch -r | grep -E 'origin/(release/|master$|main$)'
   ```

6. **作業名を決め、チケット番号の有無をユーザーに尋ねる**（チケットは推測しない）。
   **チケットは作業名に含めない**——topic PR のタイトル末尾にだけ添える
   （[topic-pr.md](topic-pr.md)「作る」）。以降 `<作業名>` は 1 つの値を指し、topic ブランチ
   （`topic/<作業名>`）・統合ツリー（`.claude/worktrees/supervisor-<作業名>`）・
   `place.py --work` のすべてに同じ値を渡す。ここを 2 通りに分けると、`place.py base-dir` が
   組み立てる統合ツリーのパスと実際に作ったパスが食い違って `git worktree list` に無いと
   言われ、§2 で止まる。
7. **その作業名がまだ使われていないことを確かめる。** topic ブランチと統合ツリーのパスが
   作業名から決まるので、既にあるものと重なると取り違える。

   ```bash
   git ls-remote --exit-code --heads origin topic/<作業名>   # 0 なら既にある
   git worktree list                                        # supervisor-<作業名> があるか
   ```

   どちらかが当たったら、**そのまま進めずユーザーに確認する**（別の作業の途中かもしれない）。

**ユーザーの作業ツリーの状態は問わない**（カレントブランチも未コミットの変更も）。リードは
§1 で作る統合ツリーの中だけで動く。

## 全体フロー

1. **topic ブランチと統合ツリーを作り、その中へ移る** → 「1. topic と統合ツリーを作る」
2. **前提を集めて `brief.md` を書く** → 「2. 前提を集める」
3. **Explore に調査させて `map.md` を書く** → 「3. 調査する」
4. **タスクを設計して `ledger.md` v0 を書き、topic PR を作る** → 「4. タスクを設計する」
5. **権限を先に通す** → 「5. 権限を先に通す」
6. **タスクを `TaskCreate` で登録する** → 「6. タスクを登録する」
7. **ループ**: 空き枠にワークフローを起動し、完了通知を受けたら取り込んで topic PR を更新する → 「7. 回す」
8. **全タスク完了後にフル検証して topic PR を仕上げる** → 「8. 仕上げる」

各エージェントが読む契約は次のファイルにある。**プロンプトを組み立てる直前・スクリプトを書く
直前に対応するファイルを Read する**（コンパクションで本文が失われても取り直せる）。

- スクリプトの骨組み: [workflow-script.md](workflow-script.md)
- 実装・修正エージェントの契約: [implementation-prompt.md](implementation-prompt.md)
- レビューエージェントの契約: [review-prompt.md](review-prompt.md)
- 裁定エージェントの契約: [judge-prompt.md](judge-prompt.md)
- 再計画エージェントの契約: [escalation-prompt.md](escalation-prompt.md)
- PR 本文エージェントの契約: [pr-body-prompt.md](pr-body-prompt.md)
- レビュー記録（review.json）の手順: [review-store.md](review-store.md)
- topic PR の作り方・本文の書式・更新: [topic-pr.md](topic-pr.md)
- リードの統合レーン: [integration.md](integration.md)
- 台帳の書式と復旧手順: [ledger.md](ledger.md)
- 設計の理由と失敗の実績: [design-notes.md](design-notes.md)

## エージェントの構成

1 タスク = 1 ワークフローで、中は直列（実装 → レビュー → 裁定 → 修正 → … → PR 本文）。同時に
走るのはレビューだけである（standard なら通常レビューと敵対的レビューの 2 体）。
**同時 3 本を増やさない**（全体図と理由は [design-notes.md](design-notes.md) の
「全体の流れ」と「なぜ同時 3 本までか」）。

## 1. topic と統合ツリーを作る

「起動前の確認」で決めた base ブランチから `topic/<作業名>` を作り、**空コミット 1 つを載せて
すぐ push** する（タスク PR の base になる。空コミットが要る理由は
[design-notes.md](design-notes.md)「なぜ最初に draft の topic PR を作るか」）。続けて**統合ツリーを
作り、セッションをその中へ移す**。以降 `<統合ツリー>` はこの絶対パスを指す。

```bash
git fetch origin
git branch --no-track topic/<作業名> origin/<base>
git worktree add .claude/worktrees/supervisor-<作業名> topic/<作業名>
git -C .claude/worktrees/supervisor-<作業名> commit --allow-empty \
  -m "chore: supervisor の統合レーンを開始する"
git -C .claude/worktrees/supervisor-<作業名> push -u origin topic/<作業名>
```

コミットメッセージの書式は `git log` から読める既存の書式に倣う。

```
EnterWorktree({ path: ".claude/worktrees/supervisor-<作業名>" })
```

**`git checkout` / `git switch` を使わない**（上のコマンドはユーザーのカレントブランチを動かさない。
空コミットも統合ツリーの中で作るので、ユーザーの作業ツリーに触らない。
理由は [design-notes.md](design-notes.md)「なぜリードに専用の worktree を与えるか」）。

**`EnterWorktree` が失敗したら、タスクを登録せずそこで止める。** 統合レーンは `git merge` と
`git reset --hard` を繰り返すので、統合ツリーに入らずに進めるとユーザーの作業ツリーを巻き込む
（[design-notes.md](design-notes.md)「なぜ `EnterWorktree` を必須にし、それでも `-C` を書くか」）。

統合ツリーのディレクトリが `git status` に未追跡で出るリポジトリでは、**`.gitignore` に
`/.claude/worktrees/` を足すことをユーザーに提案する**（勝手にコミットしない）。

## 2. 前提を集める

`<ベース>` を 1 行受け取る（無ければ作られる）。

```bash
<スクリプト>/place.py base-dir --work <作業名>
```

次を特定して `<ベース>/brief.md` に書く。全サブエージェントがこれを読む。

- **検証コマンド一式**: `.github/workflows/` などの CI 定義・CLAUDE.md・docs から、
  「マージしてよい」と言える全チェック（テスト・lint・フォーマット・ビルド）を列挙する。
  **ビルドだけを流すコマンドも分けて書く**——取り込みの直後はビルドだけを流す
  （[integration.md](integration.md) §2）。
- **外形動作を確かめる手順**: アプリや CLI を実際に起動して動きを見る手順（`/run` や `/verify`
  スキル、起動コマンド）。レビューとリードは実装の報告を信じず自分で動かす。
- **不可侵パス**: 触ってはならないパス、専用の手順が要るパス。
- **ブランチとコミットの規約**: デフォルトブランチ名、ブランチ命名、コミット署名、PR テンプレート。
  **PR タイトルを検査する job があるかどうかも書く**（`^(feat|fix|docs|…): ` の形で先頭を見るもの。
  あるときの書き方は [topic-pr.md](topic-pr.md)「タイトルの接頭辞」）。

## 3. 調査する

- コードベースの現状は **Explore エージェント**（`model: "opus"`、"very thorough"）に調べさせる。
  行数を数える程度は自分でやってよい。
- 結果は `<ベース>/map.md` に書く。**書くのは入口だけ**——関連ディレクトリと
  主要なクラス・関数の名前を数個。変更するファイルの一覧や行番号つきの内部構造は書かない
  （理由は [implementation-prompt.md](implementation-prompt.md) の §0）。

## 4. タスクを設計する

- **依存はタスクの `blockedBy` で表す。** 前のタスクの成果を前提にする作業は依存を張る。
  依存が無い作業は並列に走らせる。
- **同じ中核モジュールを構造から書き換えるタスクは直列にする。**
- **達成状況を検分するタスク・一覧表を書くタスクは、対象タスクに `blockedBy` を張る。**
- **1 タスクの大きさ**: 1 ワークフローで完結し、合否が一意に判定できる大きさ。機能単位で割る。
- **`tier` を付ける**:
  - `light`: docs の追随、生成物の機械的な更新、中核ロジックに触れない数ファイルの変更。
    **複数の light を 1 ワークフローに束ね、1 ブランチ・1 PR にする。** レビューは通常 1 本。
  - `standard`（既定）: ロジック・中核・挙動に関わる変更。通常＋敵対的の 2 本立て。
    **迷ったら standard にする。**
- **各タスクに次を書く**（台帳の書式は [ledger.md](ledger.md)）:
  - DoD: 達成すべき状態で書く。「このファイルのこの行をこう変える」という手順にしない。
  - 受け入れ基準と検証: 合否を判定する観点と、実際に叩くコマンド。
  - スコープ境界: やること / やらないこと、触ってよい領域 / 触ってはならない領域。
  - 調査の入口: 関連ディレクトリと主要な名前を数個。
  - 隣接タスクとの契約: 並列に走る他タスクと共有する I/F・前提の一行要約。
  - `tier`。
- **同じファイルを触る 2 タスクを並列にするなら、触ってよい領域を明示する。** worktree は
  ファイルの編集衝突しか防がない。意味の衝突が深いならタスクを直列にする。
- 設計し終えたら `<ベース>/ledger.md` に v0 を書く。
- **続けて topic PR を draft で作る**（本文の書式とコマンドは [topic-pr.md](topic-pr.md)。
  作る直前にそこを Read する）。
  **作成が非 0 で終わったら 1 回だけ再試行し、それでも失敗したらコマンドとエラー出力を
  ユーザーに示して止まる**（§5・§6 に進まない。台帳 v0 と topic ブランチは残るので、原因が
  解消したらここから続けられる）。
  **返ってきた PR 番号を台帳に控える**——タスク PR のタイトルに入り（`args.topicPr`）、
  以降の更新先になる。

## 5. 権限を先に通す

権限の確認はリードの画面に出る。**ワークフロー内のエージェントが権限プロンプトを出すと
ワークフローが止まる**ので、聞かれる前に許可リストへ入れる。

- `<ベース>/brief.md` に書いた検証コマンド・起動コマンド・プロジェクト固有の MCP
- スキル付属の 4 スクリプト（このスキルの `allowed-tools` はリードにしか効かない。ワークフロー内の
  エージェントも同じものを呼ぶ）

**`<スクリプト>` は「起動前の確認」で確定した絶対パスに置き換えて登録する**（`${CLAUDE_SKILL_DIR}`
という文字列のまま登録しない。サブエージェントはこの変数を持たず絶対パスでコマンドを打つので、
変数のままの規則とは一致しない）。

```
Bash(<スクリプト>/review.py *)
Bash(<スクリプト>/place.py *)
Bash(<スクリプト>/verify.py *)
Bash(<スクリプト>/worktree.py *)
```

## 6. タスクを登録する

各タスクを `TaskCreate` で登録する。

- `subject`: `[task<番号>] <件名>`
- `description`: DoD・受け入れ基準・スコープ境界・調査の入口・隣接タスクとの契約
- `metadata`: `{ "tier": "standard", "branch": "topic/<作業名>--task-<番号>", "approved": false }`

依存は `TaskUpdate` の `addBlockedBy` で張る。

## 7. 回す

**同時に走らせるワークフローは 3 本まで。** 枠が空いたら、`blockedBy` が解けているタスクのうち
番号が小さいものから起動する。

### 起動する

[workflow-script.md](workflow-script.md) の骨組みからスクリプトを組み立て、`args` を
**実オブジェクトで**渡す（JSON 文字列で渡すとスクリプト側で全フィールドが `undefined` になる）。

```
Workflow({ script: <組み立てたスクリプト>, args: {
  task: { id: "task4", subject: "...", tier: "standard",
          branch: "topic/<作業名>--task-4", dod: "...", acceptance: "...",
          scope: "...", entrypoints: "...", contracts: "..." },
  topic: "topic/<作業名>", base: "<ベース>", work: "<作業名>", topicPr: 100
}})
```

返り値の `runId` を台帳に控える（[ledger.md](ledger.md)）。ワークフローはバックグラウンドで走り、
完了は通知で届く。**走行中に部分結果は届かない。**

### 完了通知を受けたら

返り値は 3 種類ある。**どれも鵜呑みにせず、実物で確かめてから動く**（[integration.md](integration.md) §1）。

| 返り値 | どうするか |
| --- | --- |
| `approved: true` | `verify.py`・`review.py list --require-empty`・レビュアーの体数（`reviewers` と `expectedReviewers`）の 3 つを通してから PR にして topic へ取り込む（[integration.md](integration.md) §1） |
| `blocked: true` | `questions` をユーザーに上げ、答えを受けてタスクを組み直し、起動し直す |
| `failed: true` | `reason` と review.json を見て、立て直すか、ユーザーに上げる（下記） |

`topicPr` は §4 で控えた topic PR の番号で、タスク PR のタイトルに入る
（[topic-pr.md](topic-pr.md)「タイトルの接頭辞」）。

取り込んだら台帳と **topic PR の本文**を更新し（[topic-pr.md](topic-pr.md)）、空いた枠に次のタスクを
起動する。

### 失敗したワークフローを立て直す

**やり直しではなく続きから始める。** push 済みのコミット・review.json・引き継ぎノートが
残っているので、それを起点にする（**PR はまだ作られていない**）。手順（`resumeFromRunId` と
`resumeFrom` の使い分け・打ち切りの条件）は [ledger.md](ledger.md)「落ちたワークフローの扱い」に
ある。**立て直す直前にそこを Read する。**

**タスクを `blocked` / `failed` で打ち切ったら、台帳と topic PR の本文を更新する**——タスク一覧の
状態を書き換え、打ち切った理由を「残課題」に 1 行残す（[topic-pr.md](topic-pr.md)）。

### 質問・blocked を受けたら

- **自分で答えられるなら答えて、タスクを組み直して起動し直す**（タスク設計の意図・ベース資料・
  他タスクとの整合）。
- **ユーザーに上げるのは次の 4 つだけ**: 作業範囲の解釈が割れる / 規模が当初想定から大きく
  増減する / 後戻りしにくい設計上の取引が要る / ユーザーの指示が既存の DoD や設計文書と矛盾する。
- **確認を待つ間も走行中のワークフローは完走させ、取り込みは進める。** 答え次第で無駄に
  なりそうなタスクだけ、新しく起動するのを止める。

### 完了の根拠

**タスクリストの `completed` を完了の根拠にしない。** subagent が終了すると harness が spawn 元の
タスクを自動で `completed` にすることがある。完了の根拠は次の 4 つだけである。

1. ワークフローの返り値が `approved: true` であること
2. `verify.py`（ブランチとコミットの実在）
3. `review.py list --require-empty`（review.json が存在し、open が 0 件）
4. 返り値の `reviewers` が `expectedReviewers` 以上であること（走るはずのレビュアーが揃った）

**3 と 4 は別のことを確かめている。** 3 は「指摘が全件決着したこと」で、レビュアーが何体
走ったかは review.json からは分からない（指摘 0 件で終わったのか起動しなかったのかが同じに
見える）。4 の 2 つの数だけがその食い違いを表せる（[integration.md](integration.md) §1 手順 4）。

## 8. 仕上げる

1. **台帳の全タスクが `merged` か `blocked` になっていることを確かめ、git と突き合わせる。**
   `git -C <統合ツリー> log --oneline origin/topic/<作業名>` に、`merged` のタスクごとに
   `--no-ff` のマージコミットが 1 つあることを見る。数が合わなければ取り込み漏れである。
2. topic を最新化し、**`<統合ツリー>` の中で**`<ベース>/brief.md` の検証コマンド一式と外形動作を
   フルで 1 回流す。
3. 台帳を最終版に更新する（commit しない）。
4. **topic PR の本文を最終版に差し替える**（[topic-pr.md](topic-pr.md)「最終版の本文」。
   差し替える直前にそこを Read する）。進行中の 4 節を最新にし、「変更による挙動の変化」
   「確認項目」「検証結果」「自律判断の記録」（§9）を足す。
   **台帳は commit されないので、ユーザーが残る形で読める記録はこの本文だけになる。**
   タスク一覧（件名・PR 番号・却下した残件の件数）も本文に写す。
5. **`merged` が 1 件以上なら `gh pr ready <topicPR番号>` でレビュー可能にする。**
   **マージはしない**（ユーザーが行う）。1 件も無ければ `ready` を叩かず draft のまま残す
   （topic ブランチも消さない。あとで続きを頼まれたときの足場になる）。
6. 何がマージされたか・失敗で残ったタスク・自分の判断で変えた目標・先送りにした作業・
   残課題をユーザーにまとめる。**topic PR の URL を必ず添える。**
7. 統合ツリーを外す。**台帳と引き継ぎノートはこの中にあるので一緒に消える。** 消してよいか
   **ユーザーに確認してから**行う。PR がマージされる前に消すと、ユーザーが追加を頼んだときに
   再開の足場が無い。**残すと言われたら統合ツリーも残す。**

   ```
   ExitWorktree({ action: "keep" })
   ```

   ```bash
   git -C <統合ツリー> status --porcelain     # 空でなければ中身をユーザーに示す
   git worktree remove .claude/worktrees/supervisor-<作業名>
   ```

   `action: "keep"` にするのは、`path` で入った worktree を `ExitWorktree` が消さない仕様だから
   である（消すのは次の `git worktree remove`）。未コミットの変更があると `git worktree remove` は
   拒む。**`--force` を先に付けず、何が残っているかを示す**（コンフリクト解消の途中で終わって
   いた可能性がある）。**`status` が空でも台帳は消える**——無視されたファイルは `status` に出ず、
   `git worktree remove` にも拒まれない。

## 9. 自律判断を記録する

ユーザーに確認せず自分で決めたことは、判断内容と判断材料（根拠・退けた代替案）を残す。
とくに次の 2 つは**必ず**残す。

- 最終目標・DoD・スコープを自分の判断で変えた
- やる予定だった作業を先送りにした、または対象外にした

書き先:

- 個別タスクの中で閉じる判断 → そのタスクの PR 本文
- 作業全体に関わる判断 → まず台帳の `## 自律判断の記録` に書き、§8 で topic PR 本文の同名
  セクションへ転記する
  - `### 変更した最終目標・DoD・スコープ`
  - `### 先送り・対象外にした作業`
  - **台帳は commit しないので、PR 本文への転記を省かない。**
- **ワークフローが返した `decisions` と `deferrals` を集約して載せる**（バックグラウンドで
  下された判断を取りこぼさない）。
- **リードがコンフリクトを解いてコードを書いた場合もここに書く**（リードがコードに触れる
  唯一の場面。[integration.md](integration.md) §2）

## 10. 運用上の注意

- ユーザーの指示（削除・仕様変更）が既存の DoD や設計文書と矛盾するときは、黙って従わず
  **事実と帰結を先に示す**（例: 消す対象が別の成果物を兼ねている、検査が任意に格下げになる）。
  示したうえで判断が明らかなら、妥当な解釈で進めてよい。
- ワークフローの `blocked` 報告・失敗報告を鵜呑みにしない。`git show` やテストの実行で
  確かめてから裁く。
- 進捗の報告は「どのタスク・どの PR がどの状態か」を軸に短くまとめる。
