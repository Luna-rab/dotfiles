---
name: team-supervisor
description: >-
  入れ子の subagent で開発作業を並列に進める監督者ワークフロー。監督者（リード）は
  大きな作業をタスクに割り、タスクごとに「サブリーダー」subagent を worktree つきで立てる。
  サブリーダーは自分の worktree で実装 subagent とレビュー subagent を動かし、
  レビュー承認まで面倒を見る。1 タスク = 1 worktree = 1 ブランチ = 1 PR = 1 サブリーダー。
  承認済みブランチの topic への取り込みはリード本体が 1 本ずつ行い、最後に
  topic → デフォルトブランチの PR を作る（最終マージはユーザー）。
  進捗は topic 上の台帳ファイルに残すので、セッションが落ちても続きから再開できる。
when_to_use: >-
  ユーザーが `/team-supervisor` と明示的に打ったときだけ起動する。多数のエージェントを
  起動する高コストなワークフローのため、自動では発動しない
  （`disable-model-invocation` を付けてある）。dynamic workflow で回す supervisor スキルとは
  トリガー語が重なるので、どちらを使うかはユーザーが選ぶ。走行中にユーザーと話しながら
  方針を変えたい・途中から再開したい場合はこちらが向く。
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
  - Bash(~/.claude/skills/team-supervisor/scripts/gh-review.py *)
hooks:
  SubagentStop:
    - hooks:
        - type: command
          command: ~/.claude/skills/team-supervisor/scripts/subagent-stop.sh
---

# team-supervisor（入れ子の subagent による並列開発の統括）

作業対象: $ARGUMENTS

あなたは**リード**である。次の 6 つに専念する。

- 大きな作業をタスクに割り、DoD（完了条件＝達成すべき状態）を確定する
- タスクごとにサブリーダーを立てる（同時 3 体まで）
- 承認済みブランチを topic へ 1 本ずつ取り込む
- 台帳を更新する
- ユーザーと話す
- 最終 PR を作る

実装とレビューはサブリーダー配下の subagent が行う。**リードは findings の本文を読まない。**

## 用語

- **サブリーダー**: 1 タスクを担当する subagent。`isolation: "worktree"` で起動し、その worktree で
  実装 subagent とレビュー subagent を動かし、承認まで進めて 1 行で報告する。名前は `task<番号>`。
  **1 タスク = 1 worktree = 1 ブランチ = 1 PR = 1 サブリーダー。**
- **タスク**: 1 サブリーダーが完結させる作業単位。合否が一意に判定できる大きさに割り、
  リスク階層 `tier`（`light` / `standard`）を付ける。
- **台帳**: topic 上の `docs/supervisor/<作業名>.ledger.md`。タスク分解・承認状態・自律判断を残す。
  セッションが落ちたときはここから再開する（[ledger.md](ledger.md)）。
- **ベース 3 ファイル**: サブリーダーと subagent が毎回読む前提資料。すべて topic 上に置く。
  `<作業名>.brief.md`（検証コマンド・不可侵パス・ブランチ規約）、
  `<作業名>.map.md`（コードベースの入口）、`<作業名>.ledger.md`。

## 起動前の確認

1. 環境変数を確かめる。

   ```bash
   echo "teams=${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-unset} depth=${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-3}"
   ```

   `teams=unset depth=5` でなければ、`.claude/settings.json` の `env` を直して
   セッションを開き直すよう伝え、**停止する**（どちらが欠けても壊れる。
   [design-notes.md](design-notes.md)）。
2. git リポジトリであること、`gh auth status` が通ることを確認する。
3. `test -x ~/.claude/skills/team-supervisor/scripts/subagent-stop.sh`。無ければ `chmod +x` する
   （[hooks.md](hooks.md)）。

## 役割ごとのモデル

`Agent` ツールの `model` を**必ず明示する**（セッション既定の継承に頼らない）。

| 役割 | model |
| --- | --- |
| リード（このセッション） | `fable` |
| サブリーダー（standard） | `opus` |
| サブリーダー（light の束ね） | `sonnet` |
| 実装 subagent（standard） | `opus` |
| 実装 subagent（light） | `sonnet` |
| 実装 subagent の差し替え（impl-b） | `fable` |
| レビュー（standard） | `opus` |
| レビュー（light） | `sonnet` |
| コンフリクト解消レビュー | `opus` |
| Explore 調査（リードの意思決定用） | `opus` |

指定したモデルが使えなければ 1 つ下げる（`fable` → `opus` → `sonnet`）。`sonnet` も使えなければ
`model` を省く。リードが `fable` 以外で動いていたら、タスク設計の前に `/model` での切り替えを
ユーザーに提案する。`effort` は spawn 時に指定できない——軽くしたいときは `model` を下げる。

## 全体フロー

1. **前提を集めて `brief.md` を書く** → 「1. 前提を集める」
2. **Explore に調査させて `map.md` を書く** → 「2. 調査する」
3. **タスクを設計して `ledger.md` v0 を書く** → 「3. タスクを設計する」
4. **topic ブランチを作り、ベース 3 ファイルを commit して push** → 「4. topic を作る」
5. **権限を先に通す** → 「5. 権限を先に通す」
6. **タスクを `TaskCreate` で登録する** → 「6. タスクを登録する」
7. **ループ**: 空き枠にサブリーダーを spawn し、承認報告を受けたら統合する → 「7. 回す」
8. **全タスク完了後にフル検証して最終 PR を作る** → 「8. 仕上げる」

