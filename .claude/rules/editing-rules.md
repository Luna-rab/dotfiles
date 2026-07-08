---
paths:
  - ".claude/rules/**"
  - "**/.claude/rules/**"
  - ".claude/CLAUDE.md"
  - "**/CLAUDE.md"
---

# rules / CLAUDE.md を編集するときは公式ドキュメントを参照する

`.claude/rules/` 配下のルールや CLAUDE.md を作成・変更する前に、必ず公式ドキュメント
https://code.claude.com/docs/ja/memory を参照し、最新の仕様に従う。記憶や推測で書かない
（仕様は変わる）。特に次を確認する:

- ルールの置き場所とスコープ（`.claude/rules/` は常時 or path-scoped、
  `~/.claude/rules/` はユーザーレベルで全プロジェクト適用）。
- `paths` frontmatter の書き方（特定ファイルにスコープすると、一致するファイルを
  触るときだけ読み込まれ、常時ロードのノイズを減らせる）。常時必要な振る舞い規則以外は
  path-scoped にできないか検討する。
- サイズの目安（CLAUDE.md は 200 行以下）、構造（見出し・箇条書き）、具体性
  （検証できる指示を書く）。
