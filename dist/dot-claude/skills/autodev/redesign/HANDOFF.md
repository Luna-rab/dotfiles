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
| 5 | ポリシー・反応・統括・実行器・決定的なステージ・指示書（`contracts/`）・`schemas/` | ほぼ済み。3 巡目の should-fix（S1〜S5）は直した。nit の N1〜N3 を直す作業と push が残っている（§6） |
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

3 巡目のレビューで出た should-fix の S1〜S5 は直し、レビューを通してマージした。次の手順で段 5 を締める。

1. nit の N1〜N3 を `medium-worker` に直させ、`medium-reviewer` でレビューしてからマージし、検査を通す（作業場所は `feature/autodev-redesign-domain-judgments`）
2. `feature/autodev-redesign` を origin に push する。ユーザーが頼んだのは段 5 を締めた後の push である。作業用の `feature/autodev-redesign-*` は push しない。PR は作らない
3. 段 6 に入る前に止まって、ユーザーに報告する

パスは `dist/dot-claude/skills/autodev/scripts/autodevlib/` を省いて書く。

**直す nit（ユーザーが選んだ）**

- **N1**: 載せ直すコミットが 0 件なら失敗にする判断が、`app/programs.py:272-276` にある。ドメインの規則にして、programs は事実を渡すだけにする
- **N2**: `session_lost` を決める式（`resumed and NO_RESULT and not initialized`）が、`app/executor.py:801` にある。事実を Evidence に載せ、Task が判定する
- **N3**: 根元が無いときに、コミット数を 0 として証拠に出している（`app/executor.py:648` あたり）。「数えられない」と「0 件」を見分ける

**段 6 へ回す**

- **S6. 取り下げた後に届いた回答は、`rejected.jsonl` に残るだけで `/autodev` に見えない**
  - 根拠: `domain/questions.py:83-92`・`app/files.py:63-86`
  - 段 6 で: `autodev answer` が `questions/<id>.json` の status を確かめ、withdrawn なら reason を添えて落ちるようにする。SKILL.md にも書く

**設計として残る点（すぐ直すものではない。ユーザーに伝える）**

- 答えて続けられる仕事（`answer_only`）は、答えて続ける輪に上限が無い
- ラン統括が応じずにユーザーが答えて起こし直した（RETRY）とき、その回答は元のエスカレーションには使えない。そのためユーザーに 2 回聞くことになる
- ラン統括の `ask-user` は、Run で開いていないエスカレーションの id でも質問を出せる（`domain/questions.py:55-76` は Run の状態を見ない）。開いたことの無い id への回答はラン統括を起こすので止まりはしないが、閉じた id で質問を出す道は残る。塞ぐなら、ask-user を Run が受けて確かめ、ポリシーが `PostQuestion` を出す形にする

### 直さないと決めたもの

- 古いイベントの列から読み戻すと、`EscalationRaised.failures` が抜けて 0 になり、上げを 1 回少なく数える。作り直しの途中で古い列がまだ無いので、直さない
- イベントの版を上げずに欄を足したので、直す前に記録したランの列は、新しい欄が既定値で読み戻る（`AnswerRecorded.escalation_closed` など）。同じ理由で直さない
- N4（検査用の `join()` が、待ち直しで後ろへ回した begin を待たない）。メインループには響かない
- N5（SIGKILL のタイマーは、子が SIGTERM で終わっても 10 秒後に必ず `killpg` を送る）。10 秒のうちに pid が一周しないと起きない
- S4 の直しで残る隙間。lock を確かめてから消すまでの数 ms に、同じ worktree の別の実行が git を始めうる。起きても git が落ちてステージの失敗になり、黙って壊れはしない

## 7. 触らないもの

- `~/.local/state/autodev/pr-body-markers/tree`。この作業と関係が無い
