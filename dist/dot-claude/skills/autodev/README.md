# autodev

指示 1 つを stacked PR まで無人で持っていく。**マージはしない**——人間がレビューして
`gh stack merge` で下から行う。用語（ラン・ステージ・概要 PR・要対応など）の意味は
[GLOSSARY.md](GLOSSARY.md) にある。

ステージはすべて `claude -p` を 1 プロセス起動して走らせ、**進行は driver が持つ**（ラン 1 回は
`scripts/autodevlib/app/drive.py`、ステージの順番と打ち切りは `scripts/autodevlib/app/` の
各ファイル）。モデルが決めるのは各ステージの中身だけである。

## 要るもの

| | 何に使うか | 無いと |
| --- | --- | --- |
| `claude`（ログイン済み） | 全ステージの起動 | 何も動かない |
| `gh`（＋ `gh stack` 拡張） | 公開の PR 操作 | PR を作れない |

**資格情報は `claude` 自身のログインである。** Anthropic Console の API キーは要らない。
driver はステージを起動するとき `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_BASE_URL` を**外す**。残っていると claude がサブスクリプションではなく従量課金に
切り替わる。無人のマシンでは `CLAUDE_CODE_OAUTH_TOKEN` を置く（`claude setup-token` で作る）。

## 使い方

起動するのは `/autodev` skill である。入口は `scripts/autodev.py`（`~/.claude/skills/autodev/`
の下）で、PATH には置かない。

```bash
scripts/autodev.py run --name add-cache --repo ~/ghq/github.com/foo/bar --instruction "…"
scripts/autodev.py run --name add-cache        # 続きから（state.json があれば再開）
scripts/autodev.py status --name add-cache
scripts/autodev.py list
scripts/autodev.py clean --name add-cache      # worktree を外す（記録は残す）
```

新しく始めるときは `--instruction` が要る（無ければ走らない）。長い指示は
`--instruction -` で標準入力から渡せる。**走り始めたら途中で口を出せない。**

**やり直すときは `autodev clean` を先に打つ。** ランディレクトリを手で消すと worktree の
実体は消えるが git 側の登録は残り、ブランチが「別の場所でチェックアウト中」として扱われる。
消してしまった場合は `git -C <対象> worktree prune` で外す（`autodev run` も起動時に
`prune` を通すので、作り直しは通る）。

## 進めなくなったとき、決めるのは呼び出し元のエージェント

driver は理由を終了コードと `autodev status` に載せて終わる。**計画を引き直して呼び直すか
どうかを決めるのは呼び出し元のエージェント**（`/autodev` skill を務めるエージェント）である。
driver は自分が呼び直されるかどうかを決めない。

| コード | 意味 |
| --- | --- |
| 0 | 全部スタックに追加した |
| 1 | 走れなかった（起動前の確認・git・gh の失敗） |
| 2 | 計画ステージが `blocked`。前提が崩れているので指示を書き直す |
| 3 | 要対応のタスクがある（要確認 `blocked` か失敗 `failed` のタスク） |
| 4 | ステージが回答を待って止まっている（下） |

`autodev status` はランの状態 `outcome` を返す（`planning` 計画中 / `running` 実行中 /
`waiting` 回答待ち / `held` 要対応 / `stacked` 完了）。

## ステージは聞いて待てる。回答すると続きから進む

計画ステージが受入条件の曖昧さに当たったら、**推測せずに聞いて止まる。**

```
<autodev> ask --id range-empty --question "空入力の range は None か 0.0 か"
```

フックが `defer` を返すのでステージはそこで止まり、driver は質問を出して終了コード 4 で返る。
回答を置いて呼び直すと、**同じツール呼び出しから続く**——読んだ内容も文脈も失われない。

```bash
scripts/autodev.py answer --name <ラン名> --id range-empty --body "None。mean と揃える"
scripts/autodev.py run --name <ラン名>
```

**止まったステージを再開するときはプロンプトを渡さない**（渡すと新しいターンが始まって、止まった
ツール呼び出しが再開されない）。判断するのは**回答のファイルが在るかどうかだけ**なので、
何度呼び直しても同じ結果になる。

実測では、聞いて止まるまでが 9 ターン / $0.519、回答してから計画が終わるまでが 4 ターン /
$0.907 だった。同じ曖昧さで `blocked` を返して全部やり直した経路は 20 ターン / $1.204 を
2 回払っていた。

