# 台帳の書式と、セッションが落ちたときの再開手順

## 4 か所に状態を持つ

| どこ | 何を持つ | 誰が書く |
| --- | --- | --- |
| 組込タスクリスト（`~/.claude/tasks/<セッション ID>/`。既定ではセッション ID がそのままディレクトリ名になる） | 実行中の真実。状態・依存・metadata | **リードだけ**が `TaskCreate` / `TaskUpdate` で |
| `<ベース>/ledger.md` | 節目の記録。タスク分解・進行状態・自律判断 | リードだけが書く（commit しない） |
| `<ベース>/notes/task<番号>/` | 引き継ぎノートと **review.json**（レビューの往復） | ワークフローの各エージェント |
| topic PR の本文（[topic-pr.md](topic-pr.md)） | ユーザーに見せる記録。全体の計画と DoD・タスク一覧の状態・残課題 | リードだけが `gh pr edit --body-file` で |

`<ベース>` は次で受け取る絶対パス。統合ツリー（`.claude/worktrees/supervisor-<作業名>`）の中の
`.claude/supervisor/` で、自分を無視する `.gitignore` が入っているので **git の追跡対象に入らず、
PR の差分にも出ない**。**統合ツリーを外すと一緒に消える**（`git worktree remove` は無視された
ファイルを拒まずに消す。実測）。

```bash
<スクリプト>/place.py base-dir --work <作業名>
```

**ワークフローのエージェントはタスクリストに触れない。** 状態はリードが返り値を受けて更新する。

台帳はリポジトリの履歴に残らない。**ユーザーが後から読める記録は topic PR 本文だけ**なので、
タスク一覧と自律判断は PR 本文へ転記する（`SKILL.md` §8・§9）。タスクリストのディレクトリ名は
セッション ID から決まるので、**新しいセッションを立てるとタスクリストは引き継げない**
（`claude --resume <元のセッション ID>` で開き直せば、`--fork-session` を付けない限りセッション
ID が再利用されるので残る）。したがって再開の足場は台帳が持つ。**台帳も失われていたら
topic PR の本文から書き直す**（下の再開手順 2。ただし `runId` は本文に無い）。

## 台帳を書くタイミング

台帳と topic PR 本文（[topic-pr.md](topic-pr.md)）の更新は、次のように対応する。

| # | 場面 | 台帳に書くこと | topic PR 本文 |
| --- | --- | --- | --- |
| 1 | タスク設計の直後（v0） | 分解の全体。これが無いと分解そのものが失われる | §4 で**この PR を作る**（本文の初版） |
| 2 | ワークフローを起動した直後 | `runId`。後始末（`worktree.py remove --run`）と同一セッション内の再実行（`resumeFromRunId`）に要る | 更新しない（`runId` は載せない） |
| 3 | 1 タスクを topic へ取り込むたび | 状態を `merged` にし、PR 番号・`rejected` の件数・`decisions`・`deferrals` を写す | 差し替える（[integration.md](integration.md) §5） |
| 4 | タスクを `blocked` / `failed` で打ち切ったとき | 状態と `reason` | 差し替える（状態と「残課題」） |
| 5 | §8 の仕上げ | 最終版に更新する | 最終版に差し替え、`merged` が 1 件以上なら `gh pr ready` |

## 書式

