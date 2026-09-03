# レビュー記録（review.json）の手順と書式

レビュー・実装・裁定の各エージェントとリードが共通で使う。**JSON を直接書かず、必ず次の
スクリプトを引数付きで呼ぶ。**

```
<スクリプト>/review.py <サブコマンド> [引数]
```

書式の検査（rating の綴り、許されない status 遷移、コメント無しの status 変更）はスクリプトの
中に閉じてある。呼ぶ側は中身だけを渡す。

## 目次

どの節に何が書いてあるか。必要な節だけ読めばよい。

- なぜ GitHub ではなくファイルに置くか
- 置き場と形
- 役割ごとの使い分け
- ラウンドの先頭で記録を作る（レビューエージェント）
- レビューを立てる（レビューエージェント）
  - location の書き方
- コメントを足す（実装・レビュー・裁定）
- status を動かす（裁定エージェント）
- 一覧する（全員）
- スレッドの規則
- スクリプトが拒むこと

## なぜ GitHub ではなくファイルに置くか

**PR はレビューが全件決着してから作る。** つまりレビューが走っている間、GitHub 側にはブランチ
しか存在せず、指摘を置く先が無い。そこでレビューはタスクごとのファイルに閉じ込め、決着した
状態だけを PR として外に出す（理由と退けた代替案は design-notes.md
「なぜレビューを review.json に置くか」と「なぜ PR を最後に作るか」）。

**人間が読むのは PR だけである。** review.json は往復の作業記録で、ユーザーに見せる成果物では
ない（スタックツリーの中にあり、git の追跡対象にも入らない）。

## 置き場と形

`<ベース>/notes/task<番号>/review.json`。**タスクごとに 1 本**で、別のタスクのワークフローは
別のファイルを触る。

```json
{
  "r1": {
    "reviewer": "review:normal",
    "rating": "must-fix",
    "location": "src/core/parser.rs:42",
    "review": "境界値 len == 0 のとき slice[0] で panic する。",
    "status": "open",
    "threads": [
      {
        "thread_id": "t1",
        "comments": [
          {"commenter": "impl:a", "comment": "早期 return を追加 (a1b2c3d)"},
          {"commenter": "judge", "comment": "parser.rs:44 を確認。cargo test parser:: 通過"}
        ],
        "transition": {"from": "open", "to": "closed"}
      }
    ]
  }
}
```

| フィールド | 中身 |
| --- | --- |
| キー（review-id） | `r1` `r2` … **スクリプトが振る**。自分で決めない |
| `reviewer` | 立てた役割。`review:normal` / `review:adversarial` |
| `rating` | `must-fix` / `should-fix` / `nit`（定義は [review-prompt.md](review-prompt.md) §5）。**立てた後は誰も変えられない** |
| `location` | 指摘の場所（下記） |
| `review` | 指摘の本文。何が・どういう入力や状態で・どう失敗するか |
| `status` | `open` / `closed` / `rejected`。動かせるのは裁定だけ |
| `threads` | 「修正 → 判定」の往復。1 往復が 1 本 |
| `transition` | そのスレッドで起きた status の遷移。判定が付いていないスレッドには無い |

## 役割ごとの使い分け

| 役割 | 呼ぶサブコマンド | 読む範囲 |
| --- | --- | --- |
| `review:normal` / `review:adversarial` | **`init`（ラウンドの先頭で必ず）** / `new` / `comment` | `list`（**open だけ**） |
| `impl:a` / `impl:b` | `comment` | `list`（**open だけ**） |
| `judge` | `comment` / `status`（`--commenter judge`） | `list --all`（全件） |
| リード | — | `list --require-empty`（stacked PR へ積む前の確認） |

**レビュアーと実装は closed / rejected を読まない。** 却下された指摘が目に入らないことで、次の
ラウンドのレビュアーが差分だけを見て判断でき、裁定が誤って却下した `must-fix` を拾い直せる
（design-notes.md「なぜレビュアーに却下を申し送りしないか」）。

## ラウンドの先頭で記録を作る（レビューエージェント）

```bash
<スクリプト>/review.py init --dir <ベース>/notes/task<番号>
```

**指摘を読み書きする前に、必ずこれを呼ぶ。** 既にファイルがあれば中身に触らない（何度呼んでも
同じ結果になる）。

なぜ要るか。指摘 0 件で終わったラウンドは 1 件も書き込まないので、**ファイルの中身を見ても
「走ったが指摘なし」と「起動しなかった」「`--dir` を打ち間違えた」の 3 つを区別できない。**
リードが積む前に叩く `list --require-empty` は、レビューが決着したことを確かめる唯一の門
なので、ここを取り違えると未レビューのブランチが stacked PR に入り、人間のレビューに出てしまう。
そこで**ファイルの実在**を「レビューが走った」の証拠に使い、`init` でその証拠を残す。

## レビューを立てる（レビューエージェント）

```bash
<スクリプト>/review.py new \
  --dir <ベース>/notes/task<番号> \
  --reviewer review:normal \
  --rating must-fix \
  --location src/core/parser.rs:42 \
  --review-file /tmp/review.md
```

- `--review` に直接書いてもよいが、**改行や記号を含む本文はファイル（`--review-file`、`-` で
  標準入力）で渡す**。シェルの引用符でバックティックが実行される事故を避けるため。
