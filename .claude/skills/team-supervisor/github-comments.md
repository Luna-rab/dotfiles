# GitHub レビューコメントの手順と書式

レビュー subagent・実装 subagent・サブリーダー・リードが共通で使う。**GraphQL を直接書かず、
必ず次のスクリプトを引数付きで呼ぶ。**

```
~/.claude/skills/team-supervisor/scripts/gh-review.py <サブコマンド> [引数]
```

GraphQL の組み立てと JSON のエスケープはスクリプトの中に閉じてある。役割タグ・重大度・状態語の
書式もスクリプトが組み立てるので、呼ぶ側は中身だけを渡す。

## なぜ本文ではなくスレッドに書くか

指摘をレビュー本文（サマリ）に書くと、解決済みかどうかを機械的に確かめられない。**すべての
指摘をレビュースレッドとして投稿すれば、`unresolved == 0` が承認の条件になる。**

## 貼り付け先を 3 段階で選ぶ

| 段階 | 条件 | findings に書くもの |
| --- | --- | --- |
| 1 | 該当行を特定できる | `path` と `line`（複数行なら `startLine` も） |
| 2 | 行は特定できないが、対象ファイルが diff に含まれる | `path` だけ（`line` を書かない）→ ファイル単位になる |
| 3 | 対象が diff 外のファイル、または複数ファイルにまたがる | **diff 内で最も関係の深いファイル**を `path` に、本来の対象を `target` に書く |

**指摘をサマリ本文に落とさない。** 段階 3 で必ず diff 内のファイルを選ぶ（diff 外のパスを
指定すると GitHub が 422 で拒む）。

## 隠しメタデータ（機械が読む行）

スクリプトは投稿する本文の 1 行目に、GitHub 上で表示されない HTML コメントを置く。

```
<!-- team-supervisor {"kind":"finding","role":"review:normal","severity":"must-fix","category":"correctness"} -->
**[review:normal]** `must-fix` / correctness

境界値 `len == 0` のとき `slice[0]` で panic する。
```

`kind` は `finding`（指摘スレッドの先頭）/ `review`（レビューのサマリ本文）/ `reply`（返信）。
`threads` の `role` と `severity`、`gate` の `--require-roles` の判定はこの行から読む。
表示用の `**[役割]**` タグは**人間が PR 画面で誰の指摘かを読むために残してある**もので、
機械はこちらを見ない（タグの書式を変えてもパースは壊れない）。

## 役割タグ

`--role` に渡す。スクリプトが隠しメタデータの `role` と、表示用の `**[役割]**` 行に置く。

| タグ | 誰 | 使える状態語（`reply` の `--status`） |
| --- | --- | --- |
| `review:normal` | 通常レビュー subagent | `still-open` / `resolved` |
| `review:adversarial` | 敵対的レビュー subagent | 同上 |
| `review:conflict` | コンフリクト解消レビュー subagent | 同上 |
| `impl:a` / `impl:b` | 実装 subagent（`b` は差し替え後） | `fixed` / `partial` / `wont-fix` / `disputed` / `deferred` |
| `subleader:task<番号>` | サブリーダー | `upheld` / `overruled` |
| `lead` | リード | `note` |

役割に許されない状態語を渡すとスクリプトが拒む。

## レビューを投稿する（レビュー subagent）

findings を JSON 配列で書き、`post` に渡す。**1 回の呼び出しで、PENDING レビューの作成 →
行単位スレッド → ファイル単位スレッド → submit まで済む。**

```bash
cat > /tmp/findings.json <<'JSON'
[
  {
    "severity": "must-fix",
    "category": "correctness",
    "body": "境界値 `len == 0` のとき `slice[0]` で panic する。",
    "evidence": "cargo test -- --ignored で再現（parser::empty_input が panic）",
    "suggestion": "空スライスを早期 return する",
    "path": "src/core/parser.rs",
    "startLine": 42,
    "line": 47
  },
  {
    "severity": "should-fix",
    "category": "design",
    "body": "エラー型が層をまたいで漏れている。",
    "path": "src/core/mod.rs",
    "target": "src/core/ 全体（diff 外の既存コードを含む）"
  }
]
JSON

~/.claude/skills/team-supervisor/scripts/gh-review.py post \
  --pr 123 --role review:normal --verdict changes-requested \
  --findings /tmp/findings.json --summary-file /tmp/summary.md
```

### findings のフィールド

| キー | 必須 | 中身 |
| --- | --- | --- |
| `severity` | 必須 | `must-fix` / `should-fix` / `nit` |
| `category` | 任意 | `correctness` / `design` / `naming` など。省略時は `general` |
| `body` | 必須 | 指摘の本文 |
| `evidence` | 任意 | `**根拠**:` として出る。実測の内容を書く |
| `suggestion` | 任意 | `**提案**:` として出る |
| `path` | 必須 | ファイルパス |
| `line` | 任意 | 書けば行単位、書かなければファイル単位 |
| `startLine` | 任意 | 複数行のとき。`line` より小さくする |
| `side` / `startSide` | 任意 | 既定 `RIGHT` |
| `target` | 任意 | `**本来の対象**:` として出る。段階 3 で使う |

### `--verdict` と `--summary-file`

- `--verdict` は `approved` / `changes-requested`。**`approved` の条件は must-fix が 0 件。**
- `--summary-file` はサマリに足す本文。検証コマンドの結果と外形動作の確認結果を書く。
  件数（must-fix / should-fix / nit）は**スクリプトが findings から数えて自動で載せる**ので
  書かない。

### 投稿前の後始末は自動

