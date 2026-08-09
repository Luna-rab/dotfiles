# skill-checks 台帳

- lead-session: 6a417a7b-8307-4f8e-b89a-1f4d0e6dd3bb
- task-list: session-6a417a7b
- topic: topic/skill-checks
- default-branch: main
- created: 2026-08-10
- ベース資料: docs/supervisor/skill-checks.brief.md / docs/supervisor/skill-checks.map.md

## 全体のゴールと DoD

このリポジトリに**検証コマンド一式が存在する**状態にする。具体的には、引数なしで実行できて
終了コードで合否を返すチェックが 2 本あり、それが CI で自動的に走り、README に走らせ方が
書いてある状態。

現状は CI もテストも無く、`.claude/skills/` 配下の壊れ（frontmatter の YAML 崩れ、リンク切れ、
参照先スクリプトの消失、`SKILL.md` の肥大）を機械的に検出する手段が無い。この作業で埋める。

## タスク一覧

| # | 件名 | tier | 依存 | ブランチ | PR | agentId | 状態 | must | should |
|---|---|---|---|---|---|---|---|---|---|
| 1 | スキル検査スクリプトを作る | standard | — | topic/skill-checks--task-1 | #9 | a3ae954acf424418b | running | — | — |
| 2 | subagent-stop.sh の自動テストを作る | standard | — | topic/skill-checks--task-2 | #8 | ab21787d905dd27f9 | running | — | — |

### 中断の記録

**2026-08-10 08:54 JST 時点**: 利用制限（`You've hit your session limit · resets 3:50am`）で
task1・task2 のサブリーダーと `/code-review` の子が同時に落ちた。両タスクとも実装が終わって
PR を作り、レビュアーを起動する直前だった。

- 成果は全部 push 済み（task1 は 2 コミット、task2 は 1 コミット）。
  **両サブリーダーの worktree に未コミットの変更は 0 件**——実装 subagent への
  「意味のある単位ごとに commit・push」の義務づけが効いた。
- worktree は 6 つ残っている（サブリーダー 2 ＋ レビュアー 4）。再開を打ち切るまで消さない。
- 再開は 1 回目（`resume-count-task1` / `resume-count-task2` に記録）。
| 3 | CI と README に載せる | light | 1,2 | topic/skill-checks--task-3 | — | — | pending | — | — |

状態は `pending` / `running` / `approved` / `merged` / `blocked` / `failed`。

## タスクの詳細

### task 1: スキル検査スクリプトを作る

- **DoD**: `.claude/scripts/check-skills.py` があり、リポジトリのルートから引数なしで実行すると
  `.claude/skills/` 配下の全スキルを検査して、問題があれば終了コード 1 と「どのファイルの
  どの検査に落ちたか」を出力し、問題が無ければ終了コード 0 を返す状態。検査項目は次の 4 つ。
  - frontmatter が YAML として妥当に読める
  - `SKILL.md` が 500 行以下（`.claude/rules/editing-skills.md` の基準）
  - 本文から張られた同一スキル内の相対リンク（`*.md`）の実在
  - 本文が参照する `scripts/` 配下のファイルの実在。`.sh` は実行権限があること
- **受け入れ基準と検証**:
  - `python3 -m py_compile .claude/scripts/check-skills.py` が通る
  - `python3 .claude/scripts/check-skills.py` が終了コード 0 を返す
  - **わざと壊した入力で 1 を返すことを実際に示す**（一時ディレクトリに不正な `SKILL.md` を
    置いて検査させる、など）。出力に落ちたファイル名と検査名が出る
- **スコープ境界**: 作ってよいのは `.claude/scripts/check-skills.py` の 1 本だけ。
  **既存のスキルファイルを直さない**——検査に落ちる箇所を見つけても、直さずに報告する。
  CI への登録は task3 の担当なので手を出さない。
- **調査の入口**: `.claude/skills/`（4 スキル）、`.claude/scripts/statusline.sh`、
  `.claude/rules/editing-skills.md`
- **隣接タスクとの契約**: task3 が CI から `python3 .claude/scripts/check-skills.py` の形で
  呼ぶ。**引数なし・リポジトリのルートから実行・終了コード 0 / 1** を変えない。
- **tier**: standard

### task 2: subagent-stop.sh の自動テストを作る

