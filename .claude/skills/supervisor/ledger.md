# 台帳の書式と、セッションが落ちたときの再開手順

## 台帳と stack PR 本文は `state.py` が書き出す

**台帳（`<ベース>/ledger.md`）を手で書かない。** 状態は `<ベース>/state.json` に持ち、
`<スクリプト>/state.py render` が台帳と stack PR 本文（`<ベース>/stack-pr-body.md`）の
**両方を同時に**書き出す。同じ表を 2 か所へ書き写す作業と、片方だけ古くなる事故が無くなる。

| 何を | どう | いつ |
| --- | --- | --- |
| 全体の値（作業名・stacked PR の土台・trunk・stack PR 番号・セッション ID） | `state.py init` / `state.py meta` | [lead-setup.md](lead-setup.md) §4 の頭と、stack PR を作った直後 |
| タスクの分解 | `state.py add-task`（1 タスク 1 回） | [lead-setup.md](lead-setup.md) §4 |
| 状態・PR 番号・runId・起点・却下件数・判断 | `state.py set` | ワークフローを起動した直後、stacked PR へ積むたび、打ち切ったとき |
| 散文の節（概要・計画・挙動の変化・確認項目・検証結果・自律判断・残課題） | `<ベース>/prose/<名前>.md` を `Write` で書く | 中身が変わったときだけ |
| 台帳と stack PR 本文の生成 | `state.py render`（仕上げは `--final`） | 上のどれかを変えた直後 |
| タスク PR 本文の生成（stacked PR の案内を先頭に差す） | `state.py task-body --task <番号>` | タスク PR の本文を差し替える直前（[integration.md](integration.md) §2 の手順 1） |

散文を 1 節 1 ファイルに分けてあるのは、**変わった節だけを書き直せるようにする**ためである。
本文全体を毎回組み立て直すと、節を落とす事故が起きるうえ、1 本積むごとに書式を読み直すことになる。

```bash
<スクリプト>/state.py set --base <ベース> --task 1 --status stacked --pr 101 --rejected 2
<スクリプト>/state.py render --base <ベース>
gh pr edit <stackPR番号> --body-file <ベース>/stack-pr-body.md
```

**`--status stacked` を入れた順が stacked PR の並びになる**（`stack_order` に足され、`stacked_on` に
1 つ下のブランチが入る）。実物の並びは `stack.py show --tree <スタックツリー>` が
`gh stack view --json` から読むので、食い違ったらそちらが正しい。

`prose/<名前>.md` の名前と、それがどの節になるかは `state.py` の `PROSE` にある
（`prelude` はリポジトリの PR テンプレートの見出しを先頭に残すための枠）。

## 4 か所に状態を持つ

| どこ | 何を持つ | 誰が書く |
| --- | --- | --- |
| 組込タスクリスト（`~/.claude/tasks/<セッション ID>/`。既定ではセッション ID がそのままディレクトリ名になる） | 実行中の真実。状態・依存・metadata | **リードだけ**が `TaskCreate` / `TaskUpdate` で |
| `<ベース>/state.json` と `<ベース>/prose/*.md` | 節目の記録の**出所**。タスク分解・進行状態・自律判断・散文 | **リードだけ**が `state.py` と `Write` で |
| `<ベース>/ledger.md` | 上の 2 つから `state.py render` が書き出した台帳。**手で直さない** | `state.py render` |
| `<ベース>/notes/task<番号>/` | 引き継ぎノートと **review.json**（レビューの往復） | ワークフローの各エージェント |
| stack PR の本文（[stack-pr.md](stack-pr.md)） | ユーザーに見せる記録。`state.py render` が書いた `stack-pr-body.md` を載せたもの | リードだけが `gh pr edit --body-file` で |
| `.git/worktrees/supervisor-<作業名>/gh-stack` | stacked PR の並びの実物。`gh stack` が持つ | `gh stack`（`stack.py` 経由） |

`<ベース>` は次で受け取る絶対パス。スタックツリー（`.claude/worktrees/supervisor-<作業名>`）の中の
`.claude/supervisor/` で、自分を無視する `.gitignore` が入っているので **git の追跡対象に入らず、
PR の差分にも出ない**。**スタックツリーを外すと一緒に消える**（`git worktree remove` は無視された
ファイルを拒まずに消す。実測）。`gh stack` の追跡情報も同じときに消えるので、
続きを頼まれたときは `gh stack checkout <stackPR番号>` か `stack.py init` から作り直す。