各エージェントが読む契約は次のファイルにある。**プロンプトを組み立てる直前に対応するファイルを
Read する**（コンパクションで本文が失われても取り直せる）。

- サブリーダーの契約: [subleader-prompt.md](subleader-prompt.md)
- 実装 subagent の契約: [implementation-prompt.md](implementation-prompt.md)
- レビュー subagent の契約: [review-prompt.md](review-prompt.md)
- GitHub レビューコメントの手順: [github-comments.md](github-comments.md)
- リードの統合レーン: [integration.md](integration.md)
- 台帳の書式と復旧手順: [ledger.md](ledger.md)
- `SubagentStop` フック: [hooks.md](hooks.md)
- この設計を選んだ理由: [design-notes.md](design-notes.md)

## エージェントの階層

```
リード（層 0）
└─ サブリーダー task<番号>（層 1・worktree あり・背景・同時 3 体まで）
   ├─ 実装 / 修正 subagent（層 2・isolation なし＝親の worktree を共有・同期）
   └─ レビュー subagent（層 2・isolation あり＝自分の worktree・同期）
      └─ Explore や /code-review の子（層 3 以下）
```

入れ子は 5 層まで、同時に走る subagent は 20 体まで（サブリーダー自身も 1 枠を使う）。
同時 3 体で最大 15 体に収まる。**3 体を増やさない**（[design-notes.md](design-notes.md)）。

## 1. 前提を集める

次を特定して `docs/supervisor/<作業名>.brief.md` に書く。サブリーダーと subagent はこれを読む。

- **検証コマンド一式**: `.github/workflows/` などの CI 定義・CLAUDE.md・docs から、
  「マージしてよい」と言える全チェック（テスト・lint・フォーマット・ビルド）を列挙する。
- **外形動作を確かめる手順**: アプリや CLI を実際に起動して動きを見る手順（`/run` や `/verify`
  スキル、起動コマンド）。レビューとリードは実装の報告を信じず自分で動かす。
- **不可侵パス**: 触ってはならないパス、専用の手順が要るパス。
- **ブランチとコミットの規約**: デフォルトブランチ名、ブランチ命名、コミット署名、PR テンプレート。

## 2. 調査する

- コードベースの現状は **Explore エージェント**（`model: "opus"`、"very thorough"）に調べさせる。
  行数を数える程度は自分でやってよい。
- 結果は `docs/supervisor/<作業名>.map.md` に書く。**書くのは入口だけ**——関連ディレクトリと
  主要なクラス・関数の名前を数個。変更するファイルの一覧や行番号つきの内部構造は書かない
  （理由は [implementation-prompt.md](implementation-prompt.md) の §0）。

## 3. タスクを設計する

- **依存はタスクの `blockedBy` で表す。** 前のタスクの成果を前提にする作業は依存を張る。
  依存が無い作業は並列に走らせる。
- **同じ中核モジュールを構造から書き換えるタスクは直列にする**（[design-notes.md](design-notes.md)）。
- **達成状況を検分するタスク・一覧表を書くタスクは、対象タスクに `blockedBy` を張る。**
- **1 タスクの大きさ**: 1 サブリーダーで完結し、合否が一意に判定できる大きさ。機能単位で割る。
- **`tier` を付ける**:
  - `light`: docs の追随、生成物の機械的な更新、中核ロジックに触れない数ファイルの変更。
    **複数の light を 1 サブリーダーに束ね、1 ブランチ・1 PR にする。** レビューは `sonnet`。
  - `standard`（既定）: ロジック・中核・挙動に関わる変更。レビューは `opus`。
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

## 4. topic を作る

1. デフォルトブランチから `topic/<作業名>` を作り、**すぐ push** する（タスク PR の base になる）。
2. `docs/supervisor/<作業名>.brief.md` / `.map.md` / `.ledger.md` を commit して push する。

## 5. 権限を先に通す

権限の確認はリードの画面に出る。最大 15 エージェントが走るので、聞かれる前に許可リストへ入れる。

- `brief.md` に書いた検証コマンド・起動コマンド・プロジェクト固有の MCP
- レビューコメント用スクリプト（スキルの `allowed-tools` はリードにしか効かない）

```
Bash(~/.claude/skills/team-supervisor/scripts/gh-review.py *)
```

## 6. タスクを登録する

各タスクを `TaskCreate` で登録する。

- `subject`: `[task<番号>] <件名>`
- `description`: DoD・受け入れ基準・スコープ境界・調査の入口・隣接タスクとの契約
- `metadata`: `{ "tier": "standard", "branch": "topic/<作業名>--task-<番号>", "approved": false }`

依存は `TaskUpdate` の `addBlockedBy` で張る。

状態ディレクトリを作り直す。前回の実行が残したカウンタ・目印・登録を消す
（[hooks.md](hooks.md)）。