**いま聞けるのは計画ステージだけである。** タスクの途中のステージ（テスト作成・実装・修正・レビュー・
ジャッジ）が止まると、そこまでの手順をやり直さずに再開する仕組みが要る——タスク 1 本の中の
どこまで進んだかを state.json に持たせる話になるので、別に切ってある。それらのステージは
指示書のとおり `blocked` か `testConflict` で報告する。

**回答を作るかどうか、呼び直すかどうかを決めるのは呼び出し元のエージェントである。** driver は待っている
事実と質問だけを返す。

## 全体の流れ

```mermaid
flowchart TD
    start["/autodev skill が 1 回起動"] --> pre
    subgraph driver["driver（決定的。状態遷移・回数の上限・打ち切りを持つ）"]
        pre["準備: worktree を切る / ブリーフとコードマップを置く"] --> plan
        plan["計画ステージ: 受入条件と DoD を確定し、1 PR に収まるか判定して割る"] --> base
        base["公開: 概要ブランチ（空コミット 1 つ）と概要 PR（draft）"] --> loop
        loop["タスクを 1 本ずつ（順に）"] --> tg
        tg["テスト作成ステージ"] --> impl
        impl["実装ステージ（テストは read-only）"] --> rv
        rv["レビューステージ（通常＋敵対的）"] --> jd
        jd["ジャッジ"] --> decide{"未解決の指摘が 0？"}
        decide -->|いいえ| fix["修正ステージ"] --> rv
        decide -->|はい| gateq{"完了チェック"}
        gateq -->|通る| post["公開: PR 本文 → push → タスク PR → gh stack link（スタックに追加）"] --> loop
        gateq -->|落ちる| stop["概要 PR に理由を残して止まる（要確認）"]
        loop --> done["全部スタックに追加したら概要 PR を gh pr ready"]
    end
    done --> human["人間がレビューし gh stack merge で下から"]
```

## 崩してはいけない線引き

この 8 つは、どれか 1 つを崩すと**この仕組みが成り立たなくなる。**

1. **進行をモデルに持たせない。** どのステージを何回呼ぶか、いつ打ち切るか、完了チェックの合否は driver に
   ある。モデルが進行役になると、完了チェックを飛ばすことも「通った」と言うこともできる。
2. **インフラと実装を混ぜない。** GitHub を触るのは driver の公開だけ。
   ステージは `gh` を叩かず、push もしない。コンテナで走らせたときに GitHub の
   資格情報を渡さずに済む。
3. **テストを書けるのはテスト作成ステージだけ。** 実装ステージはフック（`hooks/deny-writes.py`）で
   止まる。テストが仕様と矛盾していたら、直さずに `testConflict` で報告する。
4. **指摘の状態を動かせるのはジャッジだけ。** driver がジャッジトークン（`AUTODEV_JUDGE_TOKEN`）を
   ジャッジの process にだけ渡す。他のステージは名乗っても拒まれる。自分で閉じられると
   「未解決が 0 件」が自己承認になる。
5. **完了の根拠はステージの報告ではない。** 完了チェック（証拠を集めるのは
   `scripts/autodevlib/ports/evidence.py`、合否を決めるのは
   `scripts/autodevlib/core/verdict.py`）を
   driver が毎回通す。
   検証コマンドは driver が自分で流す。
6. **セッションを続けるのは実装と修正だけ。** レビューは毎ラウンドまっさらにする。1 ラウンド目の
   結論を持ち込むと、それがフレーミングになって検出が落ちる。
7. **マージしない。** 概要 PR が draft のあいだは上のタスク PR もマージできない。
   全部スタックに追加し終わってから `gh pr ready` に上げる。
8. **進行状態を agent に書かせない。** タスクの一覧・状態・PR 番号・要対応のタスクの出所は
   state.json だけで、driver がテンプレートのマーカーへ毎回組み立てて差す。agent が書くのは
   自由記述だけである。

## ステージごとに変えるもの

`scripts/autodevlib/config/stages.py` の `TABLE` にある。**体数の計算も打ち切りの条件も
ここには無い**（`scripts/autodevlib/core/review_policy.py` と driver にある）ので、
レビューステージを 1 体増やしても
完了判定のコードに手が入らない。