```bash
<スクリプト>/place.py base-dir --work <作業名>
```

**ワークフローのエージェントはタスクリストに触れない。** 状態はリードが返り値を受けて更新する。

台帳はリポジトリの履歴に残らない。**ユーザーが後から読める記録は stack PR 本文だけ**なので、
タスク一覧と自律判断は PR 本文へ載せる（[finish.md](finish.md) §8・§9）。タスクリストのディレクトリ名は
セッション ID から決まるので、**新しいセッションを立てるとタスクリストは引き継げない**
（`claude --resume <元のセッション ID>` で開き直せば、`--fork-session` を付けない限りセッション
ID が再利用されるので残る）。したがって再開の足場は台帳が持つ。**台帳も失われていたら
stack PR の本文から書き直す**（下の再開手順 2。ただし `runId` は本文に無い）。

## 台帳を書くタイミング

どの場面で何を `state.py` に入れ、`render` の結果を PR に載せるかどうかの対応である。

| # | 場面 | 叩くもの | stack PR 本文 |
| --- | --- | --- | --- |
| 1 | タスク設計の直後 | `init` → `add-task` を全タスク分 → `render` | §4 で**この PR を作る**（本文の初版）。番号が返ったら `meta --stack-pr` |
| 2 | ワークフローを起動した直後 | `set --status running --run-id <runId> --parent <起動時の stacked PR の先頭>` | 更新しない（`runId` は本文に載らない） |
| 3 | 1 タスクを stacked PR へ積んだ直後 | `set --status stacked --pr … --rejected … --reviews … --decision … --deferral …` → `render` | 差し替える（[integration.md](integration.md) §2 の手順 4・5） |
| 4 | タスクを `blocked` / `failed` で打ち切ったとき | `set --status blocked --reason …` → `render` | 差し替える（`render` が「残課題」に理由を足す） |
| 5 | §8 の仕上げ | 散文の節を書いてから `render --final` | 最終版に差し替え、`stacked` が 1 件以上なら `gh pr ready` |

**`runId` は台帳にだけ載る。** 後始末（`worktree.py remove --run`）と同一セッション内の再実行
（`resumeFromRunId`）に要る内部の値で、ユーザーが読む意味が無いので `render` は PR 本文の表から
外す。

## 書き出される台帳の書式

**この形は `state.py render` が作る。** 手で書かないが、何が載るかを知っておく。

```markdown
# <作業名> 台帳

- lead-session: 80b6dda0-1385-4290-bab1-c1e65a800a92   ← claude --resume に渡す値（/status で確かめる）
- stacked PR の土台: stack/<作業名>--task-0
- stack-pr: 100                                        ← 計画と進行状況を書いている draft PR
- trunk（stacked PR の土台が向く base）: main
- stacked PR の並び（下から）: stack/x--task-0 ← stack/x--task-2 ← stack/x--task-1
- default-branch: main
- created: 2026-08-11
- ベース資料: 同じディレクトリの brief.md / map.md
- 引き継ぎノート: 同じディレクトリの notes/task<番号>/

## 全体のゴールと DoD

<この作業全体で達成する状態>

## タスク一覧

| # | 件名 | tier | 依存 | ブランチ | 起点 | 積んだ位置 | PR | runId | 状態 | 却下した残件 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | パーサの境界値を直す | standard | — | stack/x--task-1 | stack/x--task-0 | 2 | #101 | wf_a1b2c3d4e5f6 | stacked | 2 |
| 2 | エラー型を整理する | standard | — | stack/x--task-2 | stack/x--task-0 | 1 | #102 | wf_0f1e2d3c4b5a | stacked | 0 |
| 3 | 一覧表を更新する | light | 1,2 | stack/x--task-3 | — | — | — | — | pending | — |

「起点」はそのブランチを切ったときの stacked PR の先頭で、リードが作るタスク PR の base になる。
「積んだ位置」は下（base 側）から数えた位置で、**タスク番号の順とは一致しない**（積むのは決着した順。
この例では task 2 が先に決着している）。

`runId` は `Workflow` の返り値にある値。worktree の後始末（`worktree.py list --run` /
`remove --run`）と、同一セッション内での再実行（`resumeFromRunId`）に使う。

状態は `pending` / `running` / `settled`（決着したがまだ stacked PR へ積んでいない）/
`stacked`（stacked PR へ積んでレビュー待ち）/ `merged`（ユーザーがマージした）/ `blocked` / `failed`。
**PR 番号は `stacked` になる時点で付く。** タスク PR を作るのはリードで、全レビューが決着した
後の [integration.md](integration.md) §2 である（`settled` までは PR が無い）。

## タスクの詳細

### task 1: パーサの境界値を直す

- **DoD**: 空入力でも panic せず、`Err(EmptyInput)` を返す状態
- **受け入れ基準と検証**: `cargo test parser::` が通る。`cargo clippy -- -D warnings` が通る。
  `echo "" | ./target/debug/app` が終了コード 1 と `empty input` を返す
- **スコープ境界**: 触ってよいのは `src/parser/`。`src/core/` には触らない
- **調査の入口**: `src/parser/`、`Parser::parse`、`ParseError`
- **隣接タスクとの契約**: task 2 が `ParseError` に variant を足すので、既存 variant は消さない
- **レビュー**: closed 5 件 / rejected 3 件。うち 2 件は「妥当だがこのタスクでは直さない」と
  裁定した残件（review.json のコメントに理由が残っている）
- **decisions**: none
- **deferrals**: `Err` の詳細メッセージの整形は task 2 に寄せた（重複を避けるため）

## 自律判断の記録

### 変更した最終目標・DoD・スコープ

- <何を・なぜ・判断材料（根拠と退けた代替案）・残課題>

### 先送り・対象外にした作業

- <何を・なぜ・次に何をすべきか>
```