- **DoD**: `.claude/scripts/test-subagent-stop.sh` があり、引数なしで実行すると
  `.claude/skills/team-supervisor/scripts/subagent-stop.sh` の 4 つの分岐について期待どおりの
  終了コードが返ることを確かめ、全部通れば 0、1 つでも外れれば 1 を返す状態。4 つの分岐は
  `hooks.md` の「振る舞い」表にある。
  - ブランチ登録が無い → 素通し（0）
  - 登録があり、そのブランチが origin に push されていない → 押し戻し（2）
  - `blocked-<agentId>` の目印がある → 素通し（0）
  - 押し戻しが 3 回を超えた → 打ち切り（1）
- **受け入れ基準と検証**:
  - `bash -n .claude/scripts/test-subagent-stop.sh` が通る
  - `.claude/scripts/test-subagent-stop.sh` が終了コード 0 を返し、4 ケースの結果が出力に出る
  - **このリポジトリを汚さない**: 実行の前後で `git status --porcelain` の出力が変わらず、
    `.git/team-supervisor/` が残らない。検査は使い捨ての一時 git リポジトリで行う
- **スコープ境界**: 作ってよいのは `.claude/scripts/test-subagent-stop.sh` の 1 本だけ。
  **`subagent-stop.sh` 本体を変えない**——テストが落ちたら、直さずに報告する。
- **調査の入口**: `.claude/skills/team-supervisor/scripts/subagent-stop.sh`、
  `.claude/skills/team-supervisor/hooks.md`（振る舞いの表と、状態ディレクトリのファイル一覧）
- **隣接タスクとの契約**: task3 が CI から `.claude/scripts/test-subagent-stop.sh` の形で呼ぶ。
  **引数なし・リポジトリのルートから実行・終了コード 0 / 1** を変えない。
- **tier**: standard

### task 3: CI と README に載せる

- **DoD**: `.github/workflows/` に、`push` と `pull_request` で task1・task2 の 2 本を走らせる
  ワークフローがある状態。README に検査の走らせ方（2 本のコマンドと、それぞれが何を見るか）が
  書いてある状態。
- **受け入れ基準と検証**:
  - ワークフローの YAML が `python3 -c "import yaml,sys; yaml.safe_load(open('<パス>'))"` で読める
  - ローカルで `python3 .claude/scripts/check-skills.py` と
    `.claude/scripts/test-subagent-stop.sh` を流して両方 0 が返る
  - README の追記が `.claude/rules/writing-style.md` と `japanese-checks.md` に従う
- **スコープ境界**: 触ってよいのは `.github/workflows/` の新規ファイルと `README.md` だけ。
  **task1・task2 の成果物を変えない**——CI で落ちたら、直さずに報告する。
- **調査の入口**: `README.md`、`.claude/scripts/`（task1・task2 の成果物）
- **隣接タスクとの契約**: task1 と task2 が提供するコマンドの呼び出し形（引数なし・ルートから
  実行・終了コード 0 / 1）に合わせる。ubuntu-latest に `python3` はあるが **`PyYAML` は
  無い**ので、ワークフローで入れる。
- **tier**: light

## 自律判断の記録

### 変更した最終目標・DoD・スコープ

- **作業対象を「このスキルを e2e で実測する」から「検証コマンド一式を作る」に具体化した。**
  ワークフローは実装 → レビュー → PR を回す器なので、実際のコード作業が要る。題材を選ぶにあたり、
  起動前の調査で「このリポジトリには検証コマンド一式が 1 つも無い」ことが分かった。これは
  `brief.md` の中核が空になり、レビュー subagent の「検証コマンド一式を実際に流す」が
  成立しないという構造的な穴である。穴を埋める作業を題材にすれば、e2e の測定と成果物が
  一致する。退けた代替案: README の追記だけの小さいタスクで機械の動作だけ測る案——安いが、
  レビューの検証経路と統合レーンのビルド検査を測れないまま残るので採らなかった。
- **ルートの `.gitignore` に `!/.github` と `!/docs` の 2 階層を足した。** このリポジトリの
  `.gitignore` は `/*` と `/.**` で全無視してから `!` で個別に復活させる許可リスト方式で、
  `docs/` も `.github/` も無視される。台帳を topic に commit できず（スキルの再開設計が
  成立しない）、task3 の CI も置けなかった。`.gitignore` の冒頭コメントが「階層は必要なだけ
  追加する」と明記しているので、この追加はファイル自身が想定している拡張だと判断した。
  退けた代替案: 台帳を `.claude/supervisor/` に置いて `.gitignore` を触らない案——台帳は
  置けるが `.github/` が無いままで CI を作れず、task3 が README の追記だけに縮む。

### 先送り・対象外にした作業

- （まだ無し）