| | 効くもの |
| --- | --- |
| モデルと思考量 | 読んで決めるステージは `opus`、文章を組むだけのステージ（PR 本文・まとめ）は `sonnet` |
| 書き込みの範囲 | `edits` が偽のステージは worktree の中を書き換えられない（フックが止める） |
| ターンの上限 | `--max-turns`。超えるとステージが失敗する |
| 環境変数 | ジャッジトークンとテストの解禁は、それが要るステージにだけ渡す |

書き込みを止めるのは `hooks/deny-writes.py` で、**`--disallowedTools` は使わない。**
ツールごと消すと、読むだけのステージが自分の結果の JSON を書けなくなる（`Write` が要る）。
フックなら宛先で分けられる——worktree の中は止め、結果を書く `<ランディレクトリ>/` の下は通す。
Bash のリダイレクトも同じ 1 か所で見る。

それでも完全には止められないので、ソースが動いていないことは完了チェック②（コミット数）と
完了チェック⑤（テストの差分）で git から確かめる。

## 走行中に driver が見て、打ち切る

ステージの JSONL は**走りながら 1 行ずつ**読む。読んだ行はその場でログへ落とすので、打ち切った
ステージにも記録が残る。

見ているのは 2 つで、**判断するのは driver のコードである。モデルは入らない。**

- **進行**——ターン数と直前のツールを state.json の `running` に書く（5 秒ごと）。statusline と
  `autodev status` がこれを読む
- **ガードとの衝突**——フックに 10 回止められたステージは打ち切る。指示書を読み違えているので、
  ターンの上限まで使い切っても直らない

打ち切りは**プロセスを kill せず、標準入力へ制御要求を送る。**

```json
{"type":"control_request","request_id":"…","request":{"subtype":"interrupt"}}
```

こうすると `result` イベントが返るので、usage と停止理由（`subtype:
error_during_execution` / `terminal_reason: aborted_streaming`）が残る。制限時間の超過も
同じ経路を通る。`cancel_queued` は `system/init` の `capabilities` に
`interrupt_cancel_queued_v1` があるときだけ付ける——**版の文字列で比べない**（公式もそう
指示している）。

**ツール 1 回ごとの承認はしない。** 同期の関門を挟むと、答える相手が生きていないと進めず、
無人で回せなくなる。ツール単位で止めるのはフック（`hooks/deny-writes.py`）の仕事で、
そちらは即答する。

## 文面はテンプレートにあり、数は state.json から入る

**Python の中に Markdown を書かない。** 文面は `templates/*.md` にあり、`${名前}` のマーカーを
driver が埋める。埋め方は 2 種類だけである。

| マーカー | 誰が用意するか | いつ |
| --- | --- | --- |
| `${tasks}` `${held}` `${decisions}` `${deferrals}` `${verify}` `${test_globs}` | **driver が state.json / config.json から組み立てる** | 書き出すたび |
| `${prose}`（概要 PR）・`${notes}`（brief）・タスク PR の本文 | **agent が書いたものを `<ランディレクトリ>/prose/*.md` から読む** | agent を呼んだとき 1 回 |

こう分けるのは、**進行状態の出所を state.json 1 つに保つ**ためである。概要 PR の本文は
1 本スタックに追加するたびに書き直すので（タスク 3 本なら 5 回）、表まで agent に書かせると
「state.json では要確認なのに本文では進行中」がありえて、そのたびにモデルを呼ぶことになる。

自由記述を書く agent は 2 つある。

- **まとめステージ**（`summary`）: 概要 PR の「何をする作業か」。**計画の直後と仕上げの 2 回だけ**
  呼ぶ。1 本スタックに追加するたびの書き直しでは呼ばず、`<ランディレクトリ>/prose/overview.md` を読み直す
- **計画ステージ**: `briefNotes` を返すと、driver が `brief.md` の「気をつけること」に差す。
  テスト作成ステージから後の全部が読む

## ステージの結果は schemas/ で形が決まる

driver は `schemas/<指示書>.json` の本文を `claude --json-schema` に渡す。するとステージに
`StructuredOutput` ツールが 1 つ増え、**最後の応答がその形の JSON になる。**

**形の出所はスキーマのファイル 1 か所である。** 指示書（`contracts/*.md`）は各キーが何を意味
するかを書き、形そのものは書かない。

知っておくべきことが 3 つある。

- **生成時の制約ではなく事後の検証である。** 外れた出力はツールのエラーとして差し戻され、
  claude が言い直す。その言い直しが `--max-turns` の予算を食うので、結果を返すステージの上限は
  多めに置いてある
