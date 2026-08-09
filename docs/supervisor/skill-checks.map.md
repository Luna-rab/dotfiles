# skill-checks コードベースの入口（map）

**ここに書いてあるのは入口だけ。** 該当箇所の特定と構造の把握は、実装 subagent が自分で行う。

## 検査の対象

`.claude/skills/` に 4 スキル。形がそろっていないので、検査は形の違いを吸収する必要がある。

| スキル | 構成 |
| --- | --- |
| `create-pr` | `SKILL.md` のみ |
| `dig` | `SKILL.md` のみ |
| `supervisor` | `SKILL.md` ＋ 補助 md 6 本 |
| `team-supervisor` | `SKILL.md` ＋ 補助 md 7 本 ＋ `scripts/` |

frontmatter に現れるフィールドは 4 スキルの和集合で 7 種（`name` / `description` /
`when_to_use` / `argument-hint` / `disable-model-invocation` / `allowed-tools` / `hooks`）。
`.claude/skills/.gitkeep` があるので、ディレクトリ走査でこれを拾わないこと。

## 書き方の前例

- `.claude/skills/team-supervisor/scripts/subagent-stop.sh` — フックから呼ばれるスクリプト。
  stdin の JSON を `sed` で読み、終了コードで結果を返す。**task2 が外から叩く対象。**
- `.claude/scripts/statusline.sh` — stdin の JSON を `jq -r` で読む。`#!/bin/bash`、
  英語 1 行の概要コメント、`# --- ... ---` のセクション区切り、小さな関数に切る書き癖。
  `set -euo pipefail` は使っていない。

## 検査の基準の出どころ

- `.claude/rules/editing-skills.md` — `SKILL.md` は 500 行以下、補助ファイルへリンクする、
  frontmatter フィールドの意味。**「何を検査すべきか」はここから来る。**
- `.claude/rules/japanese-checks.md` — 「SKILL.md は 500 行以下」を固有スタイルとして明記。

## 注意すべき仕掛け

- **ルートの `.gitignore` は許可リスト方式。** `/*` と `/.**` で全無視し、`!` で個別に復活させる。
  新しいディレクトリを作っても既定では追跡されない。`.claude/**` / `.github/**` / `docs/**` は
  復活済みなので、そこに置くぶんには問題ない。**それ以外の場所に成果物を置かない。**
- `install.sh` の `link_claude_config()` が `.claude/scripts` を `~/.claude/scripts` へ symlink
  する。リポジトリ側の編集がそのまま反映される。
- `.claude/skills/team-supervisor/scripts/.gitignore` に Python のテンプレート
  （`__pycache__/` など）が入っている。`.claude/scripts/` には無いので、Python を置くなら
  生成物が追跡対象にならないか確かめる。
