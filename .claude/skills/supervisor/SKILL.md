---
name: supervisor
description: >-
  dynamic workflow で開発作業を並列に進める監督者ワークフロー。リードは大きな作業をタスクに割り、
  タスクごとに dynamic workflow を 1 本ずつ起動する。ワークフローは worktree 付きのサブエージェントで
  実装・レビュー・裁定・修正を回し、レビュー承認まで面倒を見る。
  1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。承認済みブランチの topic への取り込みはリード本体が
  1 本ずつ行い、最後に topic → デフォルトブランチの PR を作る（最終マージはユーザー）。
  進捗は `.git` 配下の台帳ファイルに残すので、git の履歴を汚さずに、セッションが落ちても
  続きから再開できる。
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
  - Bash(gh pr view *)
  - Bash(gh pr list *)
  - Bash(gh pr close *)
  - Bash(gh pr comment *)
  - Bash(gh repo view *)
  - Bash(~/.claude/skills/supervisor/scripts/gh-review.py *)
  - Bash(~/.claude/skills/supervisor/scripts/place.py *)
  - Bash(~/.claude/skills/supervisor/scripts/verify.py *)
  - Bash(~/.claude/skills/supervisor/scripts/worktree.py *)
---

# supervisor（dynamic workflow による並列開発の統括）

作業対象: $ARGUMENTS

あなたは**リード**である。次の 6 つに専念する。

- 大きな作業をタスクに割り、DoD（完了条件＝達成すべき状態）を確定する
- タスクごとに dynamic workflow を 1 本起動する（同時 3 本まで）
- 承認済みブランチを topic へ 1 本ずつ取り込む
- 台帳を更新する
- ユーザーと話す
- 最終 PR を作る

実装・レビュー・裁定・修正はワークフローの中のサブエージェントが行う。**リードは findings の
本文を読まない。** ワークフローが返すのは件数と検証の要約だけで、指摘の本文は PR のレビュー
スレッドにある。findings の本文がリードの画面に流れてきたら、この境界が壊れている。

## 用語

- **タスク**: 1 本のワークフローが完結させる作業単位。合否が一意に判定できる大きさに割り、
  リスク階層 `tier`（`light` / `standard`）を付ける。
  **1 タスク = 1 ワークフロー = 1 ブランチ = 1 PR。**
- **ベースディレクトリ**: ベース 3 ファイルと引き継ぎノートの置き場。`.git` 配下なので
  **git の追跡対象に入らず、PR の差分にも出ない**。全 worktree から同じ絶対パスに解決でき、
  リポジトリのクローンが残る限りセッションをまたいで残る。パスは自分で組み立てず、次で 1 行受け取る。

  ```bash
  ~/.claude/skills/supervisor/scripts/place.py base-dir --work <作業名>
  ```

- **ベース 3 ファイル**: 全サブエージェントが毎回読む前提資料。ベースディレクトリに
  `brief.md`（検証コマンド・不可侵パス・ブランチ規約）、`map.md`（コードベースの入口）、
  `ledger.md`（台帳）の 3 つを置く。
- **引き継ぎノート**: `<ベース>/notes/task<番号>/<役割>-r<ラウンド>.md`。各サブエージェントが
  読んだ箇所・実行した検証と結果・構造の要点を残し、次のラウンドの同じ役割が先に読む。
- **台帳**: ベースディレクトリの `ledger.md`。タスク分解・承認状態・自律判断を残す。
  セッションが落ちたときはここから再開する（[ledger.md](ledger.md)）。**commit しない**ので、
  ユーザーに見せる記録は最終 PR 本文に書く（§9）。

## 起動前の確認

1. git リポジトリであること、`gh auth status` が通ることを確認する。
2. 付属スクリプト 4 本が実行できることを確かめる。エージェントはパスを直に叩くので、実行ビットが
   無いと `permission denied` になる。

   ```bash
   cd ~/.claude/skills/supervisor/scripts && ls -l gh-review.py place.py verify.py worktree.py
   ```

   `x` が欠けているものに `chmod +x` する。
3. **自分がどのモデルで動いているかをユーザーに申告する。** `opus` でなければ、タスク設計に
   入る前に `/model` での切り替えを提案する（下の表は `opus` を前提にコストを見積もっている）。

## 役割ごとのモデルと effort

ワークフローの `agent()` には**必ず `model` を明示する**（セッション既定の継承に頼らない）。
機械的な段階には `effort` を効かせる。

| 役割 | model | effort |
| --- | --- | --- |
| リード（このセッション） | `opus` | — |
| 実装・修正（standard） | `opus` | 既定 |
| 実装・修正（light） | `sonnet` | `medium` |
| 実装の差し替え（impl-b） | `opus` | 既定 |
| 通常レビュー（standard） | `opus` | `medium` |
| 通常レビュー（light） | `sonnet` | `medium` |
| 敵対的レビュー（standard のみ） | `opus` | `medium` |
| doc だけの修正の再レビュー | `sonnet` | `low` |
| 裁定 | `opus` | `medium` |
| 再計画（エスカレーション） | `opus` | 既定 |
| Explore 調査（リードの意思決定用） | `opus` | 既定 |