## 自律判断をどこに書くか

[finish.md](finish.md) §9 で残すと決めた判断の書き先である。

| 判断の範囲 | 書き先 |
| --- | --- |
| 個別タスクの中で閉じる判断 | そのタスクの PR 本文（PR 本文エージェントが `decisions` と引き継ぎノートから書く） |
| ワークフローが返した `decisions` / `deferrals` | `state.py set --task <番号> --decision "…" --deferral "…"`。`render` が台帳と PR 本文の両方に `task<番号>: <中身>` の形で載せる |
| リードが自分で下した、作業全体に関わる判断 | `<ベース>/prose/decisions.md` と `<ベース>/prose/deferrals.md` に `Write` で書く |

見出しは 2 つで、台帳と stack PR 本文に同じ名前で出る。

- `### 変更した最終目標・DoD・スコープ`（`prose/decisions.md` ＋ 各タスクの `decisions`）
- `### 先送り・対象外にした作業`（`prose/deferrals.md` ＋ 各タスクの `deferrals`）

漏らしてはいけないものが 2 つある。

- **ワークフローが返した `decisions` と `deferrals` を `state.py set` に入れる。** バックグラウンドで
  下された判断なので、入れなければリードの画面にも PR にも残らない。
- **リードがコンフリクトを解いてコードを書いた場合も書く。** リードがコードに触れる唯一の
  場面である（[integration.md](integration.md) §3）。

**台帳は commit されない。** stack PR 本文への転記を省くと、ユーザーが残る形で読める記録が
何も無くなる（[stack-pr.md](stack-pr.md)）。

## セッションが落ちたときの再開手順

`/resume` できるかどうかに関わらず、同じ手順で再開できる。**開き直せるなら
`claude --resume <台帳の lead-session の値>` を使う**（`--fork-session` を付けない）。
セッション ID が再利用されるのでタスクリストが残り、手順 7 が要らなくなる。

1. **スタックツリーを取り戻す。** 積み替えの git と `gh stack` を動かす専用の worktree で、
   stacked PR の先頭が載っている。**台帳・引き継ぎノート・`gh stack` の追跡情報もこの中にあるので、
   残っているかどうかで手順 2 の結果が決まる。** 以降 `<スタックツリー>` はこの絶対パス。
   **ユーザーの作業ツリーでブランチを切り替えない。**

   ```bash
   git worktree list | grep 'supervisor-<作業名>'    # 残っていればそれを使う
   # 無ければ作り直す（stacked PR のブランチは origin にある）
   git fetch origin
   git worktree add .claude/worktrees/supervisor-<作業名> 'stack/<作業名>--task-0'
   ```

   **作り直したときは stacked PR の追跡情報も無い。** `gh stack checkout <stackPR番号>` で GitHub 上の
   Stack を引き寄せる（remote の stacked PR をローカルへ組み直す）。Stack が無い・壊れているときは
   `stack.py init` から作り直し、積み終わっているブランチを
   `stack.py append --branch <ブランチ>` で下から順に入れ直す。

