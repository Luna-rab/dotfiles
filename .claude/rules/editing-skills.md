---
paths:
  - ".claude/skills/**"
  - "**/.claude/skills/**"
---

# skills を編集するときは公式ドキュメントを参照する

`.claude/skills/` 配下の SKILL.md やその補助ファイルを作成・変更する前に、必ず公式
ドキュメント https://code.claude.com/docs/ja/skills を参照し、最新の仕様に従う。記憶や
推測で書かない（仕様は変わる）。特に次を確認する:

- 本体は簡潔に保つ。実行内容（何をするか）を述べ、方法や理由を説明しない。読み込まれた
  SKILL.md 本体はターン全体でコンテキストに留まり、毎ターン再送されトークンコストになる。
- SKILL.md は 500 行以下。詳細なリファレンス資料は補助ファイルへ移し、必要なときだけ
  読み込ませる。SKILL.md からリンクして、内容と読み込むタイミングを示す。
- `description` と `when_to_use` の書き方（Claude が自動起動を判断する材料。合計
  1,536 文字で短縮されるため主要ユースケースを前置きする）。
- frontmatter フィールド（`allowed-tools`・`disable-model-invocation`・`context: fork`
  など）の意味と、呼び出し・コンテキスト読み込みへの影響。