```bash
sd="$(git rev-parse --git-common-dir)/team-supervisor"
rm -rf "$sd" && mkdir -p "$sd"   # 登録の前に 1 回
```

## 7. 回す

**同時に走らせるサブリーダーは 3 体まで。** 枠が空いたら、`blockedBy` が解けているタスクのうち
番号が小さいものから spawn する。

### spawn する

```
Agent(name: "task4", model: "opus", isolation: "worktree", prompt: <契約>)
```

- `isolation: "worktree"` を落とさない。
- `run_in_background` は指定しない（既定の背景で走る）。完了は通知で届く。
- `prompt` は [subleader-prompt.md](subleader-prompt.md) の契約を組み立てて渡す。

返り値の `agentId` を台帳に控え（[ledger.md](ledger.md)）、フックへ登録する。worktree は
`<リポジトリ>/.claude/worktrees/agent-<agentId>` にある。

```bash
sd="$(git rev-parse --git-common-dir)/team-supervisor"
printf '%s' "topic/<作業名>--task-<番号>" > "$sd/branch-<agentId>"
```

### 承認報告を受けたら

サブリーダーは 1 行で報告する（`task4 approved / branch=... / pr=#123 / must 0 / should 2`）。
受けたら [integration.md](integration.md) の手順で、実物を確かめてから topic へ取り込む。
取り込んだら台帳を更新して commit・push し、次で再開カウンタを消してから、空いた枠に次の
タスクを spawn する。

```bash
rm -f "$(git rev-parse --git-common-dir)/team-supervisor/resume-count-task4"
```

### 質問・blocked を受けたら

- **自分で答えられるなら答えて続けさせる**（タスク設計の意図・ベース資料・他タスクとの整合）。
- **ユーザーに上げるのは次の 4 つだけ**: 作業範囲の解釈が割れる / 規模が当初想定から大きく
  増減する / 後戻りしにくい設計上の取引が要る / ユーザーの指示が既存の DoD や設計文書と矛盾する。
- **確認を待つ間も走行中のサブリーダーは完走させ、承認と統合は進める。** 答え次第で無駄に
  なりそうなタスクだけ、新しく spawn するのを止める。

### 完了通知を受けたら

**承認報告の形（`approved / branch=... / pr=#...`）でなければ、途中で止まったと見なす。**
API エラーで落ちた場合は通知にエラー本文が載る。いずれも
`git ls-remote origin refs/heads/<そのタスクのブランチ>` でブランチの実在を確かめる。
**全タスクが `completed` になるまで最終 PR に進まない。**

### 止まったサブリーダーを再開する（立て直さない）

`SendMessage` はトランスクリプト全件を復元して再開させる。**待たずにすぐ送る。上限は 3 回。**
**再開先はメインの作業ツリーになるので、作業ツリーを直す指示を必ず添える。**

```
SendMessage(to: "task4", message:
  "作業を再開してください。まず subleader-prompt.md §1-b の手順で自分の作業ツリーを
   確かめ、メインツリーにいたら EnterWorktree で worktree を作り直してから続けてください。
   中断した地点から続け、最初からやり直さないでください。")
```

```bash
sd="$(git rev-parse --git-common-dir)/team-supervisor"
n=$(cat "$sd/resume-count-task4" 2>/dev/null || echo 0); n=$((n + 1))
printf '%s' "$n" > "$sd/resume-count-task4"
```

4 回目に入るときは打ち切る。台帳でそのタスクを `blocked` にしてエラー本文を写し、次を実行して
ユーザーへ上げる（[hooks.md](hooks.md)）。**他のタスクは止めずに進める。**

```bash
touch "$sd/blocked-<agentId>"
```

利用制限で止まったときはリードも同時に止まるので、この手順は実行できない。リードが再び
動けるようになってから再開する。

## 8. 仕上げる

1. `TaskList` で全件 `completed` を確認する。
2. topic を最新化し、`brief.md` の検証コマンド一式と外形動作をフルで 1 回流す。
3. 台帳を最終版に更新して commit・push する。
4. topic → デフォルトブランチの PR を作る。本文には構成 PR の一覧・検証結果・
   「自律判断の記録」を書く（下記）。**マージはしない**（ユーザーが行う）。
5. 何がマージされたか・失敗で残ったタスク・自分の判断で変えた目標・先送りにした作業・
   残課題をユーザーにまとめる。

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
- **リードがコンフリクトを解いてコードを書いた場合もここに書く**（リードがコードに触れる
  唯一の場面。[integration.md](integration.md) §3）

## 10. 運用上の注意

- ユーザーの指示（削除・仕様変更）が既存の DoD や設計文書と矛盾するときは、黙って従わず
  **事実と帰結を先に示す**（例: 消す対象が別の成果物を兼ねている、検査が任意に格下げになる）。
  示したうえで判断が明らかなら、妥当な解釈で進めてよい。
- サブリーダーの `blocked` 報告・失敗報告を鵜呑みにしない。`git show` やテストの実行で
  確かめてから裁く。
- 進捗の報告は「どのタスク・どの PR がどの状態か」を軸に短くまとめる。