`post` は投稿前に**自分の PENDING レビューを消す**。途中で落ちて残った PENDING のスレッドは
未解決件数に現れず、承認の判定が誤って通るため。残したい場合だけ `--keep-pending` を付ける。

### 中身を確かめてから投稿する

`--dry-run` を付けると GitHub を呼ばず、組み立てた本文とスレッドを表示して終わる。書式を
確かめたいときに使う。

## スレッドを列挙する（実装・再レビュー・サブリーダー・リード）

```bash
~/.claude/skills/team-supervisor/scripts/gh-review.py threads --pr 123

# 自分が立てたスレッドだけに絞る（再レビューで使う）
~/.claude/skills/team-supervisor/scripts/gh-review.py threads --pr 123 --role review:normal
```

既定は**未解決のみ**。`--all` で解決済みも含める。JSON で `id` / `isResolved` / `isOutdated` /
`path` / `line` / `subjectType` / `severity` / `role` / `comments` が返る。`severity` と `role` は
スクリプトが先頭コメントの隠しメタデータから読み取った値（読み取れなければ `null`）。
**重大度や役割で選別するときは、本文の文字列ではなくこのフィールドを使う。**
`--role` を付ければスクリプト側で絞り込める。

**`isOutdated` が `true` のスレッドも未解決なら対象に含める。** 実装が push すると、その前に
付いた行単位のコメントは outdated 表示になるが、指摘そのものは消えていない。

## 返信する（実装 subagent）

```bash
~/.claude/skills/team-supervisor/scripts/gh-review.py reply \
  --thread <スレッド ID> --role impl:a --status fixed \
  --message "空スライスの早期 return を追加" --commit a1b2c3d
```

| 状態語 | 意味 | あとで何が起きるか |
| --- | --- | --- |
| `fixed` | 直した | 再レビューが確かめて畳む |
| `partial` | 一部だけ直した | 残した理由を `--message` に書く。再レビューが判断する |
| `wont-fix` | 直さない | サブリーダーが裁く |
| `disputed` | 指摘が誤りだと考える | 根拠を `--message` に書く。サブリーダーが裁く |
| `deferred` | このタスクの範囲外 | 台帳の `deferrals` に載る。サブリーダーが裁く |

**実装 subagent は `--resolve` を使えない**（スクリプトが拒む）。自分で畳めるなら
`unresolved == 0` が自己承認になる。

## 畳む（再レビュー subagent / サブリーダー）

返信と同時に畳む場合:

```bash
# 再レビュー: 直っていると確かめたスレッド
gh-review.py reply --thread <ID> --role review:normal --status resolved \
  --message "早期 return を確認。cargo test parser:: 通過" --resolve

# サブリーダー: 指摘を退けたスレッド
gh-review.py reply --thread <ID> --role subleader:task4 --status overruled \
  --message "呼び出し元 mod.rs:71 の assert で非空が保証されている" --resolve
```

畳むだけなら `resolve --thread <ID> --role <役割>`。

**畳んでよいのは 2 者だけ。**

- **再レビュー subagent**: 実際の差分を見て直っていると確かめたスレッド
- **サブリーダー**: 自分が `overruled` と裁定したスレッド

直っていないスレッドには `--status still-open` で返信し、**畳まない**。

## 承認の門を確かめる（サブリーダー / リード）

```bash
# standard（通常レビューと敵対的レビューの 2 本立て）
~/.claude/skills/team-supervisor/scripts/gh-review.py gate --pr 123 \
  --require-roles review:normal,review:adversarial

# light（通常レビュー 1 本）
~/.claude/skills/team-supervisor/scripts/gh-review.py gate --pr 123 \
  --require-roles review:normal
```

次の 3 つが**すべて満たされたときだけ終了コード 0**。そうでなければ 1 を返し、`missing_roles`
と未解決スレッドの一覧を表示する。サブリーダーは承認を決める前に、リードは topic へ取り込む
前にこれを通す。

1. 未解決スレッドが 0 件
2. 自分の PENDING レビューが 0 件
3. `--require-roles` に挙げた役割のレビューが、すべて 1 件以上提出されている

**`--require-roles` は必須である。** 未解決スレッドが 0 件であることは「レビューが行われた」
ことを意味しない——レビューを 1 度も走らせていない PR ではスレッドがそもそも 0 件になるので、
1 と 2 だけの門はレビュー無しの PR を素通しする。任意引数にすると渡し忘れた時点で同じ穴に
戻るため、省略するとスクリプトが止まる。

`verdict` が `approved` であることは条件にしない。再レビューは直ったスレッドに `reply
--resolve` を返すだけで `post` を再実行しないので、承認に至った PR でもその役割の最新レビューは
初回の `changes-requested` のまま残る。

## スクリプトが拒むこと

呼ぶ側が書き間違えても止まるようにしてある。

- `severity` が 3 種類以外
- `verdict` が 2 種類以外
- `path` の無い finding
- 役割の接頭辞が `review` / `impl` / `subleader` / `lead` 以外
- 役割に許されない状態語
- 実装 subagent による `resolve`（返信を投稿する前に止める）

## 直接 GraphQL を書かない理由

`event` は `COMMENT` 固定でなければならない（`APPROVE` と `REQUEST_CHANGES` は自分が作った PR に
対して GitHub が拒む。実装もレビューも同じ `gh` 認証で動くので必ず自分の PR になる）。また
`subjectType: FILE` は `addPullRequestReviewThread` にしかなく、バッチ投稿の
`DraftPullRequestReviewThread` には無いので、行単位はまとめて積み、ファイル単位は 1 件ずつ足す
必要がある。この順序と使い分けを毎回手で書くと間違える。スクリプトが引き受ける。
