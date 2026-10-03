# 作り直しの引き継ぎ

autodev を一から作り直す作業を、新しい会話で続けるための資料。作り直しが終わったら（段 7）`redesign/` ごと消す。

## 1. 読む順番

1. このファイル
2. [IMPLEMENTING.md](IMPLEMENTING.md): 置き場・書き方・検査・コミットの作法。§1 は絶対に守る
3. [ARCHITECTURE.md](ARCHITECTURE.md) → [DOMAIN_MODEL.md](DOMAIN_MODEL.md) → [ADDENDUM.md](ADDENDUM.md)。食い違ったら ADDENDUM が勝つ。§12 は実装で設計書から変えたこと
4. [LEDGER.md](LEDGER.md): 旧 autodev から持ち込む知見と持ち込まない知見。旧実装は `git show 4465542:<パス>` で読む

## 2. ユーザーと決めた、守ること

- **ビジネスロジックはドメイン層にだけ書く。** ユーザーがいちばん嫌うのは、設計で決めすぎてアドホックな対応がコードに散らばることである。app/・adapters/ で判断が要ったら、そこに書かずにドメインへ名前の付いた問いを足す
- **旧 autodev の進め方の規則を持ち込まない。** 持ち込むのは LEDGER の「持ち込む」に挙げた、外部の仕組みとインフラの事実だけ
- **段を飛ばさない。** /autodev（ユーザーとの対話）→ ラン統括 → タスク統括 → ステージ。エスカレーションも回答も 1 段ずつ通る（ARCHITECTURE.md:67）
- **旧 autodev は最後に完全に置き換える。** 旧い文書やコードを残さない
- main で作業しない。push と PR は、ユーザーに頼まれたときだけ行う

## 3. 進め方

- 統合のブランチは `feature/autodev-redesign`。作業ごとに `feature/autodev-redesign-<名前>` を `.claude/worktrees/<名前>` に切り、`git merge --no-ff` で統合のブランチへ戻す
- 実装はサブエージェント `medium-worker` に任せる。成果物は必ず `medium-reviewer` に /code-review でレビューさせ、must-fix と should-fix を直させてからマージする（どちらも `.claude/agents/` にあり、effort は medium）。作業者への指示には、作業場所・読む文書・IMPLEMENTING §1・検査・コミットの決まり・返してほしい項目を書く
- 作業者を並列に走らせるときは、触るファイルを分ける。片方をマージしたら、走っている側に重なる所を伝え、仕上げる前に統合のブランチを取り込ませる
- 検査は `uv run` で流す（`ruff check`・`ruff format --check`・`ty check`・`pytest -q`）。`python3 .claude/scripts/check-skills.py` も流す。システムの python3（3.12）だと、検査名の「・」のせいで `test/autodev/test_driver.py` などが読み込めない
- 作業者はときどき Bash のヒアドキュメントでファイルを書き換える。報告に書いてきたら、レビューで中身を確かめさせる

## 4. 段と状態

| 段 | 中身 | 状態 |
| --- | --- | --- |
| 1 | 知見の台帳（LEDGER.md） | 済み |
| 2 | ドメインの土台（値・コマンド・イベント・ステージの定義・フローの検査） | 済み |
| 3 | 集約・ドメインサービス・つなぎ目の表（`test_seams.SEAMS`） | 済み |
| 4 | インフラ（SQLite のイベントストア・メインループ）・アダプタ（claude・git・gh）・ガードのフック | 済み |
| 5 | ポリシー・反応・統括・実行器・決定的なステージ・指示書（`contracts/`）・`schemas/` | ほぼ済み。レビューと直しを 2 巡した。3 巡目のレビューで出た should-fix を直す作業が残っている（§6） |
| 6 | CLI・SKILL.md・HUD をつなぎ、本物の claude・gh で確かめる | 未着手 |
| 7 | 文書を置き換え、旧 autodev の残りを消して仕上げる | 未着手 |

## 5. 次にやること

### 段 6

