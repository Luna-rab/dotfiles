---
name: create-pr
description: gh コマンドで GitHub の Pull Request を作成する。未コミット変更のコミット、release/* または master 上での新規ブランチ作成（命名規則つき）、過去のコミット履歴を辿ったマージ先の自動判定、リポジトリの PR テンプレート追従、挙動の変化を中心にした PR description 生成を行う。「PRを作って」「プルリクを出して」等の依頼で利用する。
---

# Create PR スキル

`gh` コマンドを使って Pull Request を作成する。コードの差分そのものではなく **挙動の変化** を中心に説明する PR を作る。

各ステップは順番に実行する。判断に迷う箇所はユーザーに確認し、勝手に推測で埋めない。

## 前提

- `gh` CLI がインストール済みで認証済み (`gh auth status` で確認できる)
- カレントディレクトリが対象の git リポジトリ

## 手順

### 1. 現在の状態を把握する

以下を確認する。

```bash
git status --porcelain        # 未コミット変更の有無
git branch --show-current     # 現在のブランチ名
git log --oneline -20         # 直近の履歴
```

### 2. 未コミット変更の処理

`git status --porcelain` に出力がある（=未コミット変更がある）場合:

1. **現在のブランチが `release/*` または `master`（および `main`）の場合は、コミット前に新しいブランチを作成する。** 後述の「ブランチ命名ルール」に従う。新しいブランチ名を決める際、チケット番号が不明なら**ステップ3に進む前に**ユーザーに確認する（推測しない）。
2. 変更内容を確認した上でコミットする。コミットメッセージは変更の意図がわかるものにする。

現在のブランチが `release/*` / `master` / `main` 以外なら、ブランチはそのままでコミットだけ行う。

未コミット変更が無い場合はこのステップをスキップする。

### 3. マージ先（base ブランチ）の判定

以下の順で決定する。

1. **コミット履歴を遡って `release/*` または `master` を探す。**
   現在の HEAD から到達可能なコミットのうち、`release/*` または `master`（`main`）ブランチが指しているものを探し、**直近（最も近い祖先）のもの**を base にする。

   ```bash
   # HEAD の祖先に含まれる release/* と master/main を、HEAD から近い順に列挙する
   git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/origin/ \
     | grep -E '(^|/)(release/|master$|main$)' \
     | while read ref; do
         if git merge-base --is-ancestor "$ref" HEAD 2>/dev/null; then
           # HEAD から ref までの距離（近いほど小さい）
           dist=$(git rev-list --count "$ref"..HEAD 2>/dev/null)
           echo "$dist $ref"
         fi
       done | sort -n
   ```

   上記で複数出た場合は `dist` が最小（最も近い）ものを採用する。1つに定まればそれを base にする。

2. **1で定まらない場合は、ユーザーに選んでもらう。**
   候補となる `release/*` および `master`/`main` を列挙し、どれをマージ先にするかユーザーに尋ねる。

   ```bash
   git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/origin/ \
     | grep -E '(^|/)(release/|master$|main$)' | sort -u
   ```

### 4. PR テンプレートの確認

リポジトリに PR テンプレートがあればそれに従って本文を組み立てる。以下を確認する。

```bash
ls .github/PULL_REQUEST_TEMPLATE.md \
   .github/pull_request_template.md \
   .github/PULL_REQUEST_TEMPLATE/ \
   docs/PULL_REQUEST_TEMPLATE.md \
   PULL_REQUEST_TEMPLATE.md 2>/dev/null
```

テンプレートが存在する場合は、その見出し・セクション構成を維持したまま中身を埋める。テンプレートが無い場合は次節の標準構成で本文を作る。

### 5. PR description の作成

差分を確認してから本文を書く。

```bash
git diff <base>...HEAD          # base はステップ3で決めたブランチ
git log <base>..HEAD --oneline
```

**description の方針:**

- コードの変更点を逐一説明しない。差分を見ればわかる。**挙動の変化** を中心に書く。
- コードについて触れるのは、理解が難しい箇所・注意を払うべきと判断した箇所に限る。
- チェック項目を作る場合は、**実際の挙動から観測できる変化** を具体的に書く。コード内部の状態（変数・フラグ等）は通常観測できないので書かない。
- 「正しく」「正常に」など解釈に余地のある言葉は使わない。何がどう変わるかを具体的な事象で書く。
- **チケット番号は推測しない。** ブランチ名や明示された情報から判別できない場合はユーザーに尋ねる。

テンプレートが無い場合の標準構成:

```markdown
## 概要
<このPRで何が変わるか。挙動の変化を1〜3行で>

## 変更による挙動の変化
- <操作Xをすると、これまではAだったが、これからはBになる>
- <画面/コマンド/APIレスポンスなどで観測できる差分を列挙>

## 確認項目
- [ ] <操作手順> を行うと <観測できる結果> になる
- [ ] <別の操作> を行うと <観測できる結果> になる

## 補足
<理解が難しい箇所・注意点があれば。なければ省略>
```

### 6. PR の作成または更新

まずブランチを push する。

```bash
git push -u origin "$(git branch --show-current)"
```

次に、**現在のブランチに紐づく PR が既に存在するかを確認する。**

```bash
gh pr view --json number,url,baseRefName,title 2>/dev/null
```

- **既存 PR がある場合**: 新規作成せず、その PR を更新する。本文・タイトルはステップ4・5で作り直したものに差し替える。base を変更する必要があれば `--base` も付ける。

  ```bash
  gh pr edit \
    --title "<タイトル>" \
    --body "<上で作成した本文>"
  # マージ先が変わる場合のみ: --base "<base>"
  ```

  既存の本文をテンプレート構成で上書きしてよいか不安が残る場合（手動で書き加えられた節がある等）は、上書き前にユーザーへ確認する。

- **既存 PR が無い場合**: 新規に作成する。

  ```bash
  gh pr create \
    --base "<base>" \
    --title "<タイトル>" \
    --body "<上で作成した本文>"
  ```

作成・更新後、`gh pr view --web` の URL を含め、対象の PR をユーザーに伝える。

## ブランチ命名ルール

新しいブランチを作るときは以下に従う。チケットが存在する場合は **チケット名を末尾につける**（`--` 区切り）。

| 種別 | プレフィックス | 例 |
|------|---------------|----|
| リリース | `release/` | `release/v1.23.0` |
| 機能追加 | `feature/` | `feature/add-user-roles--DVSPBASE-12345` |
| 修正 | `fix/` | `fix/role-permission-inconsistency--DVSPBASE-23456` |
| 緊急修正 | `hotfix/` | `hotfix/role-permission-inconsistency` |

- 説明部分はケバブケース（小文字・ハイフン区切り）で、変更内容が伝わる短い英語にする。
- チケット番号が不明な場合は **推測せず** ユーザーに確認する。確認の結果チケットが無いなら末尾のチケット名は省略する。

## やらない

- チケット番号・チケット名の推測（不明ならユーザーに尋ねる）
- マージ先の推測（履歴から1つに定まらなければユーザーに選んでもらう）
- コードの差分を逐一なぞる説明（挙動の変化に集約する）
- 「正しく動く」「正常に処理される」等、観測できない・解釈に幅のある表現
- コード内部状態をチェック項目にすること（観測できる挙動だけを項目にする）
