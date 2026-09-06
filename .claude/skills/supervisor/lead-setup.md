# 立ち上げ（起動前の確認と §1〜§6）

**リードが最初に 1 回だけ読む節である。** `SKILL.md` の全体フローのうち、ループに入る前の段を
ここに置いてある。ループ（§7）は `SKILL.md` にあり、仕上げ（§8・§9）は
[finish.md](finish.md) にある。

`<スキル>` `<スクリプト>` `<ベース>` `<スタックツリー>` `<作業名>` `<base>` は `SKILL.md` の用語と
同じものを指す。

## 起動前の確認

1. git リポジトリであること、`gh auth status` が通ることを確認する。
   **続けて `gh stack`（GitHub 公式の拡張 github/gh-stack）が入っていることを確かめる。**
   stacked PR の組み立てをこの拡張に任せているので、無いと 1 本も積めない。

   ```bash
   gh extension list | grep github/gh-stack   # 0 なら入っている
   ```

   入っていなければ**ユーザーに `./install.sh` の実行を頼む**（`install_gh_extensions()` が
   `gh extension install github/gh-stack` を叩く）。急ぐときは同じコマンドを直接叩いてよい。
   入るまで §1 に進まない。
2. **スキルの置き場を確定する。** 以降 `<スキル>` と `<スクリプト>` はこの絶対パスを指す。

   ```
   <スキル>     = ${CLAUDE_SKILL_DIR}
   <スクリプト> = ${CLAUDE_SKILL_DIR}/scripts
   ```

   この行はスキルの読み込み時に絶対パスへ展開されている。**自分で組み立てず、展開された値を
   そのまま使う。** `<スキル>` はワークフロー起動時に `args.skillDir` として渡す——
   サブエージェントは `${CLAUDE_SKILL_DIR}` を持たず、契約ファイルをこのパスから読む
   （[workflow-script.md](workflow-script.md)「契約はどうエージェントに届くか」）。
3. `scripts/review.py`・`scripts/place.py`・`scripts/verify.py`・`scripts/worktree.py`・
   `scripts/stack.py`・`scripts/state.py` の全スクリプトに実行ビットがあることを確かめ
   （`ls -l <スクリプト>`）、欠けていれば `chmod +x` する
   （`scripts/task-workflow.js` は `Workflow` ツールが読むので実行ビットは要らない）。
4. **自分がどのモデルと effort で動いているかをユーザーに申告する。** standard の実装・レビュー・
   裁定・再計画・PR 本文は、model と effort を指定せずにこのセッションの設定を継承する
   （[workflow-script.md](workflow-script.md)「役割ごとのモデルと effort」）。つまり今のモデルと
   effort がそのまま全エージェントの既定になる。変えたいならタスク設計に入る前に `/model` で
   切り替える。
5. **分岐元（＝ stacked PR の土台が向く base。`gh stack` の trunk）を確定する。** 候補は
   デフォルトブランチと、過去の PR が実際にマージ先にしているブランチである（ブランチ名の
   規則を決め打ちしない。リポジトリごとに違う）。候補が 2 つ以上あればどこから切るかを
   ユーザーに尋ね、1 つならそれで確定して尋ねない。以降 `<base>` はこのブランチ名を指す。

   ```bash
   gh repo view --json defaultBranchRef -q .defaultBranchRef.name
   gh pr list --state all --limit 50 --json baseRefName -q '.[].baseRefName' | sort | uniq -c | sort -rn
   ```

6. **作業名を決め、Issue 番号やチケット ID の有無をユーザーに尋ねる**（推測しない）。
   **作業名に含めない**——stack PR のタイトル末尾にだけ添える
   （[stack-pr.md](stack-pr.md)「作る」）。以降 `<作業名>` は 1 つの値を指し、stacked PR のブランチ
   （`stack/<作業名>--task-<番号>`）・スタックツリー（`.claude/worktrees/supervisor-<作業名>`）・
   `place.py --work` のすべてに同じ値を渡す。ここを 2 通りに分けると、`place.py base-dir` が
   組み立てるスタックツリーのパスと実際に作ったパスが食い違って `git worktree list` に無いと
   言われ、§2 で止まる。
7. **その作業名がまだ使われていないことを確かめる。** stacked PR のブランチ名とスタックツリーのパスが
   作業名から決まるので、既にあるものと重なると取り違える。

   ```bash
   git ls-remote --exit-code --heads origin 'stack/<作業名>--task-0'   # 0 なら既にある
   git worktree list                                                  # supervisor-<作業名> があるか
   ```

   どちらかが当たったら、**そのまま進めずユーザーに確認する**（別の作業の途中かもしれない）。

**ユーザーの作業ツリーの状態は問わない**（カレントブランチも未コミットの変更も）。リードは
§1 で作るスタックツリーの中だけで動く。

## 1. stacked PR の土台とスタックツリーを作る

「起動前の確認」で決めた base ブランチから **`stack/<作業名>--task-0`（stacked PR の土台）** を作り、
**空コミット 1 つを載せてすぐ push** する（タスク PR の base になる。空コミットが要る理由は
design-notes.md「なぜ最初に draft の stack PR を作るか」）。続けて**スタックツリーを
作り、セッションをその中へ移す**。以降 `<スタックツリー>` はこの絶対パスを指す。

