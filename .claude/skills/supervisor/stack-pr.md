# stack PR（計画と進行状況の置き場）

**stack PR** は、stacked PR の土台 `stack/<作業名>--task-0`（空コミット 1 つ）に付けた PR である。
base ブランチ（[lead-setup.md](lead-setup.md) §5 でユーザーと決めた分岐元）へ向けて
この 1 本を作業の入口として最初に
作り、**全体の計画とタスクの進行状況を本文に持つ**。台帳（`<ベース>/ledger.md`）は git の
追跡対象外でスタックツリーを外すと消えるので（[ledger.md](ledger.md)）、
**ユーザーが GitHub 上で読める記録はこの本文である。**

```
main ← stack/<作業名>--task-0（この PR）
        ← stack/<作業名>--task-1 ← stack/<作業名>--task-2 …
```

**この土台の上に、タスク PR が 1 本ずつ載る。** タスク PR はリードが作り、
`gh stack` で stacked PR へ積む（[integration.md](integration.md)）。
**マージはユーザーが下から順に行う**（`gh stack merge`。[finish.md](finish.md) §8）。この PR が
最初にマージされ、以降は GitHub が上の PR の base を自動で張り替える。

計画をファイルにしてコミットしない。

**本文の先頭に、stacked PR であることと関連する PR の一覧を書く。** GitHub も merge box に
スタックを描くが、`gh stack` は新しいのでレビュワーが「下から順に読んでマージする」と気づけない。
`state.py render` が「この PR について」の節として書き出すので、手で書かない
（`state.json` から作るので 1 本積むたびに最新になる）。

## いつ何をするか

| 場面 | 操作 |
| --- | --- |
| [lead-setup.md](lead-setup.md) §4（タスク設計の直後） | `state.py render` → `/create-pr` に `draft=true` で作らせる |
| 1 タスクを stacked PR へ積んだ直後（[integration.md](integration.md) §2 の手順 4・5） | `state.py set` → `render` → `gh pr edit --body-file` |
| タスクを `blocked` / `failed` で打ち切ったとき | 同上（`--status` と `--reason` を入れる） |
| [finish.md](finish.md) §8（仕上げ） | `render --final` → `gh pr edit`、`stacked` が 1 件以上あれば `gh pr ready` |

ワークフローを起動したときは PR 本文を更新しない（`runId` は state.json と台帳にだけ載る）。

## 本文は `state.py render` が書き出す

**本文を手で組み立てない。** `<スクリプト>/state.py render --base <ベース>` が
`<ベース>/stack-pr-body.md` を書き出すので、それを `--body-file` で渡す。書き出される節のうち、

- **表と定型の節**（この PR について・タスク一覧・残課題・自律判断の記録）は
  `<ベース>/state.json` から作られる。
  更新は `state.py set`（[ledger.md](ledger.md)「台帳と stack PR 本文は `state.py` が書き出す」）。
- **散文の節**（概要・全体の計画と DoD・変更による挙動の変化・確認項目・検証結果）は
  `<ベース>/prose/<名前>.md` に `Write` で書く。**変わった節だけを書き直せばよい。**

`--body` の引数に本文を直接書かないのは、本文に `` `stacked` `` のようなコード表記が入るためである。
シェルの二重引用符の中ではバックティックがコマンドとして実行され（`stacked: command not found`）、
`$` で始まる語は空に展開される。`<ベース>`（`place.py base-dir --work <作業名>` が返すパス）は
自分を無視する `.gitignore` の中なので、書き出した本文は PR の差分にも出ない。

## 作る（§4）

`prose/summary.md` と `prose/plan.md` を書き、`render` してから **`create-pr` スキルに作らせる**。

```bash
<スクリプト>/state.py render --base <ベース>
```

```
/create-pr base=<base> head=stack/<作業名>--task-0 draft=true title="[supervisor] <種別>: <作業内容>" body-file=<ベース>/stack-pr-body.md
```

**`gh pr create` を直接叩かない。** PR を作る作法（push・既存 PR の有無の確認・作成後の報告）は
`create-pr` に 1 か所へ集めてある。base・タイトル・本文・draft はここで決まっているので引数で渡す
——渡した項目について `create-pr` は自分で決め直さない（`create-pr/SKILL.md`「引数で渡されたものは
決め直さない」）。**とくに `base` を渡さないと `create-pr` が履歴を遡って別の base を選び、
stacked PR の土台が違う場所に載る。**