指定したモデルが使えなければ 1 つ下げる（`opus` → `sonnet`）。`sonnet` も使えなければ `model` を省く。

## 全体フロー

1. **前提を集めて `brief.md` を書く** → 「1. 前提を集める」
2. **Explore に調査させて `map.md` を書く** → 「2. 調査する」
3. **タスクを設計して `ledger.md` v0 を書く** → 「3. タスクを設計する」
4. **topic ブランチを作って push** → 「4. topic を作る」
5. **権限を先に通す** → 「5. 権限を先に通す」
6. **タスクを `TaskCreate` で登録する** → 「6. タスクを登録する」
7. **ループ**: 空き枠にワークフローを起動し、完了通知を受けたら統合する → 「7. 回す」
8. **全タスク完了後にフル検証して最終 PR を作る** → 「8. 仕上げる」

各エージェントが読む契約は次のファイルにある。**プロンプトを組み立てる直前・スクリプトを書く
直前に対応するファイルを Read する**（コンパクションで本文が失われても取り直せる）。

- スクリプトの骨組み: [workflow-script.md](workflow-script.md)
- 実装・修正エージェントの契約: [implementation-prompt.md](implementation-prompt.md)
- レビューエージェントの契約: [review-prompt.md](review-prompt.md)
- 裁定エージェントの契約: [judge-prompt.md](judge-prompt.md)
- 再計画エージェントの契約: [escalation-prompt.md](escalation-prompt.md)
- GitHub レビューコメントの手順: [github-comments.md](github-comments.md)
- リードの統合レーン: [integration.md](integration.md)
- 台帳の書式と復旧手順: [ledger.md](ledger.md)
- 設計の理由と失敗の実績: [design-notes.md](design-notes.md)

## エージェントの構成

```
リード（このセッション・ワークフローの外）
└─ タスクごとの dynamic workflow（同時 3 本まで）
   ├─ 実装 / 修正 agent（worktree あり）
   ├─ レビュー agent（worktree あり・standard は通常＋敵対的の 2 体を並列）
   ├─ 裁定 agent（worktree あり）
   └─ 再計画 agent（worktree あり・読み取りのみ）
      └─ Explore や /code-review の子
```

1 タスクの中は直列（実装 → レビュー → 裁定 → 修正 → …）で、同時に走るのは通常レビューと
敵対的レビューの 2 体だけである。**同時 3 本を増やさない**（理由は
[design-notes.md](design-notes.md)）。

## 1. 前提を集める

置き場を 1 行受け取る（無ければ作られる）。以降 `<ベース>` はこの絶対パスを指す。

```bash
~/.claude/skills/supervisor/scripts/place.py base-dir --work <作業名>
```

次を特定して `<ベース>/brief.md` に書く。全サブエージェントがこれを読む。

- **検証コマンド一式**: `.github/workflows/` などの CI 定義・CLAUDE.md・docs から、
  「マージしてよい」と言える全チェック（テスト・lint・フォーマット・ビルド）を列挙する。
- **外形動作を確かめる手順**: アプリや CLI を実際に起動して動きを見る手順（`/run` や `/verify`
  スキル、起動コマンド）。レビューとリードは実装の報告を信じず自分で動かす。
- **不可侵パス**: 触ってはならないパス、専用の手順が要るパス。
- **ブランチとコミットの規約**: デフォルトブランチ名、ブランチ命名、コミット署名、PR テンプレート。

## 2. 調査する

- コードベースの現状は **Explore エージェント**（`model: "opus"`、"very thorough"）に調べさせる。
  行数を数える程度は自分でやってよい。
- 結果は `<ベース>/map.md` に書く。**書くのは入口だけ**——関連ディレクトリと
  主要なクラス・関数の名前を数個。変更するファイルの一覧や行番号つきの内部構造は書かない
  （理由は [implementation-prompt.md](implementation-prompt.md) の §0）。

## 3. タスクを設計する

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

## 4. topic を作る

デフォルトブランチから `topic/<作業名>` を作り、**すぐ push** する（タスク PR の base になる）。
ベース 3 ファイルは commit しない（`.git` 配下にあり、全 worktree から読める）。

## 5. 権限を先に通す

権限の確認はリードの画面に出る。**ワークフロー内のエージェントが権限プロンプトを出すと
ワークフローが止まる**ので、聞かれる前に許可リストへ入れる。

- `<ベース>/brief.md` に書いた検証コマンド・起動コマンド・プロジェクト固有の MCP
- スキル付属の 4 スクリプト（このスキルの `allowed-tools` はリードにしか効かない。ワークフロー内の
  エージェントも同じものを呼ぶ）

```
Bash(~/.claude/skills/supervisor/scripts/gh-review.py *)
Bash(~/.claude/skills/supervisor/scripts/place.py *)
Bash(~/.claude/skills/supervisor/scripts/verify.py *)
Bash(~/.claude/skills/supervisor/scripts/worktree.py *)
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
  topic: "topic/<作業名>", base: "<ベース>", work: "<作業名>"
}})
```

