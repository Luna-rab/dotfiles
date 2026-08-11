# 台帳の書式と、セッションが落ちたときの再開手順

## 3 か所に状態を持つ

| どこ | 何を持つ | 誰が書く |
| --- | --- | --- |
| 組込タスクリスト（`~/.claude/tasks/session-<セッション ID の先頭 8 文字>/`） | 実行中の真実。状態・依存・metadata | **リードだけ**が `TaskCreate` / `TaskUpdate` で |
| `<ベース>/ledger.md` | 節目の記録。タスク分解・承認状態・自律判断 | リードだけが書く（commit しない） |
| `<ベース>/notes/task<番号>/` | 引き継ぎノート。読んだ箇所・実行した検証・構造の要点 | ワークフローの各エージェント |

`<ベース>` は次で受け取る絶対パス。`.git` 配下なので **git の追跡対象に入らず、PR の差分にも
出ない**。全 worktree から同じパスに解決でき、リポジトリのクローンが残る限りセッションをまたいで
残る（`/tmp` は WSL の再起動で消えるので使えない）。

```bash
~/.claude/skills/supervisor/scripts/place.py base-dir --work <作業名>
```

**ワークフローのエージェントはタスクリストに触れない。** 状態はリードが返り値を受けて更新する。

台帳はリポジトリの履歴に残らない。**ユーザーが後から読める記録は最終 PR 本文だけ**なので、
タスク一覧と自律判断は PR 本文へ転記する（`SKILL.md` §8・§9）。タスクリストのディレクトリ名は
セッション ID から決まるので、**新しいセッションを立てるとタスクリストは引き継げない**
（`claude --resume <元のセッション ID>` で開き直せば、`--fork-session` を付けない限りセッション
ID が再利用されるので残る）。したがって再開の足場は台帳が持つ。

## 台帳を書くタイミング

1. **タスク設計の直後（v0）** — これが無いと分解そのものが失われる。必ず書く。
2. **ワークフローを起動した直後** — `runId` を控える。後始末（`worktree.py remove --run`）と
   同一セッション内の再実行（`resumeFromRunId`）に要る。
3. **1 タスクを topic へ取り込むたび** — 状態を `merged` にし、`shouldFix` の件数・`decisions`・
   `deferrals` を写す。
4. **最終 PR を作る前** — 最終版に更新し、同じ内容を PR 本文へ転記する。

## 書式