**返ってきた PR 番号を `state.py meta --stack-pr <番号>` で入れる**（タスク PR のタイトルに入り、
以降の `gh pr edit` の宛先になる）。

- `<base>` は「起動前の確認」でユーザーと決めた分岐元。`<種別>` は `feat` / `fix` / `docs` など、
  そのリポジトリの既存 PR タイトルに倣う。Issue 番号やチケット ID を聞き出してあれば末尾に添える。
- **draft で作る**。仕上げが済むまで GitHub 側がマージを拒むので、途中の誤マージが起きない。
  **stacked PR の土台なので、これが draft のあいだは上の PR もマージできない**——ユーザーがマージを
  始められるのは §8 で `gh pr ready` に上げてからである。
- **失敗したら 1 回だけ再試行し、それでも非 0 なら止まる。** 叩いたコマンドとエラー出力を
  ユーザーに示し、[lead-setup.md](lead-setup.md) §5（権限）・§6（`TaskCreate`）に進まない。state.json と
  `stack/<作業名>--task-0` は残るので、原因が解消したら §4 のここから続けられる。
- リポジトリに PR テンプレートがあれば（`.github/PULL_REQUEST_TEMPLATE.md` など）、**その見出しを
  残したまま**下の節を足す。テンプレートの中身は `<ベース>/prose/prelude.md` に置く——`render` が
  本文の先頭にそのまま出す。

## タイトルの接頭辞

supervisor が管理している PR だと一覧で見分けられるように、3 種すべてに接頭辞を付ける。

| PR | タイトル |
| --- | --- |
| stack PR（task-0） | `[supervisor] <種別>: <作業内容>` |
| タスク PR（PR 本文エージェントが決めた形でリードが付ける） | `[supervisor #<stackPR番号> task<番号>] <種別>: <件名>` |
| 残件回収 PR（[integration.md](integration.md)「却下された残件の回収」） | `[supervisor #<stackPR番号> followup] <種別>: <件名>` |

**PR タイトルを検査する GitHub Actions があるリポジトリでは接頭辞を末尾に回す。** タイトルの
先頭が `feat:` `fix:` などの型で始まることを求める job（`amannn/action-semantic-pull-request` が
その一例）では、`[supervisor]` が先頭にあると落ちる。**どういう検査なのかは実際の workflow を
読んで判断する**——§2 で `.github/workflows/` を読み、`<ベース>/brief.md` に書いてある。
回した形はこうなる。

```
fix: パーサの境界値を直す [supervisor #100 task1]
```

`#<stackPR番号>` は PR 本文エージェントに `args.stackPr` で渡る
（[workflow-script.md](workflow-script.md)「呼び方」）。

## 本文の構成

**先頭は「この PR について」**（stacked PR であることと関連する PR の一覧）。次に**PR テンプレートの
節を畳まずに出す**（概要・変更による挙動の変化・確認項目・残課題）。この PR は人間のレビュワーも
最後に読む。読者が最初に知りたいのは「どの順に読むのか」と「この作業で何がどう変わるか」なので、
**作業の計画と記録**（全体の計画と DoD・タスク一覧・検証結果・自律判断の記録）は `<details>` に
畳んで後ろへ回す。

`state.py render` が次の形で書き出す。`<…>` の箇所が `prose/*.md` に対応する。**★ の節は
`--final` のときだけ出る**（[finish.md](finish.md) §8）。**`runId` は載らない**（worktree の
後始末と再実行に使う内部の値で、ユーザーが読む意味が無い）。

