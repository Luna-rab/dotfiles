# `TeammateIdle` フックの設置

## 何を防ぐか

公式ドキュメントのトラブルシューティングに 2 つの失敗が挙がっている。

> **Agents stopping early**: Teammates may stop after encountering errors instead of recovering.
> **The lead can stop early too**, deciding the team is finished before all tasks are actually complete.

どちらも「ユーザーが気づいて言い直す」前提で書かれていて、自律で走らせる設計と噛み合わない。
とくに 2 つ目は、**リード自身の判断ミスをリード自身に検出させる**ことになり担保にならない。
フックはリードの外にあるので、そこを埋められる。

## 何ができて、何ができないか

フックの標準入力に来るのは `teammate_name` と `team_name` の 2 つだけで、タスクの状態は
来ない。だから確かめられるのは**外部の実物**に限られる。

このフックは **タスクブランチが `origin` に push されているか**を見る。承認まで進んだ
サブリーダーは必ず push している（[subleader-prompt.md](subleader-prompt.md) §3・§8）ので、
push が無いままアイドルに入る＝未完、と判定できる。

終了コード 2 で終了を拒否し、標準エラー出力が teammate に見える。

## 設置場所は SKILL.md の frontmatter（settings.json ではない）

`settings.json` に置くと**全プロジェクト・全セッションの `TeammateIdle` で発火する**。このスキルと
無関係な teammate まで巻き込むので、SKILL.md の `hooks` frontmatter に置く。公式ドキュメント:

> These hooks are **component-scoped and run only when that component is active**, cleaning up when
> it completes.

SKILL.md の frontmatter に書いてある（設定形式は `settings.json` と同じ）。

```yaml
hooks:
  TeammateIdle:
    - hooks:
        - type: command
          command: ~/.claude/skills/team-supervisor/scripts/teammate-idle.sh
```

有効なのは**このスキルを呼び出してから、そのセッションが終わるまで**。スキルの内容は呼び出すと
セッションの残りの間コンテキストに留まるので（公式「スキルコンテンツのライフサイクル」）、
フックも同じ期間だけ生きる。呼び出していないセッションでは存在しない。

スクリプトは `~/.claude/skills/team-supervisor/scripts/teammate-idle.sh`
（`~/.claude/skills` は dotfiles の `.claude/skills` への symlink）。実行権限を付ける。

```bash
chmod +x ~/.claude/skills/team-supervisor/scripts/teammate-idle.sh
```

**スキルを呼び出したセッションの中では、このスキルが立てたのではない teammate にも発火する。**
スクリプト側で teammate 名が `task` で始まるものだけを対象にして二重に絞っている（下表の 1 行目）。

## 振る舞い

| 状況 | 終了コード | 何が起きるか |
| --- | --- | --- |
| teammate 名が `task` で始まらない | 0 | 素通し（他のスキルや手動の teammate を邪魔しない） |
| git リポジトリでない | 0 | 素通し |
| `blocked-<名前>` の目印ファイルがある | 0 | 素通し（リードへ報告済み） |
| タスクブランチが push されている | 0 | 素通し。カウンタを消す |
| push が無い（1〜3 回目） | 2 | 終了を拒否し、続けるか blocked を報告するよう伝える |
| push が無い（4 回目） | 1 | 打ち切る。ユーザーにだけ警告を出す |

**無限ループを避けるため 3 回で打ち切る。** 本当に進められない teammate を永久に止め続けると、
トークンを使い続けるだけになる。

## blocked の目印

サブリーダーがリードへ blocked を報告したあと、次を実行するとこの確認が止まる。

```bash
touch "$(git rev-parse --git-common-dir)/team-supervisor/blocked-<teammate 名>"
```

目印とカウンタは `.git/team-supervisor/` に置く。作業ツリーに出ないので、差分を汚さない。

## 後始末

作業が終わったら、リードが目印を消す。

```bash
rm -rf "$(git rev-parse --git-common-dir)/team-supervisor"
```

## 動作確認

```bash
echo '{"teammate_name":"task1","team_name":"session-abc12345"}' |
  ~/.claude/skills/team-supervisor/scripts/teammate-idle.sh; echo "exit=$?"
```

`topic/...--task1` が origin に無ければ `exit=2` と、続けるよう促す文が出る。
