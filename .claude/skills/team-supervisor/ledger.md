# 台帳の書式と、セッションが落ちたときの再開手順

## 2 か所に状態を持つ

| どこ | 何を持つ | 誰が書く |
| --- | --- | --- |
| 組込タスクリスト（`~/.claude/tasks/<team 名>/`） | 実行中の真実。所有者・状態・依存・metadata | リードとサブリーダーが `TaskCreate` / `TaskUpdate` で |
| `topic` 上の `docs/supervisor/<作業名>.ledger.md` | 節目の記録。タスク分解・承認状態・自律判断 | リードだけが commit する |

タスクリストにはクレームのファイルロックがあり、状態の同時更新を守る。台帳は成果物に残り、
最終 PR の差分に載る。**タスクリストは新しいセッションからは自動で引き継げない**（team 名が
`session-` + セッション ID の先頭 8 文字で、上書きできない）ので、再開の足場は台帳が持つ。

## 台帳を書くタイミング

1. **タスク設計の直後（v0）** — これが無いと分解そのものが失われる。必ず書く。
2. **1 タスクを topic へ取り込むたび** — 状態を `merged` にし、findings 件数・`decisions`・
   `deferrals` を写す。
3. **最終 PR を作る前** — 最終版に更新する。

## 書式

```markdown
# <作業名> 台帳

- team: session-a1b2c3d4        ← 再開時に ~/.claude/tasks/<この名前>/ を読むために要る
- topic: topic/<作業名>
- default-branch: main
- created: 2026-08-09
- ベース資料: docs/supervisor/<作業名>.brief.md / docs/supervisor/<作業名>.map.md

## 全体のゴールと DoD

<この作業全体で達成する状態>

## タスク一覧

| # | 件名 | tier | 依存 | ブランチ | PR | 状態 | must | should |
|---|---|---|---|---|---|---|---|---|
| 1 | パーサの境界値を直す | standard | — | topic/x--task-1 | #12 | merged | 0 | 2 |
| 2 | エラー型を整理する | standard | — | topic/x--task-2 | #13 | approved | 0 | 1 |
| 3 | 一覧表を更新する | light | 1,2 | topic/x--task-3 | — | pending | — | — |

状態は `pending` / `running` / `approved` / `merged` / `blocked` / `failed`。

## タスクの詳細

### task 1: パーサの境界値を直す

- **DoD**: 空入力でも panic せず、`Err(EmptyInput)` を返す状態
- **受け入れ基準と検証**: `cargo test parser::` が通る。`cargo clippy -- -D warnings` が通る。
  `echo "" | ./target/debug/app` が終了コード 1 と `empty input` を返す
- **スコープ境界**: 触ってよいのは `src/parser/`。`src/core/` には触らない
- **調査の入口**: `src/parser/`、`Parser::parse`、`ParseError`
- **隣接タスクとの契約**: task 2 が `ParseError` に variant を足すので、既存 variant は消さない
- **findings**: must 0 / should 2（PR #12 のスレッドに残っている）
- **decisions**: none
- **deferrals**: `Err` の詳細メッセージの整形は task 2 に寄せた（重複を避けるため）

## 自律判断の記録

### 変更した最終目標・DoD・スコープ

- <何を・なぜ・判断材料（根拠と退けた代替案）・残課題>

### 先送り・対象外にした作業

- <何を・なぜ・次に何をすべきか>
```

## セッションが落ちたときの再開手順

`/resume` できるかどうかに関わらず、同じ手順で再開できる。

1. `git fetch origin && git checkout topic/<作業名> && git pull`
2. `docs/supervisor/<作業名>.ledger.md` を読む。全タスクの DoD・tier・依存・状態が分かる。
3. `git log --oneline origin/topic/<作業名>` で、台帳の `merged` と実際の取り込みが一致するか
   突き合わせる。**台帳より git を信じる**（台帳の更新前に落ちた可能性がある）。
4. `git branch -r | grep 'topic/<作業名>--task-'` と
   `gh pr list --base topic/<作業名> --state all` で、走行中だったタスクの途中成果を確かめる。
   push 済みのコミットはサブリーダーが死んでも残っている。
5. **旧タスクリストが読めるなら読む**（任意）。台帳の `team:` 行にある名前で
   `~/.claude/tasks/session-a1b2c3d4/` を Read すると、fix ラウンド数や metadata が拾える。
   **このファイルの形式は公式に文書化されておらず、バージョンで変わる。読めなければ飛ばす**
   ——1〜4 だけで再開できる。
6. `git worktree list` に残留があれば、[integration.md](integration.md) §2 の手順で消す。
7. 未完のタスクを `TaskCreate` で登録し直し（新しいセッションでは新しいタスクリストになる）、
   台帳に `team:` の新しい名前を書いて commit する。
8. 承認済みだが未統合のブランチがあれば、先に [integration.md](integration.md) の手順で取り込む。
9. 空き枠にサブリーダーを spawn して続きを回す。

## 落ちたサブリーダーの扱い

途中で死んだサブリーダーのタスクは、**やり直さずに途中成果から続ける**。

- ブランチに push 済みのコミットがあれば、それを起点に新しいサブリーダーを立てる
  （spawn プロンプトに「前コミット `<SHA>` まで実装済み。ここから続けよ」と書く）。
- PR が作られていればそのまま使う。レビュースレッドも残っている。
- push が無ければ成果はゼロなので、最初から立て直す。
