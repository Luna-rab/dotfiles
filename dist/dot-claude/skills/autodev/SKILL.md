---
name: autodev
description: >-
  1 つの指示を、計画・実装・テスト・レビューを経て stacked PR まで無人で進める autodev の driver を
  起動し、driver が出す質問をユーザーに渡して回答を届け、終わったら結果を報告する。ステージの進行と
  合否は driver の中の統括とドメインが決めるので、このスキルはステージを務めず、質問にも自分で答えない。
when_to_use: >-
  ユーザーが `/autodev` と打ったとき、または「この作業を実装から PR まで自律で進めてほしい」
  「人が見ていなくても進むようにしてほしい」と頼んだとき、走っている autodev のランの質問に
  答えたい・状態を知りたい・ランを片付けたいと言ったときに使う。受入条件が書けるだけの大きさがある
  作業に使う。1 ファイルの小さな修正には重すぎる。
disable-model-invocation: true
---

# autodev

**作り直しの途中である。** 新しい作りに書き直したが、本物の claude・gh でまだ確かめていない。

あなたは driver の外にいて、役目は 3 つだけである: driver を起動する・質問をユーザーに渡す・回答を driver に届ける。
用語の意味は [GLOSSARY.md](GLOSSARY.md) にある。

入口は `~/.claude/skills/autodev/scripts/autodev.py`（以下 `autodev.py`）。PATH に無いので、毎回この絶対パスで呼ぶ。
結果の JSON は標準出力に、知らせと落ちた理由は標準エラーに出る。

## 1. 起動する

次の 3 つを確定する。足りないものはユーザーに聞き、推測で埋めない。

| 引数 | 決め方 |
| --- | --- |
| `--name` | ラン名。英小文字・数字・`-` で 1〜49 字。`-` で始めず、`--` と末尾の `-` を含めない。指示の主題から付ける |
| `--repo` | 対象リポジトリ。新しいランでは必須 |
| `--instruction` | 受入条件が一意に定まる文。長ければ `--instruction-file <パス>`（`-` で標準入力） |
| `--base` | ユーザーが指定したときだけ付ける。省くと origin の既定ブランチ |

```bash
~/.claude/skills/autodev/scripts/autodev.py run --name <ラン名> --repo <リポジトリ> --instruction "<指示>"
```

**Bash の `run_in_background` で走らせる**（1 タスクで 30 分以上かかる）。終わると終了コードが届く。

## 2. 待っている間

ランディレクトリは、`run` が標準エラーに出す「記録: <パス>」である（既定は `~/.local/state/autodev/<ラン名>`）。
その `questions/` を Monitor で見る（`timeout_ms` は上限にし、切れたら張り直す）。

```bash
dir=<ランディレクトリ>/questions; seen=""
while true; do
  cur=$(grep -l '"status": "open"' "$dir"/*.json 2>/dev/null | sort)
  comm -13 <(echo "$seen") <(echo "$cur"); seen=$cur; sleep 2
done
```

質問のファイル `questions/<質問 ID>.json` の欄は `question`（ID）・`body`・`status`（`open` / `answered` / `withdrawn`）・
`answer`・`reason`（取り下げた理由）。新しく `open` になったら、すぐ `body` をユーザーに渡す。
**質問にはあなたが答えず、ユーザーに聞く。** 回答は次で置く。

```bash
~/.claude/skills/autodev/scripts/autodev.py answer --name <ラン名> --question <質問 ID> --answer "<回答>"
```

driver が走っていれば、置いた回答をそのまま受け取る。呼び直すのは `run` が 4 で終わった後だけ。

取り下げた質問に答えると、`answer` は 1 で落ち、標準エラーに取り下げた理由が出る。ユーザーに
「その質問は取り下げられた」と理由を添えて伝え、次の質問を待つ。答え済み・無い質問・空の回答も 1 で落ちる。

状態を知りたいときは `autodev.py status --json --name <ラン名>` を読む。形は
[redesign/ADDENDUM.md](redesign/ADDENDUM.md) の §12「status --json の形」にある。

## 3. 終わったとき

| コード | すること |
| --- | --- |
| 0 | `status --json` の `tasks[]` で、`kind` が `implementation` のものを `status` で分けて報告する: `stacked`（積んだ）・`dropped`（止めた）・`discarded`（破棄した）。概要 PR は `stack.overview.pr`。0 は「全部積んだ」ではない。マージしない |
| 4 | 回答待ちで、進められるタスクが無い。`status --json` の `questions[]` をユーザーに渡し、答えを `answer` で置いてから `run --name <ラン名>` を呼び直す |
| 3 | パニック（利用枠の上限など。SIGTERM・SIGINT で止めたときも 3）。`run` の標準エラーとランディレクトリの `logs/` で原因を確かめてユーザーに伝え、原因が消えたら `run --name <ラン名>` を呼び直す |
| 1 | 起動できなかった。標準エラーの理由をユーザーに伝える。直せるもの（認証・gh stack・リポジトリの設定・ラン名）はユーザーと直してから呼び直す |

呼び直すときは `--instruction` を付けない（1 で止まる）。`--repo`・`--base` は省くか、最初と同じ値にする。
同じランの driver が走っている・前の driver が起こしたプロセスが生きている、で 1 になったら、終わるのを待つかユーザーに聞く。

## 4. 片付ける

どちらも消したものは戻せない。**使う前に、何が消えるかをユーザーに示して確かめる。**

| コマンド | 使う場面 | 消すもの |
| --- | --- | --- |
| `autodev.py clean --name <ラン名>` | 終了コード 0 で終えたランの worktree を外す | worktree だけ。ランディレクトリの記録は残す |
| `autodev.py purge --name <ラン名>` | やめたラン・もう見ないランを消す | 手元の worktree・ブランチ・ランディレクトリ。PR とリモートのブランチは残る |

`--force` は、止める理由（`clean` なら終えていない・回答待ち・パニック、`purge` なら走っている実行・push していない
コミット）があっても消す。コミットしていない変更や、呼び直して続ける道を失う。ユーザーが承知したときだけ付ける。
driver が走っている間は `--force` でも消さない。

## 5. しないこと

- ステージ（計画・テスト作成・実装・レビュー・ジャッジなど）を自分で務める
- 合否を判断する・積まれていないタスクの PR を自分で作る
- ランディレクトリ（`events.db`・`questions/` など）を書き換える。回答は `answer` でだけ届ける
- 統括の代わりに質問に答える
- `gh pr merge` / `gh stack merge` を呼ぶ
- `ask` を呼ぶ（計画ステージが使う）

線引きの理由は [redesign/ARCHITECTURE.md](redesign/ARCHITECTURE.md) の §3・§11 にある。