- **CLI**: `scripts/autodev.py` は、いまは終了コード 1 を返すだけの置き場である。ここに次のものを作る。
  - サブコマンド: `run`・`status --json`・`events`・`answer`・`clean`・`purge`・`ask`
  - driver の組み立て。本物の実行器は、どこからもまだつないでいない。形は `Driver(paths, executor=lambda parts: from_parts(parts, AgentRuntime(), verify=..., test_globs=..., protected_globs=..., untested_globs=...), runtime=...)`（`app/driver.py`・`app/executor.py`）
  - リポジトリごとの設定を読む所。読むのは検証コマンド・テストのパス・変更禁止のパス・テストが要らないパスで、LEDGER の FP-09 を参照。これもまだ無い
  - 終了コードは、0（ランを終えた）・1・3（パニック）・4（回答待ち）。2 は廃止した
- **SKILL.md**:
  - /autodev の入口と出口を書き直す。`questions/` を Monitor で見てユーザーに渡し、回答を置いて `autodev run` を呼び直す（ARCHITECTURE §6）
  - `disable-model-invocation: true` と「作り直しの途中」の注記は、段 7 で外す
- **HUD**: `dist/dot-claude/scripts/hud/ports/autodev.py` と `dist/dot-claude/scripts/autodev-watch.py` を、`autodev status --json` を読む形にする（ARCHITECTURE §12）。`hud/core`・`hud/render` にも旧い state の形を読む所がある
- **旧 autodev を参照しているほかのファイル**: `dist/dot-claude/hooks/turnreview/core/turn.py`・`dist/dot-claude/skills/create-pr/SKILL.md`・`install.sh`・`dist/dot-vscode-server/data/Machine/settings.json`。段 6 か段 7 で見直す。`install.sh` を変えたら、もう 1 つの dotfiles の checkout にも入れる（ユーザーのメモリー）
- **本物の claude・gh で確かめる**: ARCHITECTURE §14 の一覧を確かめる。外れたら設計と ADDENDUM を直す

### 段 7

- `README.md`・`DESIGN.md`・`GLOSSARY.md` は旧 autodev のままである。これを新しい設計で書き直し、ADDENDUM の中身を設計書へ移してから `redesign/` を消す
- テスト名の「・」を、Python 3.10〜3.12 でも読める名前にする（`pyproject.toml` は `requires-python = ">=3.10"`）
- 最後に検査をすべて流す

## 6. 残っている件

### 段 5 を締める（段 6 より先にやる）

段 5 の 2 回目の直しをまとめたレビューは返ってきた。must-fix は無く、should-fix が 6 件、nit が 5 件あった。まだ直していない。次の手順で段 5 を締める。

1. 下の S1〜S5 を `medium-worker` に直させ、`medium-reviewer` でレビューしてからマージし、検査を通す。S6 は段 6 の CLI を作るときの約束なので、段 6 に回す
2. nit（N1〜N5）は直すかどうかをユーザーに聞く
3. `feature/autodev-redesign` を origin に push する。ユーザーが頼んだのは段 5 を締めた後の push である。作業用の `feature/autodev-redesign-*` は push しない。PR は作らない
4. 段 6 に入る前に止まって、ユーザーに報告する

パスは `dist/dot-claude/skills/autodev/scripts/autodevlib/` を省いて書く。レビューで確かめた結論は次の 3 つ。

- 段を飛ばさない規則は守られている
- 根元の値は、S3 の場面を除いて正しい
- 同じ worktree で 2 つ同時に走る道は無い

**should-fix**

- **S1. 戻すのを飛ばした後に begin が失敗すると、次の試みが戻さないまま始まる**
  - 根拠: `app/executor.py:489-497`・`domain/task.py:446-447`・`:720-729`
  - 何が起きるか: 作り直しの `_restart` が待ち切れずに戻さず、次の試みの begin も失敗して FAILED になる。すると `reset_before_start` は、すぐ前の試みが RESTARTED でないので None を返す。
  - 直し方: 始めていない試み（`start_commit is None`）を飛ばして遡る。並びの検査を足す
