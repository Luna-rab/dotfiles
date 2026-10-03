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
| 5 | ポリシー・反応・統括・実行器・決定的なステージ・指示書（`contracts/`）・`schemas/` | 済み。レビューと直しを 3 巡し、nit は N1〜N3 を直した |
| 6 | CLI・SKILL.md・HUD をつなぎ、本物の claude・gh で確かめる | 途中。CLI・`status --json`・HUD・SKILL.md はレビューを通してマージした。本物で確かめる作業が残っている |
| 7 | 文書を置き換え、旧 autodev の残りを消して仕上げる | 未着手 |

## 5. 次にやること

### 段 6

- **本物の claude・gh で確かめる**（ユーザーの了承済み）
  1. ARCHITECTURE §14 の一覧を、小さな `claude -p` と gh で 1 件ずつ確かめる。外れたら、実装・設計・ADDENDUM を直す
  2. テスト用の private リポジトリ `Luna-rab/autodev-sandbox`（手元は `~/ghq/github.com/Luna-rab/autodev-sandbox`、設定は `~/.config/autodev/repos/home__naru__ghq__github.com__Luna-rab__autodev-sandbox.json`）で、通しのランを 1 本走らせる。ブランチの push と draft PR の作成までで、マージはしない。走らせ始めるときと PR ができたときに、ユーザーに知らせる
  - sandbox には旧 autodev の PR（#2・#4・#5・#7・#8）が開いたまま残っている。触らない
- **status --json に足すか、まだ決めていないもの**（HUD で出せなくなった表示。段 6 を締めるときにユーザーに聞く）: 指摘の件数と中身・起動時の指示と受入条件・終えた実行の履歴・ステージの指示と出力・制限時間・エスカレーションの理由の文
- **旧 autodev を参照しているほかのファイル**: `dist/dot-claude/hooks/turnreview/core/turn.py`・`dist/dot-claude/skills/create-pr/SKILL.md`・`install.sh`・`dist/dot-vscode-server/data/Machine/settings.json`。名前とパスを出すだけで、旧い state は読まない。段 7 で見直す。`install.sh` を変えたら、もう 1 つの dotfiles の checkout にも入れる（ユーザーのメモリー）

### 段 7

- `README.md`・`DESIGN.md`・`GLOSSARY.md` は旧 autodev のままである。これを新しい設計で書き直し、ADDENDUM の中身を設計書へ移してから `redesign/` を消す
- テスト名の「・」を、Python 3.10〜3.12 でも読める名前にする（`pyproject.toml` は `requires-python = ">=3.10"`）
- 最後に検査をすべて流す

## 6. 残っている件

パスは `dist/dot-claude/skills/autodev/scripts/autodevlib/` を省いて書く。

**設計として残る点（すぐ直すものではない。ユーザーに伝える）**

- 答えて続けられる仕事（`answer_only`）は、答えて続ける輪に上限が無い。たとえば Rebase が落ち続け、ラン統括が毎回答えると回り続ける
- ラン統括が応じずにユーザーが答えて起こし直した（RETRY）とき、その回答は元のエスカレーションには使えない。そのためユーザーに 2 回聞くことになる
- squash・rebase でマージしたランの `purge` は、手元のコミットが origin のどこからも辿れなくなるので、毎回 `--force` が要る。理由の文で案内するだけで、判定は変えていない
- ラン統括の `ask-user` は、Run で開いていないエスカレーションの id でも質問を出せる（`domain/questions.py:55-76` は Run の状態を見ない）。開いたことの無い id への回答はラン統括を起こすので止まりはしないが、閉じた id で質問を出す道は残る。塞ぐなら、ask-user を Run が受けて確かめ、ポリシーが `PostQuestion` を出す形にする

### 直さないと決めたもの

- 古いイベントの列から読み戻すと、`EscalationRaised.failures` が抜けて 0 になり、上げを 1 回少なく数える。作り直しの途中で古い列がまだ無いので、直さない
- イベントの版を上げずに欄を足したので、直す前に記録したランの列は、新しい欄が既定値で読み戻る（`AnswerRecorded.escalation_closed` など）。同じ理由で直さない
- N4（検査用の `join()` が、待ち直しで後ろへ回した begin を待たない）。メインループには響かない
- N5（SIGKILL のタイマーは、子が SIGTERM で終わっても 10 秒後に必ず `killpg` を送る）。10 秒のうちに pid が一周しないと起きない
- S4 の直しで残る隙間。lock を確かめてから消すまでの数 ms に、同じ worktree の別の実行が git を始めうる。起きても git が落ちてステージの失敗になり、黙って壊れはしない
- 書き直す前のフローに届いた Rebase の結果は、コミットを数えられなかった（None）ときも、失敗の理由に「0 件」と書く（`domain/task.py:780-790`）。根元が無いと Rebase はエラーの証拠になるので、本番ではまず起きない
- Rebase のコミット数が、中身から実行器を通って証拠に載る道を確かめる検査が無い（`app/executor.py:665-671`）。テストの土台が `commits: 1` を入れるので、実行器が数を落としても気づけない。段 6 で本物の git を通すときに足すとよい

## 7. 触らないもの

- `.claude/worktrees/` の `feature/autodev-redesign-*` は、どれも統合のブランチにマージ済みである。消すかどうかはユーザーに聞く
- `~/.local/state/autodev/pr-body-markers/tree`。この作業と関係が無い
