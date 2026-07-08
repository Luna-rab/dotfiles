# 設計の理由（オンデマンド参照）

SKILL.md 本体は「何をするか」だけを書く（毎ターン再送されトークンになるため。公式の
[スキルの書き方](https://code.claude.com/docs/ja/skills)が「実行内容を述べ、方法や
理由を説明するな」と指示している）。「なぜその設計か」はここに置き、必要なときだけ読む。

## なぜ dynamic workflow か（手作りの Agent 手動起動をやめた理由）

監督者がターンごとに `Agent` ツールを手で呼ぶと、完了通知を待って次のレビュー・修正・
次段を人手で起動することになり、多数のエージェントの中間結果が監督者の文脈に積もって
汚れる。dynamic workflow はループ・分岐・中間結果をスクリプト側が保持し、監督者の文脈
には最終結果だけを返す。並列度も高い（1 run あたり最大 16 並列・1000 エージェント）。

## なぜ agent teams ではなく dynamic workflow か

competing 案は agent teams（team lead と teammate が共有タスクリストと直接メッセージで
協調する公式機能）。依存管理を内蔵し差し戻しループにも向くが、退けた理由は次の 2 つ:

- **競合回避の粒度が足りない**: teammate 間の競合回避はタスク奪取レベル（file lock）で、
  同じファイルを触る teammate 同士のファイル編集は分離されない。結局 worktree が要る。
  dynamic workflow は `isolation: "worktree"` をそのまま持つ。
- **安定性**: agent teams は実験機能・既定無効（`v2.1.178`,
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`）で、タスク状態のラグも文書化されている。
  中核スキルの土台にするには時期尚早。

差し戻しループ自体は永続する teammate と直接メッセージを持つ agent teams の方が自然に
書ける。agent teams が安定したら再検討する。

## worktree の注意（起点と可視性）

`isolation: "worktree"` の worktree には 2 つの癖があり、SKILL.md とテンプレートの手順は
これを前提にしている:

- **デフォルトブランチから分岐する**（topic や親セッションの HEAD ではない）。topic に
  マージ済みの変更を前提にするタスクは、起点に必要なファイルが無い。実装エージェントの
  冒頭で topic または前段の feature ブランチを取り込ませる。
- **ワークフローランタイムが管理し、監督者や他ステージの作業ツリーからは直接見えない**
  （変更が無ければ自動でクリーンアップされる）。ステージ間の受け渡しと監督者のマージ前
  検証は、worktree ではなく push 済みの feature ブランチ経由で行う。

## ワークフロー実行中はユーザー確認ができない

ワークフローは実行中にユーザー入力を受け付けない（エージェントの権限プロンプトだけが
実行を一時停止できる）。そのため「確認方針」の判断はワークフロー起動前に解消し、途中で
確認が要る事態はタスクを未完了で返させて監督者が受け取る。段の区切りはワークフローを
分けて表現する（公式ドキュメントの「ステージ間の署名のために各ステージを独自の
ワークフローとして実行する」に対応）。

## サブエージェントの権限（起動前に解消する）

ワークフロー内のサブエージェントは常に acceptEdits で走り、ファイル編集は自動承認される
が、シェルコマンド・git・`gh`・許可リスト外の MCP は許可リストに無いと実行中に権限
プロンプトを出し、ワークフローを止める。実装・レビュー・修正が使うコマンドは起動前に
許可リストへ入れておく。

## 出典

- 調査（`/deep-research`、一次情報を 3-0 で検証）:
  https://code.claude.com/docs/en/workflows ,
  https://code.claude.com/docs/en/sub-agents ,
  https://code.claude.com/docs/en/agent-teams
- dynamic workflows の仕様: https://code.claude.com/docs/ja/workflows
- スキルの書き方: https://code.claude.com/docs/ja/skills
