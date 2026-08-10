# skill-checks 前提資料（brief）

サブリーダーと配下の subagent は、作業を始める前にこれを読む。

## この作業の位置づけ

**このリポジトリには現在、検証コマンド一式が存在しない。** CI 定義（`.github/workflows/`）も
テストも無く、shellcheck・shfmt・yamllint・markdownlint はいずれも未インストールである。
「マージしてよい」と機械的に言える根拠が無い状態を、この作業で埋める。

つまり**検証の仕組みを作ることが作業そのもの**なので、着手時点では流せる共通のチェックが無い。
各タスクは自分が作った成果物を自分で実行して確かめる（下記「暫定の検証」）。

## 検証コマンド一式

### 暫定の検証（task1・task2 が完成するまで）

| 対象 | コマンド |
| --- | --- |
| Python の構文 | `python3 -m py_compile <ファイル>` |
| Bash の構文 | `bash -n <ファイル>` |
| 成果物そのもの | 実際に起動して終了コードと出力を見る（下記「外形動作」） |

### 完成後の検証（task3 がこれを CI に載せる）

```bash
python3 .claude/scripts/check-skills.py          # task1 の成果物
.claude/scripts/test-subagent-stop.sh            # task2 の成果物
```

**task3 のサブリーダーは、上の 2 本を実際に流して通ることを承認の条件にする。**

## 外形動作を確かめる手順

報告を信じず、レビュアーとリードは自分で動かす。

```bash
# task1 の成果物: 正常系は終了コード 0、検査に落ちる入力では 0 以外
python3 .claude/scripts/check-skills.py; echo "exit=$?"

# task2 の成果物: 6 ケースが期待どおりの終了コードを返すこと
.claude/scripts/test-subagent-stop.sh; echo "exit=$?"

# task2 が検査する対象（フック本体）を手で叩く場合
echo '{"hook_event_name":"SubagentStop","agent_id":"affffffffffffffff"}' |
  .claude/skills/team-supervisor/scripts/subagent-stop.sh; echo "exit=$?"
```

`.claude/scripts/check-skills.py` は**リポジトリのルートから**実行する前提で書くこと。

## 使える道具

`python3`（標準ライブラリ ＋ `PyYAML`）、`jq`、`git`、`gh`。
**それ以外を新しくインストールしない。** CI でも同じ前提が要るので、追加の依存を増やさない。

## 不可侵パス

触ってはならない。

- `.zshrc` / `install.sh` / `mise/` / `sheldon/` — dotfiles の実体。この作業の対象外。
- `.claude/settings.json` — 環境変数の設定。触ると走行中のエージェントに影響する。
- `.claude/skills/*/SKILL.md` と各スキルの補助 `.md` — **検査の「対象」として読むだけ。
  書き換えない。** 検査に落ちる箇所を見つけたら、直さずに報告する。
- `.claude/skills/team-supervisor/scripts/subagent-stop.sh` と `gh-review.py` — 同上。
  task2 はこれを**外から叩いて**検査する。中身を変えない。
- `docs/supervisor/` — リードが管理する台帳とベース資料。

## 成果物を置いてよい場所

**ルートの `.gitignore` は許可リスト方式**で、`/*` と `/.**` を全無視してから `!` で個別に
復活させる。**復活していない場所にファイルを作っても git が追跡しない**（`git add` しても
無視され、push しても成果が消える）。

追跡される場所は `.zshrc` / `mise/` / `sheldon/` / `.claude/**` / `.github/**` / `docs/**` /
`install.sh` / `README.md` だけ。この作業の成果物は次に置く。

| 成果物 | 置き場所 |
| --- | --- |
| 検査スクリプト | `.claude/scripts/` |
| CI のワークフロー | `.github/workflows/` |
| 台帳とベース資料 | `docs/supervisor/`（リードが管理） |

**新しい場所にファイルを作ったら、`git check-ignore -q <パス>` で追跡されるか確かめる**
（終了コード 0 は「無視される」）。

## ブランチとコミットの規約

- デフォルトブランチ: `main`
- topic ブランチ: `topic/skill-checks`
- タスクブランチ: `topic/skill-checks--task-<番号>`
- ブランチ名を checkout しない。`git push origin HEAD:refs/heads/<タスクブランチ>` で
  リモートにだけ作る。
- コミットメッセージ: Conventional Commits の日本語（`feat:` / `fix:` / `docs:` / `chore:` /
  `refactor:` / `test:`）。件名は 1 行で、本文に「何を・なぜ」を書く。
- 署名: 各コミットの末尾に次を入れる。

  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```

- PR テンプレート: 無し。本文には DoD・検証結果・下した判断（根拠と退けた代替案）を書く。

## 文章のルール

`.claude/rules/` の 5 本に従う。**とくに次の 2 本は、この作業の成果物（スクリプトの
コメント・README の追記・PR 本文）に直接効く。**

- `writing-style.md` — 文脈を知らない読者が読んで分かるように書く。理由（なぜ）を書く。
- `japanese-checks.md` — 書き終えたら 5 項目を当てる（動作が名詞に埋もれていないか、
  漢語をつないだ造語をしていないか、修飾語が中身を指定しているか、実物の名前を書いているか）。

`editing-skills.md` は「`.claude/skills/` 配下を**変更する**とき」の規定である。この作業は
スキルを検査するだけで変更しないので直接は当たらないが、**検査の基準としては使う**
（`SKILL.md` は 500 行以下、など）。