```markdown
## この PR について

これは stacked PR の土台です。base は `main` で、この PR 自身の差分は空コミット 1 つだけです。
**レビューとマージは下から順に行ってください。**    ← state.json から自動で作られる

| 位置 | PR | 内容 | 状態 |
|---|---|---|---|
| 土台 | #100（この PR） | 全体の計画と進行状況 | — |
| 1 | #102 | エラー型を整理する | stacked |
| 2 | #101 | パーサの境界値を直す | stacked |
| — | #103 | 一覧表を更新する | running |

## 概要

<この作業で何が変わるか。挙動の変化を 1〜3 行で>    ← prose/summary.md

## 変更による挙動の変化    ★

- <操作 X をすると、これまでは A だったが、これからは B になる>    ← prose/behavior.md

## 確認項目    ★

- [ ] <操作手順> を行うと <観測できる結果> になる    ← prose/checklist.md

## 残課題

- <回収しないと決めた残件>    ← prose/remaining.md
- <打ち切ったタスクとその理由>    ← state.json から自動で足される

<details>
<summary>全体の計画と DoD</summary>

<作業全体で達成する状態。タスクへの割り方の方針を数行で>    ← prose/plan.md

</details>

<details>
<summary>タスク一覧（進行状況）</summary>

| # | 件名 | tier | 依存 | PR | 積んだ位置 | 状態 | 却下した残件 |
|---|---|---|---|---|---|---|---|
| 1 | パーサの境界値を直す | standard | — | #101 | 2 | stacked | 2 |
| 2 | エラー型を整理する | standard | — | #102 | 1 | stacked | 0 |
| 3 | 一覧表を更新する | light | 1,2 | — | — | running | — |

状態と「積んだ位置」の読み方を `render` が続けて書く（base とマージの順序は先頭の節にある）。

</details>

<details>
<summary>検証結果</summary>

- <brief.md の検証コマンド一式を stacked PR の先頭で流した結果。コマンドと結果を 1 行ずつ>    ← prose/verification.md
- <外形動作を確かめた手順と、観測した結果>

</details>

<details>
<summary>自律判断の記録</summary>

### 変更した最終目標・DoD・スコープ

- <何を・なぜ・判断材料（根拠と退けた代替案）・残課題>
  ← prose/decisions.md ＋ 各タスクの decisions（state.json）

### 先送り・対象外にした作業

- <何を・なぜ・次に何をすべきか>
  ← prose/deferrals.md ＋ 各タスクの deferrals（state.json）

</details>
```

**打ち切ったタスクは書き忘れない。** `state.py set --status blocked --reason "…"` を入れておけば、
`render` が「残課題」に 1 行足す（`prose/remaining.md` に書く必要は無い）。

## 最終版の本文（§8）

`state.py render --final` にすると、進行中の節を最新にしたうえで ★ の 4 節が足される。
`prose/behavior.md` / `checklist.md` / `verification.md` / `decisions.md` / `deferrals.md` に書く。

- 「変更による挙動の変化」と「確認項目」は、**観測できる事象で書く**。コード内部の状態
  （変数・フラグ）は書かない。「正しく」「正常に」も使わない。
- **各タスク PR の本文と重複させない。** ここに書くのは作業全体で見たときの変化で、タスクごとの
  詳細はそれぞれの PR にある。
- 「自律判断の記録」には、ワークフローが返した `decisions` と `deferrals`（`state.py set` で
  入れる）、リードがコンフリクト解消で書いたコード（`prose/decisions.md` に書く。
  [integration.md](integration.md) §3）が集まる。
- `stacked` が 1 件以上なら `gh pr ready` でレビュー可能にし、マージはユーザーに渡す。
  **1 件も無ければ `ready` を叩かず draft のまま残す**（土台のブランチも消さない。あとで続きを
  頼まれたときの足場になる。[ledger.md](ledger.md) の再開手順）。

## 更新する

状態か散文を変えたら、`render` して差し替える。2 コマンドで済む。

```bash
<スクリプト>/state.py render --base <ベース>          # --final は §8 のときだけ
gh pr edit <stackPR番号> --body-file <ベース>/stack-pr-body.md
```

**本文は毎回全文が書き出される**（節の一部だけを書き換えようとすると、他の節を落とす）。
書き出しの出所は `state.json` と `prose/*.md` なので、前回の本文を読み直す必要は無い。
`gh pr edit` は購読者に通知を出さないので、更新の回数を気にしなくてよい。

ユーザーが本文に手で書き足した節が見えるときは、上書きする前にユーザーに確認する（`render` は
その節を知らないので消える。残すなら `prose/prelude.md` に写す）。

## このスキルが扱わないこと

**PR に付いた人間のレビューコメントは扱わない。** stack PR に付いたものも、タスク PR に付いた
ものも、修正と積み替えはユーザーが行う（design-notes.md「なぜ人間のレビュー後を扱わないか」）。
Claude のレビューで却下された指摘（review.json の `rejected`）をリードが片づける手順は
[integration.md](integration.md)「却下された残件の回収」にある。