```markdown
# <作業名> 台帳

- lead-session: a1b2c3d4-....   ← claude --resume に渡すセッション ID（/status で確かめる）
- task-list: session-a1b2c3d4   ← ~/.claude/tasks/<この名前>/
- topic: topic/<作業名>
- default-branch: main
- created: 2026-08-11
- ベース資料: 同じディレクトリの brief.md / map.md
- 引き継ぎノート: 同じディレクトリの notes/task<番号>/

## 全体のゴールと DoD

<この作業全体で達成する状態>

## タスク一覧

| # | 件名 | tier | 依存 | ブランチ | PR | runId | 状態 | should |
|---|---|---|---|---|---|---|---|---|
| 1 | パーサの境界値を直す | standard | — | topic/x--task-1 | #12 | wf_a1b2c3-456 | merged | 2 |
| 2 | エラー型を整理する | standard | — | topic/x--task-2 | #13 | wf_d4e5f6-789 | approved | 1 |
| 3 | 一覧表を更新する | light | 1,2 | topic/x--task-3 | — | — | pending | — |

`runId` は `Workflow` の返り値にある値。worktree の後始末（`worktree.py list --run` /
`remove --run`）と、同一セッション内での再実行（`resumeFromRunId`）に使う。

状態は `pending` / `running` / `approved` / `merged` / `blocked` / `failed`。

## タスクの詳細

### task 1: パーサの境界値を直す

- **DoD**: 空入力でも panic せず、`Err(EmptyInput)` を返す状態
- **受け入れ基準と検証**: `cargo test parser::` が通る。`cargo clippy -- -D warnings` が通る。
  `echo "" | ./target/debug/app` が終了コード 1 と `empty input` を返す
- **スコープ境界**: 触ってよいのは `src/parser/`。`src/core/` には触らない
- **調査の入口**: `src/parser/`、`Parser::parse`、`ParseError`
- **隣接タスクとの契約**: task 2 が `ParseError` に variant を足すので、既存 variant は消さない
- **findings**: should-fix 2 件（PR #12 のスレッドに残っている。裁定が残件として畳んだ）
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

1. `git fetch origin && git checkout topic/<作業名> && git pull`
2. 台帳を読む。全タスクの DoD・tier・依存・状態が分かる。置き場は次で受け取る
   （`.git` 配下にあり、`git checkout` では作業ツリーに現れない）。

   ```bash
   ~/.claude/skills/supervisor/scripts/place.py base-dir --work <作業名> --require
   ```

   **終了コードが 1 なら、この足場は失われている**（クローンを作り直した、`.git` を消した、
   `SKILL.md` §8 の後始末を済ませていた場合）。台帳無しでの再開は手順 3〜4 の git と gh だけで
   行い、そこから台帳を書き直す。作業名が分からなければ
   `git branch -r | grep '^  origin/topic/'` で topic ブランチ名から拾う。
3. `git log --oneline origin/topic/<作業名>` で、台帳の `merged` と実際の取り込みが一致するか
   突き合わせる。**台帳より git を信じる**（台帳の更新前に落ちた可能性がある）。
4. `git branch -r | grep 'topic/<作業名>--task-'` と
   `gh pr list --base topic/<作業名> --state all` で、走行中だったタスクの途中成果を確かめる。
   push 済みのコミットはワークフローが死んでも残っている。
5. **引き継ぎノートを読む**（`<ベース>/notes/task<番号>/`）。どこまで調べ、何を検証し、何が
   残っているかが役割ごとに残っている。立て直すワークフローはこれを読んで続きから始める。
6. `git worktree list` に残留があれば、[integration.md](integration.md) §4 の手順で
   未コミットの成果を保全してから消す。**旧セッションの `runId` は台帳にある。**
7. 未完のタスクを `TaskCreate` で登録し直し（新しいセッションでは新しいタスクリストになる）、
   台帳に `lead-session:` と `task-list:` の新しい値を書く。
8. 承認済みだが未統合のブランチがあれば、先に [integration.md](integration.md) の手順で取り込む。
9. 空き枠に、未完タスクのワークフローを `resumeFrom` 付きで起動して続きを回す。

## 落ちたワークフローの扱い

**ワークフローのエージェントは再開できない**（`SendMessage` の宛先にならず、`agent()` に再開の
引数も無い。[design-notes.md](design-notes.md)）。立て直すときは**やり直しではなく続きから
始める**。足場は次の 4 つである。

| 残るもの | 何に使うか |
| --- | --- |
| push 済みのコミット | `resumeFrom.sha`。「ここまで実装済み」を実装に伝える |
| PR | `resumeFrom.pr`。作り直させない |
| レビュースレッド | 未解決があれば、初回レビューではなく修正ラウンドから始める |
| 引き継ぎノート | 各役割が読んだ箇所・検証結果。再探索を省く |

順に試す。

1. **同一セッションの中なら `resumeFromRunId` で再実行する。** 完了済みの `agent()` は
   `journal.jsonl` のキャッシュから返るので安い。**先に走行中の run を `TaskStop` で止める。**
   スクリプトを 1 行でも変えると、変えた箇所より後ろは全部再実行される。
2. **セッションが落ちた・スクリプトを組み直したときは `resumeFrom` を付けて新しく起動する**
   （`SKILL.md` §7）。`sha` は `git log origin/<タスクブランチ> -1 --format=%H` で取る。
   完了通知に入っていた `transcriptDir` も渡しておくと、必要なら前ランの `agent-*.jsonl` を
   読ませられる（引き継ぎノートの方が短いので、まずそちらを読ませる）。
3. **push も未コミットの変更も無ければ成果はゼロ**なので、`resumeFrom` を付けずに起動し直す。
4. **立て直しても承認に至らないタスクは打ち切る。** 台帳でそのタスクを `blocked` にし、
   返り値の `reason` と PR のスレッドを添えてユーザーへ上げる。**他のタスクは止めずに進める。**

利用制限で止まった場合、リードも同時に止まる。リードが再び動けるようになった時点で 1 から始める。