- 返るのは振られた review-id と、そのタスクの status ごとの件数。
- **指摘が 0 件なら何も呼ばない。** 「走ったが指摘なし」と「起動に失敗した」の区別は、
  エージェントの返り値（`schema`）で行う（[review-prompt.md](review-prompt.md) §10）。

### location の書き方

| 状況 | 書き方 |
| --- | --- |
| 該当行を特定できる | `src/core/parser.rs:42` |
| 複数行にまたがる | `src/core/parser.rs:42-47` |
| 行は特定できないが、ファイルは分かる | `src/core/parser.rs` |
| 複数ファイルにまたがる / 差分の外 | 最も関係の深いファイルを書き、**本来の対象は `--review` の本文に書く** |

## コメントを足す（実装・レビュー・裁定）

```bash
<スクリプト>/review.py comment \
  --dir <ベース>/notes/task<番号> --id r1 \
  --commenter impl:a \
  --comment "早期 return を追加 (a1b2c3d)"
```

- **実装は直したらコメントを残す。** 何をどう直したか、対応するコミットの SHA を書く。
- **レビュアーは再レビューで所見を残す。** 「まだ直っていない」「別の経路が残っている」など。
  **status は動かせない**（動かすのは裁定）。
- **status は変わらない。** コメントだけでは open のままである。

## status を動かす（裁定エージェント）

```bash
<スクリプト>/review.py status \
  --dir <ベース>/notes/task<番号> --id r1 \
  --commenter judge --to closed \
  --comment "parser.rs:44 を確認。cargo test parser:: 通過"
```

- **`--commenter judge` は必須。** ほかの役割を渡すとスクリプトが拒む。自分が受けた指摘を
  自分で閉じられると、「open が 0 件」が自己承認になるためである。
- **コメントは必須。** 空だとスクリプトが拒む。なぜ閉じたかが残らないと、次のラウンドの裁定も、
  残件を拾うリードも判断の根拠を追えない。
- 動かせるのは 4 つだけで、それ以外はスクリプトが拒む。

| 遷移 | 意味 |
| --- | --- |
| `open → closed` | 指摘は正しく、**差分を自分で読んで**対応済みだと確かめた |
| `open → rejected` | 指摘が誤り／重複（統合先の review-id を書く）／このタスクでは直さない残件 |
| `closed → open` | 畳んだ後に再発が見つかった |
| `rejected → open` | 却下を覆す新しい根拠が出た |

**`closed` と `rejected` を混ぜない。** リードは決着した review を読んで残件をまとめるので、
取り違えると残件表が狂う。

## 一覧する（全員）

```bash
# open だけ（レビュアー・実装）
<スクリプト>/review.py list --dir <ベース>/notes/task<番号>

# 全件（裁定）
<スクリプト>/review.py list --dir <ベース>/notes/task<番号> --all

# stacked PR へ積む前の確認（リード）。open が 1 件でもあれば終了コード 1
<スクリプト>/review.py list --dir <ベース>/notes/task<番号> --require-empty
```

`--reviewer` / `--rating` で絞れる。出力は `counts`・`reviews`（配列）・`exists`（review.json が
既にあるか）。**件数で判断するときは出力の `counts` を使う**（自分で数え直さない）。

| `counts` のキー | 中身 |
| --- | --- |
| `open` / `closed` / `rejected` | その status の件数 |
| `open_must_fix` | open のうち rating が `must-fix` のもの。**裁定が `schema` の `openMustFix` に入れる値である**（ワークフローの無進捗判定がこれを使う） |

`--require-empty` は 2 つの理由で非 0 になる。**どちらも「積んでよい」ではない。**

| 終了コード 1 になる場面 | 意味 |
| --- | --- |
| review.json が無い | レビューが 1 度も走っていないか、`--dir` が違う（`init` を呼んだラウンドは必ずファイルがある） |
| `open` が 1 件以上 | まだ決着していない |

終了コードを読むときは**パイプに繋がない**（`| head` に繋ぐと `$?` が `head` のものになる）。

## スレッドの規則

**1 スレッド = 「修正 → 判定」の 1 往復。** どのスレッドに足すかはスクリプトが決めるので、
呼ぶ側は指定しない。

- 判定（`transition`）が付いていないスレッドがあれば、そこに足す
- 判定が付いている（＝その往復は終わっている）なら、**新しいスレッドを立てる**
- スレッドが 1 本も無ければ、新しく立てる

```text
r2 (must-fix, open)
  t1: impl:a 「修正した」
      judge  「別の経路が残っている」       ← 判定なし。往復は続いている
  t2: impl:a 「経路も直した」
      judge  「確認した」 transition: open → closed
```

判定が付いていないスレッドがあっても新しく立てたいときだけ `--new-thread` を付ける。

## スクリプトが拒むこと

呼ぶ側が書き間違えても止まるようにしてある。

- `reviewer` が `review:normal` / `review:adversarial` 以外（**裁定も実装もレビューを立てられない**）
- `commenter` が 5 つの役割以外
- **`judge` 以外による `status`**（レビュアーと実装は status を動かせない）
- `rating` が 3 種類以外
- 許されない status 遷移（`closed → rejected`、同じ status への遷移など）
- **コメントの無い status 変更**、空白だけのコメント
- 本文の無いレビュー、`location` の無いレビュー
- 存在しない review-id への操作（あるものの一覧を出して止まる）
- **review.json が無い状態での `list --require-empty`**（「レビューが走った証拠が無い」を
  「open 0 件」と読ませない）