2. **状態を読む。** 置き場を受け取り、`state.py show` で全タスクの DoD・tier・依存・状態・
   PR 番号・`runId` を 1 回で得る（台帳の Markdown ではなく、出所の JSON を読む）。

   ```bash
   <スクリプト>/place.py base-dir --work <作業名> --require
   <スクリプト>/state.py show --base <ベース>
   ```

   **`place.py` の終了コードが 1 なら、この足場は失われている**（スタックツリーを外した、クローンを
   作り直した、[finish.md](finish.md) §8 の後始末を済ませていた場合）。そのときは **stack PR の本文から
   state.json を組み直す**（`state.py init` → `add-task` → `set`）——全体の計画と DoD・タスク一覧
   （件名・tier・依存・PR 番号・状態・積んだ位置）がそこに残っている
   （[stack-pr.md](stack-pr.md)）。

   ```bash
   gh pr list --state all --search 'head:stack/' --json number,title,headRefName
   gh pr view <stackPR番号> --json body -q .body
   ```

   **`runId` は本文に無い**ので、`resumeFromRunId` と `worktree.py list --run` は使えない。
   残った worktree は `git worktree list` から名指しで拾う（手順 6）。作業名が分からなければ
   `git branch -r | grep '^  origin/stack/'` でブランチ名から拾う。本文と実物が食い違ったら
   手順 3 のとおり **stacked PR の実物を信じる**（本文は積んだ後に更新するので、落ちた場所によっては
   1 タスク分古い）。
3. `stack.py show --tree <スタックツリー>` で、state.json の `stacked` と実際の stacked PR が一致するかを
   突き合わせる。**state.json より stacked PR の実物を信じる**（更新前に落ちた可能性がある）。
   食い違っていたら `state.py set` で直す。`needs_rebase` が残っていれば
   `stack.py append --continue` で積み替えを終わらせる。
4. `git branch -r | grep 'stack/<作業名>--task-'` で、走行中だったタスクの途中成果を確かめる。
   push 済みのコミットはワークフローが死んでも残っている。
   **PR はあるとは限らない**（作るのはリードで、決着してから積むときである）。
   `gh pr list --search 'head:stack/<作業名>--task-' --state all --json number,headRefName` で
   ブランチごとに探し、無ければ [integration.md](integration.md) §2 の手順 1 から進める。
5. **引き継ぎノートを読む**（`<ベース>/notes/task<番号>/`）。どこまで調べ、何を検証し、何が
   残っているかが役割ごとに残っている。立て直すワークフローはこれを読んで続きから始める。
6. `git worktree list` に残留があれば、[integration.md](integration.md) §2 手順 2 のやり方で
   未コミットの成果を保全してから消す。**旧セッションの `runId` は state.json にある。**
   **stacked PR へ積む前に消す**——ブランチを握った worktree があると `gh stack add` が落ちる。
7. 未完のタスクを `TaskCreate` で登録し直し（新しいセッションでは新しいタスクリストになる）、
   `state.py meta --lead-session <新しいセッション ID>` で書き換えて `render` する。
8. 決着済み（`settled`）でまだ積んでいないブランチがあれば、先に
   [integration.md](integration.md) の手順で stacked PR へ積む。
9. 空き枠に、未完タスクのワークフローを `resumeFrom` 付きで起動して続きを回す。

## stacked PR の追跡情報が壊れたとき

`gh stack` の追跡情報（`.git/worktrees/supervisor-<作業名>/gh-stack`）が実物と合わなくなると、
`stack.py show` が古い位置を返し、`stack.py append` が「既に stacked PR に入っている」と言いながら
PR の base が張り替わらない、といった食い違いが出る。**ブランチと PR は壊れていない**ので、
追跡情報だけを直す。**多いのは、別の場所で `gh stack sync` を通した後である。**

1. `stack.py show --tree <スタックツリー>` の `stale_record` を読む。記録された位置・ローカル ref・
   `origin/<ブランチ>` の 3 つが並ぶので、どれが取り残されているかが分かる。