```bash
git fetch origin
git branch --no-track 'stack/<作業名>--task-0' origin/<base>
git worktree add .claude/worktrees/supervisor-<作業名> 'stack/<作業名>--task-0'
git -C .claude/worktrees/supervisor-<作業名> commit --allow-empty \
  -m "chore: supervisor の stacked PR の土台を作る"
git -C .claude/worktrees/supervisor-<作業名> push -u origin 'stack/<作業名>--task-0'
```

コミットメッセージの書式は `git log` から読める既存の書式に倣う。

**ここの `--no-track` は残す。** 土台は `origin/<base>` から切るので、tracking を git に任せると
upstream が**base ブランチ**を向く。直後の `push -u` が `origin/stack/<作業名>--task-0` に
張り替えるので実害は無いが、誤った upstream を一瞬でも持たせない。**タスクブランチ側は逆で、
`stack.py append` が `origin/<同じ名前>` から作るので tracking を付ける**——付けないと、その
ブランチを checkout した人の `git pull` が落ちる（issue #39）。

```
EnterWorktree({ path: ".claude/worktrees/supervisor-<作業名>" })
```

**`git checkout` / `git switch` を使わない**（上のコマンドはユーザーのカレントブランチを動かさない。
空コミットもスタックツリーの中で作るので、ユーザーの作業ツリーに触らない。
理由は design-notes.md「なぜリードに専用の worktree を与えるか」）。

**`EnterWorktree` が失敗したら、タスクを登録せずそこで止める。** 積み替えレーンは
`gh stack rebase` と force push を繰り返すので、スタックツリーに入らずに進めるとユーザーの
作業ツリーを巻き込む
（design-notes.md「なぜ `EnterWorktree` を必須にし、それでも `-C` を書くか」）。

続けて**stacked PR の追跡情報をこのスタックツリーに作る。**

```bash
<スクリプト>/stack.py init --tree <スタックツリー> --trunk <base> --bottom 'stack/<作業名>--task-0'
```

**この 1 回で場所が決まる。** `gh stack` の追跡情報は worktree ごとに別なので、別の
ディレクトリで叩いた `gh stack` はこの stacked PR を見つけられない（実測は design-notes.md
「gh stack v0.1.0 で確かめたこと」）。以降、stacked PR に触るのは `stack.py`（`--tree` に
このパス）だけである。

スタックツリーのディレクトリが `git status` に未追跡で出るリポジトリでは、**`.gitignore` に
`/.claude/worktrees/` を足すことをユーザーに提案する**（勝手にコミットしない）。

## 2. 前提を集める

`<ベース>` を 1 行受け取る（無ければ作られる）。

```bash
<スクリプト>/place.py base-dir --work <作業名>
```

次を特定して `<ベース>/brief.md` に書く。全サブエージェントがこれを読む。

- **検証コマンド一式**: `.github/workflows/` などの CI 定義・CLAUDE.md・docs から、
  「マージしてよい」と言える全チェック（テスト・lint・フォーマット・ビルド）を列挙する。
  **stacked PR の先頭で 1 回流すコマンド一式**として書く（積んだ直後にここを流す。
  [integration.md](integration.md) §4）。
- **外形動作を確かめる手順**: アプリや CLI を実際に起動して動きを見る手順（`/run` や `/verify`
  スキル、起動コマンド）。レビューとリードは実装の報告を信じず自分で動かす。
- **不可侵パス**: 触ってはならないパス、専用の手順が要るパス。
- **ブランチとコミットの規約**: デフォルトブランチ名、ブランチ命名、コミット署名、PR テンプレート。
  **PR タイトルを検査する job があるかどうかも書く**（`^(feat|fix|docs|…): ` の形で先頭を見るもの。
  あるときの書き方は [stack-pr.md](stack-pr.md)「タイトルの接頭辞」）。

## 3. 調査する

- コードベースの現状は **Explore エージェント**（`model` は指定せずリードを継承、"very thorough"）に
  調べさせる。
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
  - `standard`（既定）: ロジック・中核・挙動に関わる変更。1 巡目は通常＋敵対的の 2 本立て。
    **迷ったら standard にする。**
- **DoD に「足す」と書くなら、その対象が実物に無いことを確かめる。** 試走で、既に
  `.claude/skills/editing-skills/SKILL.md` にある記述を「足す」と書いたタスクを出してしまい、
  実装が差分を作れずに理由を報告して返した実績がある。**`grep` 1 回で済む**——タスクを設計する
  前に、足す対象の語がそのファイルに無いことを見る。
- **各タスクに次を書く**:
  - DoD: 達成すべき状態で書く。「このファイルのこの行をこう変える」という手順にしない。
  - 受け入れ基準と検証: 合否を判定する観点と、実際に叩くコマンド。
  - スコープ境界: やること / やらないこと、触ってよい領域 / 触ってはならない領域。
  - 調査の入口: 関連ディレクトリと主要な名前を数個。
  - 隣接タスクとの契約: 並列に走る他タスクと共有する I/F・前提の一行要約。
  - `tier`。
