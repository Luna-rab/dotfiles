# `SubagentStop` フックの設置

## 何を防ぐか

公式ドキュメントのトラブルシューティングに、こう挙がっている。

> **Agents stopping early**: Teammates may stop after encountering errors instead of recovering.

これは「ユーザーが気づいて言い直す」前提で書かれていて、自律で走らせる設計と噛み合わない。
リードが気づけばよいが、**リード自身の判断ミスをリード自身に検出させる**ことになり担保にならない。
フックはリードの外にあるので、そこを埋められる。

## 何ができて、何ができないか

フックの標準入力に来るのは `agent_id` / `agent_type` / `agent_transcript_path` /
`last_assistant_message` などで、**タスクの状態は来ない**。だから確かめられるのは
**外部の実物**に限られる。

このフックは **タスクブランチが `origin` に push されているか**を見る。承認まで進んだ
サブリーダーの配下は必ず push している（[implementation-prompt.md](implementation-prompt.md) §1）
ので、push が無いまま終わろうとする＝未完、と判定できる。

確かめるブランチ名は、リードが spawn 直後に `.git/team-supervisor/branch-<agentId>` へ書いた
ものを読む（`SKILL.md` §7）。

**このフックは全 subagent の終了で発火する**（実装・レビュー・Explore・`/code-review` の子も
含む）。登録ファイルが無いものは素通しする、という絞り込みで対象をサブリーダーだけに限る。

終了コード 2 で終了を拒否し、標準エラー出力が subagent に見える。

## 設置場所は SKILL.md の frontmatter（settings.json ではない）

`settings.json` に置くと**全プロジェクト・全セッションの `SubagentStop` で発火する**。このスキルと
無関係な subagent まで巻き込むので、SKILL.md の `hooks` frontmatter に置く。公式ドキュメント:

> These hooks are **component-scoped and run only when that component is active**, cleaning up when
> it completes.

SKILL.md の frontmatter に書いてある（設定形式は `settings.json` と同じ）。

```yaml
hooks:
  SubagentStop:
    - hooks:
        - type: command
          command: ~/.claude/skills/team-supervisor/scripts/subagent-stop.sh
```

有効なのは**このスキルを呼び出してから、そのセッションが終わるまで**。スキルの内容は呼び出すと
セッションの残りの間コンテキストに留まるので（公式「スキルコンテンツのライフサイクル」）、
フックも同じ期間だけ生きる。呼び出していないセッションでは存在しない。

スクリプトは `~/.claude/skills/team-supervisor/scripts/subagent-stop.sh`
（`~/.claude/skills` は dotfiles の `.claude/skills` への symlink）。実行権限を付ける。
`gh-review.py`・`place.py`・`state.py`・`verify.py`・`worktree.py` もエージェントがパスを直に
叩くので、同じく実行ビットが要る。

```bash
chmod +x ~/.claude/skills/team-supervisor/scripts/{subagent-stop.sh,gh-review.py,place.py,state.py,verify.py,worktree.py}
```

## 振る舞い

| 状況 | 終了コード | 何が起きるか |
| --- | --- | --- |
| `agent_id` が来ない | 0 | 素通し |
| git リポジトリでない | 0 | 素通し |
| `branch-<agentId>` の登録が無い | 0 | 素通し（サブリーダー以外の subagent を邪魔しない） |
| `blocked-<agentId>` の目印がある | 0 | 素通し（リードへ報告済み、または再開を打ち切り済み） |
| タスクブランチが push されている | 0 | 素通し。カウンタを消す |
| push が無い（1〜3 回目） | 2 | 終了を拒否し、続けるか blocked を報告するよう伝える |
| push が無い（4 回目） | 1 | 打ち切る。ユーザーにだけ警告を出す |

**無限ループを避けるため 3 回で打ち切る。** 本当に進められない subagent を永久に止め続けると、
トークンを使い続けるだけになる。

## `.git/team-supervisor/` に置くファイル

作業ツリーに出ないので差分を汚さない。パスは
`$(git rev-parse --path-format=absolute --git-common-dir)/team-supervisor` で求める
（worktree の中からでも共有の `.git` を指す）。

| ファイル | 誰が書くか | 用途 |
| --- | --- | --- |
| `branch-<agentId>` | リード（spawn 直後・`state.py branch`） | このフックが実在を確かめるブランチ名。対象の絞り込みも兼ねる |
| `idle-count-<agentId>` | このフック | 押し戻した回数（3 回で打ち切り） |
| `resume-count-task<番号>` | リード（`state.py resume` / `state.py clear`） | `SendMessage` で再開した回数（3 回で打ち切り） |
| `blocked-<agentId>` | サブリーダーまたはリード（`state.py block`） | 押し戻しを止める目印 |
| `base/<作業名>/{brief,map,ledger}.md` | リード（`place.py base-dir` で場所を得て書く） | ベース 3 資料。サブリーダーとレビュアーが `--require` 付きで読む（[ledger.md](ledger.md)） |

上の 4 つは**このディレクトリ直下のファイル**、ベース資料は**`base/` サブディレクトリ**に置く。
`state.py init` が消すのは直下のファイルだけなので、この分け方でベース資料が残る。

リードはこれらをシェルで直に書かず `state.py` のサブコマンドを通す。パスの求め方を
1 か所に閉じ、`resume-count-task<番号>` では上限 3 との比較を終了コードで返させて、リードが
比較を飛ばせないようにするため（`SKILL.md` §7）。

## 後始末

作業が終わったら、リードがディレクトリごと消す。**台帳も一緒に消える**ので、最終 PR がマージ
されるまでは消さず、消してよいかユーザーに確認する（`SKILL.md` §8）。

```bash
rm -rf "$(git rev-parse --path-format=absolute --git-common-dir)/team-supervisor"
```

リードはタスク登録の前にも直下のファイルを消す（`SKILL.md` §6 の `state.py init`）。前回の
実行が残したカウンタ・目印・ブランチ登録が新しい実行の判定を狂わせないようにする（リードが
落ちて後始末できなかった場合の備え）。**`state.py init` はディレクトリごと消してはならない**——
ベース資料は同じディレクトリの `base/<作業名>/` にあり、`state.py init` はそれを書いた後に走る。

## 動作確認

```bash
echo '{"hook_event_name":"SubagentStop","agent_id":"affffffffffffffff"}' |
  ~/.claude/skills/team-supervisor/scripts/subagent-stop.sh; echo "exit=$?"
```

登録ファイルが無いので `exit=0`（素通し）になる。押し戻しを見たいときは、先に
`printf '%s' "no-such-branch" > "$(git rev-parse --path-format=absolute --git-common-dir)/team-supervisor/branch-affffffffffffffff"`
を実行してから同じコマンドを流すと `exit=2` になる。確認後は登録ファイルを消す。