2. **ローカル ref が origin と違うなら、先に揃える。** checkout されていないブランチは
   `git branch -f <ブランチ> origin/<ブランチ>` で揃う（upstream も付く）。**別の worktree が
   checkout しているブランチは動かさない**——`git branch -f` は git のバージョンによって通ってしまい、
   その worktree の HEAD だけを飛ばす（design-notes.md「なぜデフォルトブランチのポインタを force で
   戻さないか」）。そのワークツリーの中で `git reset --hard origin/<ブランチ>` を叩くのはユーザーである。
3. **`gh-stack`（JSON、`schemaVersion: 1`）の `head` / `base` を実物に書き換える。** trunk から順に、
   各ブランチの `head` を `origin/<ブランチ>` の SHA、`base` を 1 つ下のブランチの新しい SHA にする。
   書き換えたら `stack.py show` で `stale_record` が空になり、PR が OPEN のまま並びが戻ることを確かめる。
   **force push も `gh stack link` も要らない**（実測）。
4. それでも直らないときだけ作り直す。ローカルの追跡を捨て（**GitHub 上の Stack と PR は残る**）、
   土台から積み直す。**`gh stack add` の force push と `link` の張り直しを伴うので、決着済みの PR に
   対しては最後の手段である。**

   ```bash
   cd <スタックツリー> && gh stack unstack --local
   ```

   `stack.py init` で土台を据え直し、積み終わっているブランチを台帳の「stacked PR」の順に
   `stack.py append --branch <ブランチ>` で入れ直す。GitHub 側の並びが直らないときは
   `gh stack unstack <stack 番号>` で GitHub 上の Stack も外してから通し直す
   （`gh stack link` が並びを作り直す）。**`gh stack modify` は対話 TUI なので使わない。**

## 落ちたワークフローの扱い

**ワークフローのエージェントは再開できない**（`SendMessage` の宛先にならず、`agent()` に再開の
引数も無い。design-notes.md「ワークフローのエージェントは再開できない」）。立て直すときは**やり直しではなく続きから
始める**。足場は次の 3 つである。

| 残るもの | 何に使うか |
| --- | --- |
| push 済みのコミット | `resumeFrom.sha`。「ここまで実装済み」を実装に伝える |
| review.json | open が残っていれば、初回レビューではなく修正ラウンドから始める |
| 引き継ぎノート | 各役割が読んだ箇所・検証結果。再探索を省く |

順に試す。

1. **同一セッションの中なら `resumeFromRunId` で再実行する。** 完了済みの `agent()` は
   `journal.jsonl` のキャッシュから返るので安い。**先に走行中の run を `TaskStop` で止める。**

   ```
   Workflow({ scriptPath: "<返り値に入っていたパス>", resumeFromRunId: "<runId>" })
   ```

   スクリプトを 1 行でも変えると、変えた箇所より後ろは全部再実行される。
2. **セッションが落ちた・スクリプトを組み直したときは `resumeFrom` を付けて新しく起動する。**

   ```
   Workflow({ scriptPath: "<スキル>/scripts/task-workflow.js", args: { task, parent, base, work,
     stackPr, skillDir,
     resumeFrom: { branch: "<タスクブランチ>", sha: "<前コミット>",
                   transcriptDir: "<完了通知に入っていたパス>" } }})
   ```

   **`parent` は起点にしたブランチをそのまま渡す**（`state.py show` の `parent`）。積み替えは
   決着した後にリードが行うので、立て直しで起点を変えない。

   `sha` は `git log origin/<タスクブランチ> -1 --format=%H` で取る。
   `transcriptDir` も渡しておくと、必要なら前ランの `agent-*.jsonl` を読ませられる
   （引き継ぎノートの方が短いので、まずそちらを読ませる）。
3. **push も未コミットの変更も無ければ成果はゼロ**なので、`resumeFrom` を付けずに起動し直す。
4. **立て直しても決着に至らないタスクは打ち切る。** 台帳でそのタスクを `blocked` にし、
   返り値の `reason` と review.json の open を添えてユーザーへ上げる。**他のタスクは止めずに進める。**

利用制限で止まった場合、リードも同時に止まる。リードが再び動けるようになった時点で 1 から始める。