```markdown
# <作業名> 台帳

- lead-session: 80b6dda0-1385-4290-bab1-c1e65a800a92   ← claude --resume に渡す値（/status で確かめる）
- topic: topic/<作業名>
- topic-pr: 100                                        ← 計画と進行状況を書いている draft PR
- base: main                                           ← topic を切った先。topic PR の base
- default-branch: main
- created: 2026-08-11
- ベース資料: 同じディレクトリの brief.md / map.md
- 引き継ぎノート: 同じディレクトリの notes/task<番号>/

## 全体のゴールと DoD

<この作業全体で達成する状態>

## タスク一覧

| # | 件名 | tier | 依存 | ブランチ | PR | runId | 状態 | 却下した残件 |
|---|---|---|---|---|---|---|---|---|
| 1 | パーサの境界値を直す | standard | — | topic/x--task-1 | #101 | wf_a1b2c3d4e5f6 | merged | 2 |
| 2 | エラー型を整理する | standard | — | topic/x--task-2 | #102 | wf_0f1e2d3c4b5a | merged | 0 |
| 3 | 一覧表を更新する | light | 1,2 | topic/x--task-3 | — | — | pending | — |

`runId` は `Workflow` の返り値にある値。worktree の後始末（`worktree.py list --run` /
`remove --run`）と、同一セッション内での再実行（`resumeFromRunId`）に使う。

状態は `pending` / `running` / `settled`（決着したがまだ取り込んでいない）/ `merged` / `blocked` /
`failed`。**PR 番号は取り込みの直前に付く**（PR を作るのはレビューが全件決着した後なので、
`settled` までは空欄である）。

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

## セッションが落ちたときの再開手順

`/resume` できるかどうかに関わらず、同じ手順で再開できる。**開き直せるなら
`claude --resume <台帳の lead-session の値>` を使う**（`--fork-session` を付けない）。
セッション ID が再利用されるのでタスクリストが残り、手順 7 が要らなくなる。

1. **統合ツリーを取り戻す。** 統合レーンの git を動かす専用の worktree で、`topic/<作業名>` が
   載っている。**台帳と引き継ぎノートもこの中にあるので、残っているかどうかで手順 2 の結果が
   決まる。** 以降 `<統合ツリー>` はこの絶対パス。**ユーザーの作業ツリーでブランチを
   切り替えない。**

   ```bash
   git worktree list | grep 'supervisor-<作業名>'    # 残っていればそれを使う
   # 無ければ作り直す（topic ブランチは origin にある）
   git fetch origin
   git worktree add .claude/worktrees/supervisor-<作業名> topic/<作業名>
   git -C .claude/worktrees/supervisor-<作業名> pull --ff-only
   ```

2. 台帳を読む。全タスクの DoD・tier・依存・状態が分かる。置き場は次で受け取る。

   ```bash
   <スクリプト>/place.py base-dir --work <作業名> --require
   ```

   **終了コードが 1 なら、この足場は失われている**（統合ツリーを外した、クローンを作り直した、
   `SKILL.md` §8 の後始末を済ませていた場合）。そのときは **topic PR の本文から台帳を書き直す**
   ——全体の計画と DoD・タスク一覧（件名・tier・依存・PR 番号・状態）がそこに残っている
   （[topic-pr.md](topic-pr.md)）。

   ```bash
   gh pr list --state all --search 'head:topic/' --json number,title,headRefName
   gh pr view <topicPR番号> --json body -q .body
   ```

   **`runId` は本文に無い**ので、`resumeFromRunId` と `worktree.py list --run` は使えない。
   残った worktree は `git worktree list` から名指しで拾う（手順 6）。作業名が分からなければ
   `git branch -r | grep '^  origin/topic/'` で topic ブランチ名から拾う。本文と git が食い違ったら
   手順 3 のとおり **git を信じる**（本文は取り込みの後に更新するので、落ちた場所によっては
   1 タスク分古い）。
3. `git -C <統合ツリー> log --oneline origin/topic/<作業名>` で、台帳の `merged` と実際の取り込みが
   一致するかを突き合わせる。**台帳より git を信じる**（台帳の更新前に落ちた可能性がある）。
4. `git branch -r | grep 'topic/<作業名>--task-'` で、走行中だったタスクの途中成果を確かめる。
   push 済みのコミットはワークフローが死んでも残っている（**PR はまだ作られていない**——
   PR はレビューが決着してからリードが作るので、ブランチだけが残る）。
5. **引き継ぎノートを読む**（`<ベース>/notes/task<番号>/`）。どこまで調べ、何を検証し、何が
   残っているかが役割ごとに残っている。立て直すワークフローはこれを読んで続きから始める。
6. `git worktree list` に残留があれば、[integration.md](integration.md) §4 の手順で
   未コミットの成果を保全してから消す。**旧セッションの `runId` は台帳にある。**
7. 未完のタスクを `TaskCreate` で登録し直し（新しいセッションでは新しいタスクリストになる）、
   台帳の `lead-session:` を新しいセッション ID に書き換える。
8. 決着済みだがまだ取り込んでいないブランチがあれば、先に [integration.md](integration.md) の手順で取り込む。
9. 空き枠に、未完タスクのワークフローを `resumeFrom` 付きで起動して続きを回す。

## 落ちたワークフローの扱い

**ワークフローのエージェントは再開できない**（`SendMessage` の宛先にならず、`agent()` に再開の
引数も無い。[design-notes.md](design-notes.md)）。立て直すときは**やり直しではなく続きから
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
   Workflow({ script: <組み立てたスクリプト>, args: { task, topic, base, work,
     resumeFrom: { branch: "<タスクブランチ>", sha: "<前コミット>",
                   transcriptDir: "<完了通知に入っていたパス>" } }})
   ```

   `sha` は `git log origin/<タスクブランチ> -1 --format=%H` で取る。**PR はまだ無い**
   （決着後にリードが作る）。`transcriptDir` も渡しておくと、必要なら前ランの `agent-*.jsonl` を
   読ませられる（引き継ぎノートの方が短いので、まずそちらを読ませる）。
3. **push も未コミットの変更も無ければ成果はゼロ**なので、`resumeFrom` を付けずに起動し直す。
4. **立て直しても決着に至らないタスクは打ち切る。** 台帳でそのタスクを `blocked` にし、
   返り値の `reason` と review.json の open を添えてユーザーへ上げる。**他のタスクは止めずに進める。**

利用制限で止まった場合、リードも同時に止まる。リードが再び動けるようになった時点で 1 から始める。