- **S2. 回答が届く前にエスカレーションが閉じると、ラン統括が返せる判断の無い ANSWER で起こされ、出口の無い輪になる**
  - 根拠: `domain/run.py:899-912`・`domain/supervision.py:146-149`・`domain/questions.py:72-80`
  - 何が起きるか: ユーザーの回答とラン統括の stop-tasks・replan が前後すると、ラン統括は `answer` を拒まれる。差し戻しを使い切って supervisor-failed になり、ユーザーに聞き、答えるとまた同じ知らせで起きる。
  - 直し方: ドメインで止める。閉じたエスカレーションへの回答は AnswerRecorded に印を付け、`_wake_run` はその印を見て起こさない。または記録しない
- **S3. driver が落ちた後に Rebase を流し直すと、終わった rebase をもう一度かけて、ありもしない衝突を作る**
  - 根拠: `app/programs.py:270-279`・`domain/task.py:749-759`・`app/executor.py:425-432`
  - 何が起きるか: rebase を終えてから結果が載る前に落ちると、根元が古いまま `--onto` を流し直す。レビューが git で再現した。
  - 直し方: StageSpec に「流し直す前に `start_commit` へ戻す」を宣言し（`abandons_rebase` と同じ形）、実行器はそれに従う。検査を足す
- **S4. 残った `index.lock` が、ふつうの場面ではほとんど消えない**
  - 根拠: `app/executor.py:514`・`:521`・`:371-377`
  - 何が起きるか: 止めた走りは、終わるとすぐ `_stopping` から外れる。そのため、lock を消す条件に当たらない。検査（`test/autodev/test_executor.py:554-603`）は、実際のコードが作らない状態を手で作って通している。
  - 直し方: interrupt したら worktree ごとに「止めた後に片付けていない」印を立て、次の仕事が lock を確かめたら下ろす
- **S5. 走り出した直後に止めた実行が、自分の終わりを 120 秒待つ**
  - 根拠: `app/executor.py:514`（`waiting` が `besides` を除いていない）・`:562`
  - 何が起きるか: 同じ worktree の begin なども一緒に待つ。
  - 直し方: `waiting` から `besides` を除くか、`_run` の初めで止めた走りなら抜ける
- **S6（段 6 へ回す）. 取り下げた後に届いた回答は、`rejected.jsonl` に残るだけで `/autodev` に見えない**
  - 根拠: `domain/questions.py:83-92`・`app/files.py:63-86`
  - 段 6 で: `autodev answer` が `questions/<id>.json` の status を確かめ、withdrawn なら reason を添えて落ちるようにする。SKILL.md にも書く

**nit（直すかはユーザーに聞く）**

- **N1**: 載せ直すコミットが 0 件なら失敗にする判断が、`app/programs.py:272-276` にある。ドメインの関数にして、programs はそれを呼ぶだけにする案がある
- **N2**: `session_lost` を決める式（`resumed and NO_RESULT and not initialized`）が、`app/executor.py:773` にある。事実を Evidence に載せ、Task が判定する案がある
- **N3**: 根元が無いときに、コミット数を 0 として証拠に出している（`app/executor.py:620-622`）
- **N4**: 検査用の `join()` は、待ち直しで後ろへ回した begin を待たない（`app/executor.py:324-333`）。メインループには響かない
- **N5**: SIGKILL のタイマーは、子が SIGTERM で終わっても 10 秒後に必ず送る（`adapters/_proc.py:50-53`・`:106-114`）。そのあいだに pid が使い回されると、別の子を殺しうる。ただしレビューの推測である

**設計として残る点（すぐ直すものではない。ユーザーに伝える）**

- 答えて続けられる仕事（`answer_only`）は、答えて続ける輪に上限が無い
- ラン統括が応じずにユーザーが答えて起こし直した（RETRY）とき、その回答は元のエスカレーションには使えない。そのためユーザーに 2 回聞くことになる

### 直さないと決めたもの

- 古いイベントの列から読み戻すと、`EscalationRaised.failures` が抜けて 0 になり、上げを 1 回少なく数える。作り直しの途中で古い列がまだ無いので、直さない

## 7. 触らないもの

- `~/.local/state/autodev/pr-body-markers/tree`。この作業と関係が無い