- **同じファイルを触る 2 タスクを並列にするなら、触ってよい領域を明示する。** worktree は
  ファイルの編集衝突しか防がない。意味の衝突が深いならタスクを直列にする。

### 設計を state.json に入れる

**台帳を手で書かない。** 状態は `<ベース>/state.json` に入れ、`state.py render` が台帳
（`<ベース>/ledger.md`）と stack PR 本文（`<ベース>/stack-pr-body.md`）の両方を書き出す
（[ledger.md](ledger.md)「台帳と stack PR 本文は `state.py` が書き出す」）。

**タスク番号は 1 から振る**（`task-0` は stacked PR の土台で、タスクではない）。

**セッション ID は環境変数から読めない**（スタックツリーに入った後は `echo $CLAUDE_SESSION_ID`
が隔離の検査で拒まれる。design-notes.md「なぜ `EnterWorktree` を必須にし、それでも `-C` を
書くか」）。`ls -lt ~/.claude/tasks/` の最も新しいディレクトリ名がそれである。

```bash
<スクリプト>/state.py init --base <ベース> --work <作業名> \
  --bottom 'stack/<作業名>--task-0' --base-branch <base> --lead-session <セッション ID>
<スクリプト>/state.py add-task --base <ベース> --task 1 --subject "<件名>" \
  --tier standard --branch 'stack/<作業名>--task-1' --deps 2,3 \
  --dod "…" --acceptance "…" --scope "…" --entrypoints "…" --contracts "…"
<スクリプト>/state.py render --base <ベース>
```

散文の 2 節（`<ベース>/prose/summary.md` に概要、`prose/plan.md` に全体の計画と DoD）を
`Write` で書いてから `render` する。

### stack PR を draft で作る

**続けて stack PR（stacked PR の土台 `stack/<作業名>--task-0` の PR）を draft で作る。**
作るのは `create-pr` スキルで、`base` / `head` / `title` / `body-file` / `draft=true` を引数で渡す
（`gh pr create` を直接叩かない。呼び方とタイトルの規約は [stack-pr.md](stack-pr.md)。作る直前に
そこを Read する）。
**作成が非 0 で終わったら 1 回だけ再試行し、それでも失敗したらコマンドとエラー出力を
ユーザーに示して止まる**（§5・§6 に進まない。state.json と土台のブランチは残るので、原因が
解消したらここから続けられる）。
**返ってきた PR 番号を `state.py meta --base <ベース> --stack-pr <番号>` で入れる**
——タスク PR のタイトルに入り（`args.stackPr`）、以降の更新先になる。

## 5. 権限を先に通す

権限の確認はリードの画面に出る。**ワークフロー内のエージェントが権限プロンプトを出すと
ワークフローが止まる**ので、聞かれる前に許可リストへ入れる。

- `<ベース>/brief.md` に書いた検証コマンド・起動コマンド・プロジェクト固有の MCP
- スキル付属のスクリプト（このスキルの `allowed-tools` はリードにしか効かない。ワークフロー内の
  エージェントも同じものを呼ぶ）

**`<スクリプト>` は「起動前の確認」で確定した絶対パスに置き換えて登録する**（`${CLAUDE_SKILL_DIR}`
という文字列のまま登録しない。サブエージェントはこの変数を持たず絶対パスでコマンドを打つので、
変数のままの規則とは一致しない）。

```
Bash(<スクリプト>/review.py *)
Bash(<スクリプト>/place.py *)
Bash(<スクリプト>/verify.py *)
Bash(<スクリプト>/worktree.py *)
Bash(<スクリプト>/stack.py *)
Bash(<スクリプト>/state.py *)
Bash(gh pr create *)
Bash(gh stack *)
Skill(create-pr)
```

`stack.py` と `state.py` はリードだけが呼ぶが、`allowed-tools` に載っていても**別のセッションで
再開したときには効かない**ので、ここで登録しておく。`Skill(create-pr)` も同じ理由で入れる
——リードが stack PR・タスク PR・残件回収 PR を作るときに呼ぶ（[stack-pr.md](stack-pr.md)「作る（§4）」、
[integration.md](integration.md) §2）。

`Bash(gh stack *)` を入れるのは復旧のためである。**通常の経路では `gh stack` を直接叩かない**
——`stack.py` が順番と `--no-trunk` を守る。追跡情報が壊れて `gh stack unstack --local` から
やり直すときだけ直接使う（[ledger.md](ledger.md)「stacked PR の追跡情報が壊れたとき」）。

## 6. タスクを登録する

各タスクを `TaskCreate` で登録する。

- `subject`: `[task<番号>] <件名>`
- `description`: DoD・受け入れ基準・スコープ境界・調査の入口・隣接タスクとの契約
- `metadata`: `{ "tier": "standard", "branch": "stack/<作業名>--task-<番号>", "approved": false }`

依存は `TaskUpdate` の `addBlockedBy` で張る。

登録し終えたら `SKILL.md`「7. 回す」に戻る。