- **検証に失敗しても終了コードは 0 のまま**という経路がある。満たせないスキーマを渡した
  実測では `subtype: success` / `is_error: false` で `structured_output` が空だった。だから
  driver は結果が在ることを別に確かめる
- **スキーマはファイルパスではなく JSON の本文を渡す。** パスを渡すと
  `--json-schema is not valid JSON` で起動前に落ちる。draft-07 だけで、`$schema` に
  2020-12 を書くと拒まれる

**結果をファイルに残すのは driver である。** ステージに書かせないので、在ることと形が保証される
（`tasks/task<番号>/result-<ステージ>-<ラウンド>.json`）。

## ステージを足す・直す

ステージ 1 つは 5 か所でできている。**足すのはこれだけで、完了判定のコードには手が入らない。**

| 置き場 | 何を書くか |
| --- | --- |
| `scripts/autodevlib/config/stages.py` の `TABLE` | 名前・指示書・モデル・思考量・ターンの上限・セッションを続けるか・ジャッジトークンを渡すか・ソースを書き換えるか |
| `scripts/autodevlib/core/prompt.py` の `ROLE_KEY` | そのステージをどの役割の必須ルールで走らせるか。**足さないとステージの起動時に `KeyError` で落ちる**。役割ごと新しいなら `REQUIRED_RULES` にも 1 項目足す |
| `contracts/<名>.md` | そのステージが何をするか。**ステージはここを自分で読む** |
| `schemas/<名>.json` | 結果の形（結果を返すステージだけ）。driver が `--json-schema` に渡す |
| `templates/<名>.md` | 成果物の形。マーカーは driver が埋める（文面を出すステージだけ） |

レビューステージを増やすときは、`scripts/autodevlib/ports/review_store.py` の `REVIEW_STAGES` に
1 行足し、`scripts/autodevlib/core/review_policy.py` の `expected_reviewers()` が返す
並びに入れる。**体数は `review.json` の
`runs` から数えるので、期待する数をコードに埋める必要はない。**

指示書の本文はプロンプトに入れない。利用者のメッセージに入るのは、前置き・指示書への案内と
プレースホルダ表・このタスクの値だけで、**必須ルールは `--append-system-prompt` で system 側に置く**
（長い指示書とコードを読む間も薄まらないようにする）。

## ランナーを差し替える

`claude` を別のものに替えるときに書き換えるのは `scripts/autodevlib/ports/runner.py`
1 本である。
次の 2 つの形を保てば driver には手が入らない。

- `Call`——プロンプト・cwd・結果の書き先・モデル・ツール・上限・環境変数・`watch`
- `Result`——終了コード・停止理由・最後の応答・usage・セッション id・`aborted`

`watch` はイベント 1 つごとに呼ばれ、**文字列を返すとその理由でステージが打ち切られる。**
監視の中で例外が出てもステージは止めない（監視の誤りで作業を落とさない）。

## 実測して分かっていること

- **`docker agent` では組めない。** `harness: type: claude-code` が受け付けるのは
  `type` / `effort` / `model` の 3 つだけで、`tools` / `output_format` /
  `structured_output` / `allowed_tools` / `settings` / `max_turns` は
  `unknown field` で弾かれる。ステージごとのツール制限もターンの上限も指定できない。
- **`claude setup-token` のトークンは API キーとして使えない。** `Authorization: Bearer` で
  素の HTTP に出しても `HTTP 429: rate_limit_error`（`anthropic-ratelimit-*` ヘッダは付かず、
  message は `Error` の 1 語）が返る。Claude Code 以外のクライアントからは使えないので、
  `docker agent` の標準の agent をサブスクリプションで動かす道はここで閉じている。
- **`--settings <file>` でフックを外から渡せる。** worktree に
  `.claude/settings.local.json` を置かずに済む（置くと commit に混ざる危険があった）。
  `PreToolUse` が終了コード 2 を返すと書き込みは起きない。
- **`--disallowedTools` はツールをセッションから消す。** `bypassPermissions` でも効き、
  消したツールはモデルの手元に現れない。ただし宛先で分けられないので autodev では使わない。