返り値の `runId` を台帳に控える（[ledger.md](ledger.md)）。ワークフローはバックグラウンドで走り、
完了は通知で届く。**走行中に部分結果は届かない。**

### 完了通知を受けたら

返り値は 3 種類ある。**どれも鵜呑みにせず、実物で確かめてから動く**（[integration.md](integration.md) §1）。

| 返り値 | どうするか |
| --- | --- |
| `approved: true` | `verify.py` と `gh-review.py gate` を通してから topic へ取り込む（[integration.md](integration.md)） |
| `blocked: true` | `questions` をユーザーに上げ、答えを受けてタスクを組み直し、起動し直す |
| `failed: true` | `reason` と PR のスレッドを見て、立て直すか、ユーザーに上げる（下記） |

取り込んだら台帳を更新し、空いた枠に次のタスクを起動する。

### 失敗したワークフローを立て直す

**やり直しではなく続きから始める。** push 済みのコミット・PR・レビュースレッド・引き継ぎノートは
残っているので、それを起点にする。

1. 同じセッションの中なら、まず `resumeFromRunId` で再実行できる。完了済みの `agent()` は
   `journal.jsonl` のキャッシュから返るので安い。**先に走行中の run を `TaskStop` で止める。**

   ```
   Workflow({ scriptPath: "<返り値に入っていたパス>", resumeFromRunId: "<runId>" })
   ```

   スクリプトを 1 行でも変えると、変えた箇所より後ろは全部再実行される。

2. セッションが落ちた・スクリプトを組み直したときは、`resumeFrom` を付けて新しく起動する。

   ```
   Workflow({ script: <スクリプト>, args: { task, topic, base, work,
     resumeFrom: { branch: "<タスクブランチ>", sha: "<前コミット>",
                   pr: <PR 番号>, transcriptDir: "<完了通知に入っていたパス>" } }})
   ```

   `sha` は `git log origin/<タスクブランチ> -1 --format=%H` で取る。PR が既にあるなら
   その番号を渡す（作り直させない）。

3. 立て直しても承認に至らないタスクは、台帳で `blocked` にして事実をユーザーに上げる。
   **他のタスクは止めずに進める。**

### 質問・blocked を受けたら

- **自分で答えられるなら答えて、タスクを組み直して起動し直す**（タスク設計の意図・ベース資料・
  他タスクとの整合）。
- **ユーザーに上げるのは次の 4 つだけ**: 作業範囲の解釈が割れる / 規模が当初想定から大きく
  増減する / 後戻りしにくい設計上の取引が要る / ユーザーの指示が既存の DoD や設計文書と矛盾する。
- **確認を待つ間も走行中のワークフローは完走させ、承認と統合は進める。** 答え次第で無駄に
  なりそうなタスクだけ、新しく起動するのを止める。

### 完了の根拠

**タスクリストの `completed` を完了の根拠にしない。** subagent が終了すると harness が spawn 元の
タスクを自動で `completed` にすることがある。完了の根拠は次の 3 つだけである。

1. ワークフローの返り値が `approved: true` であること
2. `verify.py`（ブランチ・コミット・PR の実在）
3. `gh-review.py gate`（未解決 0 件・PENDING 0 件・要求した役割のレビュー提出）

## 8. 仕上げる

1. **台帳の全タスクが `merged` か `blocked` になっていることを確かめ、git と突き合わせる。**
   `git log --oneline origin/topic/<作業名>` に、`merged` のタスクごとに `--no-ff` の
   マージコミットが 1 つあることを見る。数が合わなければ取り込み漏れである。
2. topic を最新化し、`<ベース>/brief.md` の検証コマンド一式と外形動作をフルで 1 回流す。
3. 台帳を最終版に更新する（commit しない）。
4. topic → デフォルトブランチの PR を作る。本文には構成 PR の一覧・検証結果・
   「自律判断の記録」を書く（§9）。**マージはしない**（ユーザーが行う）。
   **台帳は commit されないので、ユーザーが残る形で読める記録はこの本文だけになる。**
   タスク一覧（件名・ブランチ・PR 番号・findings 件数）も本文に写す。
5. 何がマージされたか・失敗で残ったタスク・自分の判断で変えた目標・先送りにした作業・
   残課題をユーザーにまとめる。
6. 台帳と引き継ぎノートを消してよいか**ユーザーに確認してから**消す。PR がマージされる前に
   消すと、ユーザーが追加を頼んだときに再開の足場が無い。

   ```bash
   rm -rf "$(git rev-parse --path-format=absolute --git-common-dir)/supervisor"
   ```

## 9. 自律判断を記録する

ユーザーに確認せず自分で決めたことは、判断内容と判断材料（根拠・退けた代替案）を残す。
とくに次の 2 つは**必ず**残す。

- 最終目標・DoD・スコープを自分の判断で変えた
- やる予定だった作業を先送りにした、または対象外にした

書き先:

- 個別タスクの中で閉じる判断 → そのタスクの PR 本文
- 作業全体に関わる判断 → 台帳の `## 自律判断の記録` と、最終 PR 本文の同名セクション
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