- **プロンプトは標準入力から渡す。** `--allowedTools <tools...>` は可変長で、次のオプションが
  来るまで後ろの引数を全部取る。プロンプトを引数の末尾に置くとツール名として飲まれ、
  `Input must be provided either through stdin or as a prompt argument` で落ちる。
  **オプションの並び順しだいで通ることもある**ので、余計に見つけにくい。
- **`2>&1` を書き込みと数えない。** フックの正規表現が `>` を拾うので、
  `cat app.py 2>&1 | head` のような読むだけのコマンドが拒まれた。fd の複製
  （`\d*>&\d*`）を先に外してから書き込みを探す。
- **`--max-turns` は効く。** 超えると終了コード 1 と `subtype: error_max_turns` /
  `terminal_reason: max_turns` が返る。
- **`--permission-mode bypassPermissions` なら cwd の外も読み書きできる。** ステージは
  `<ランディレクトリ>/` の下に結果を書くので、これが要る（`--add-dir` は要らなかった）。
- **`--session-id` で id を呼ぶ側が決められる。** 出力から拾わなくてよい。続きは
  `--resume <id>`。
- **制御要求は標準入力に混ぜて送れる。** SDK 専用ではない。`interrupt` を送ると
  `{"subtype":"success","response":{"still_queued":[],"cancelled":[]}}` が同じ標準出力へ
  返り、会話には `[Request interrupted by user]` が残る。`still_queued` と `cancelled` に
  載るのは**こちらで `uuid` を振ったメッセージだけ**なので、送る user メッセージには必ず
  振る。
- **`--input-format stream-json` で起動したら、`result` を見て標準入力を閉じる。**
  閉じないと claude は次のメッセージを待って終わらない。
- **`system/init` はターンごとに出る。** 1 回だけ来る前提で待つと 2 ターン目以降で詰まる。
- **`session_id` は CLI が決める。** 入力に書いた値は無視される。
- **読むのは JSONL の `result` イベント 1 つでよい。** `subtype` / `is_error` /
  `stop_reason` / `num_turns` / `result` / `usage` / `total_cost_usd` /
  `permission_denials` が全部ここに載る。
- **ステージは 1 回あたり約 26k トークンの固定費がかかる**（Claude Code のシステムプロンプト）。
  ただし実際はずっと大きい。おもちゃのリポジトリ（4 ファイル）でも 1 ステージで in 238,000 前後
  だった。26k は下限であって典型値ではない。
- **`gh stack link` は local tracking state に依らない。** だから「`gh stack` の追跡情報は
  worktree ごとに別」という制約を踏まない。PR は `gh pr create` で自分のタイトルと本文で
  作り、link で連ねる。
- **順に 1 本ずつスタックに追加するので、積み替え（`gh stack rebase` ＋ force push）は 1 度も要らない。**
- **完了チェック⑤の基準は `parent` ではなくテスト作成ステージのコミット**（`state.json` の `testsAt`）。
  テスト作成ステージはタスクのブランチに commit するので、`parent` を基準にすると、実装ステージが
  テストに触っていないランでも必ずテストの差分が出る。

## 置き場

```
~/.claude/skills/autodev/        このディレクトリ（install.sh が skill ごと symlink する）
  SKILL.md    skill の本体。`skill_root()` はこのファイルを探して置き場を決める
  scripts/    入口（`autodev.py`）と driver 本体（`autodevlib/`）
  contracts/  ステージが自分で読む指示書
  schemas/    ステージが書く結果の形。driver が照らす
  templates/  文面。マーカーを driver が埋める
  hooks/      書いてはいけないファイルへの書き込みを止める PreToolUse
~/.config/autodev/repos/<repo>.json   検証コマンド・テストのパス・変更禁止パス
~/.local/state/autodev/<ラン名>/
  state.json          進行状態（driver だけが書く。ステージには渡さない）
  brief.md  map.md    ステージが読むブリーフとコードマップ
  overview-pr-body.md 概要 PR の本文（テンプレート＋state.json＋prose/overview.md）
  guard.json          書き込みを止めるフックの設定（`claude --settings` で渡す）
  prose/              agent が書いた自由記述。書き出すたびに読み直す
  tree/               worktree。git と gh を叩くのはここだけ
  tasks/task<番号>/   review.json・ステージが返した result-*.json・pr-body.md
  logs/               claude の出力そのまま
```

**対象リポジトリには 1 行も足さない。** worktree もその外に作り、フックの設定も外から渡すので、
`.gitignore` の追加も後片付けも要らない。
