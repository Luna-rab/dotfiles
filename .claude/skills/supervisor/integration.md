# 積み替えレーン（リード本体が行う）

ワークフローから決着の返り値を受けたら、**その 1 本をその場で stacked PR へ積む**。溜めない。

```
<base: lead-setup.md §5 で決めた分岐元>
 ← stack/<作業名>--task-0   空コミット 1 つ / PR = 計画と進行状況
   ← stack/<作業名>--task-1
     ← stack/<作業名>--task-2   …決着した順に積む
```

**リードはマージしない。** タスク PR は人間がレビューするまで open のまま stacked PR の上に残り、
**マージはユーザーが `gh stack merge` で下から行う**（[finish.md](finish.md) §8）。
以前は決着したブランチを topic へ `git merge --no-ff` で取り込んでいたが、指摘が来た時点で
既に混ざっていて修正と rebase がしにくかった（design-notes.md「なぜ topic への統合をやめて
スタックにしたか」）。

**決まった順番は `<スクリプト>/stack.py` に閉じ込めてある**（`precheck` / `append` / `show`）。
リードが判断するのは、検査が落ちたときとコンフリクトを解消するときだけである。

**タスク PR はまだ無い。作るのはあなたである**（§2 の手順 1）。レビューの往復は review.json に
閉じていて、全件が決着してから作るので、**人間が読む PR には決着した変更と挙動の変化だけが載る**。

## 目次

どの節に何が書いてあるか。**1 本積むあいだに §1 → §5 を順に通る**ので、その段に入る直前に
その節だけを読めばよい。

- 作業する場所——`gh stack` を叩くディレクトリと、触ってはならないもの
- §1. 返り値を実物で確かめる（動く前に必ず・1 コマンド）——`stack.py precheck` の 6 検査と、
  落ちたときの読み方
- §2. タスク PR を作り、worktree を外して積み、**stack PR（task-0 の PR）の本文を差し替える**
  （1 本ずつ・並列にしない）
- §3. コンフリクトを解消する——`stack.py append --continue` と `--abort`
- §4. stacked PR の先頭で検証する——落ちたときに原因ブランチを直して積み直す手順
- §5. 次を出す——デフォルトブランチのポインタの確認と、次のワークフローの起動
- 却下された残件の回収——全タスクが終わってから 1 本にまとめる

## 作業する場所

**git と `gh stack` を動かすのは `<スタックツリー>` の中だけである。** [lead-setup.md](lead-setup.md) §1 で作った
`.claude/worktrees/supervisor-<作業名>` で、stacked PR の先頭が載っている。

```bash
git -C <スタックツリー> <サブコマンド>
<スクリプト>/stack.py <サブコマンド> --tree <スタックツリー> …
```

**`gh stack` の追跡情報は worktree ごとに別で、連結 worktree からは見えない**（`.git/gh-stack` に
入り、別の worktree で `gh stack view` を叩くと終了コード 2 で「not part of a stack」になる。
実測は design-notes.md「gh stack v0.1.0 で確かめたこと」）。だから**stacked PR を触る場所を
スタックツリー 1 か所に固定する**。`stack.py` は `--tree` で渡したディレクトリを cwd にして
`gh stack` を叩くので、この規律はスクリプトが守る。

[lead-setup.md](lead-setup.md) §1 の `EnterWorktree` でセッションの cwd はスタックツリーになっている（通らなければ
そこで止まる決まりなので、ここに来ている以上は入っている）。**それでも `-C` と `--tree` を省かない**——
セッションが落ちて別のセッションで再開したときはスタックツリーの外から始まる
（[ledger.md](ledger.md) の再開手順 1 で入り直すが、忘れることがある）。そのとき省くと
**ユーザーが作業している作業ツリーで動き、そのブランチ・HEAD・未コミットの変更を巻き込む**
（実測は design-notes.md「なぜリードに専用の worktree を与えるか」）。

§1 → §5 の順に行う。

## §1. 返り値を実物で確かめる（動く前に必ず・1 コマンド）

**完了通知も返り値も真正性の証拠にならない。** 実行中の run に「完了」通知が誤って発火し、
PR 番号・マージ・失敗談まで含む**精巧な捏造レポート**が届いた実績がある。動く前に、
`stack.py precheck` を 1 回叩く。**6 つの検査をまとめて通す。**

```bash
<スクリプト>/stack.py precheck \
  --tree <スタックツリー> --branch <タスクブランチ> --parent <起点のブランチ> \
  --dir <ベース>/notes/task<番号> \
  --reviewers <返り値の reviewers> --expected-reviewers <返り値の expectedReviewers> \
  --tier <返り値の tier> --adversarial-ran <返り値の adversarialRan（true / false）> \
  --transcript-dir <完了通知に入っていた transcriptDir>
```

`--parent` は**そのタスクを起動したときの stacked PR の先頭**である（`state.py` の `parent`。
起動時に `state.py set --parent` で入れてある。[SKILL.md](SKILL.md) §7）。`branch-and-commits` が
ここを起点にコミットを数え、§2 の手順 1 で作るタスク PR の base にもこの値を使う。

| 検査 | 何を見るか | 落ちる条件 |
| --- | --- | --- |
| `already-stacked` | `gh stack view --json` にそのブランチがあるか | 落とさない。**true なら §2 の `add` を飛ばす**（再開・再実行で二重に積まない） |
| `run-finished` | `transcriptDir/journal.jsonl` に `"type":"result"` の行 | 行が無い（run がまだ生きている） |
| `branch-and-commits` | `verify.py`（ブランチが origin にある / 起点からのコミットが 1 件以上） | どちらかが無い。`already-stacked` が true のときは起点が動いているので見ない |
| `reviews-settled` | `review.py list --require-empty` | review.json が無い（レビューが 1 度も走っていないか `--dir` が違う）/ `open` が 1 件以上 |
| `reviewer-count` | 返り値の `reviewers` と `expectedReviewers` | `reviewers` < `expectedReviewers` |
| `adversarial-ran` | 返り値の `adversarialRan` | `tier` が `standard` なのに false |

**終了コードが 0 でなければ積まない。** 落ちた検査の生の出力は `failed_detail` に入るので、
そこだけを読む。`resumeFrom` を組み立ててワークフローを起動し直す
（`SKILL.md` §7「失敗したワークフローを立て直す」）。`reviews-settled` が落ちたときの
片づけ方は [review-store.md](review-store.md) にある。

**なぜこの 6 つなのか。**

- **`run-finished` を飛ばさない。** 生きている run の後始末（worktree の除去・ブランチの削除）を
  してはならない。`--transcript-dir` を省くとこの検査は `skipped` になり、**落ちないが通ったこと
  にもならない**（出力の `skipped` に理由が出る）。生きている疑いがあれば `TaskStop` で止め、
  静止を確かめてから進む。**worktree が生きているうちは積めない**（§2 の 2）。
- **`reviewer-count` と `adversarial-ran` は別のことを見ている。** どちらも返り値の数の
  突き合わせだが、前者はラウンド単位である。敵対的レビューは各ループの 1 巡目だけ走るので、
  2 巡目で決着したタスクは `reviewers` も `expectedReviewers` も 1 になり、前者だけでは
  「standard なのに敵対的が 1 度も走っていない」を表せない。
- **review.json からはレビュアーの体数が分からない。** 指摘 0 件で終わったラウンドも、起動
  しなかったレビュアーも、そのラウンドの記録が無い点で同じに見える。返り値の数だけが食い違いを
  表せる（design-notes.md「レビュアーが走ったことをどう担保するか」）。
- 終了コードを読むときは**パイプに繋がない**（`| head` に繋ぐと `$?` が `head` のものになる）。

## §2. タスク PR を作り、worktree を外して積み、stack PR を更新する（1 本ずつ・並列にしない）

**並列に積むとコンフリクトする。** 決着が同時に来ても 1 本ずつ処理する。

1. **タスク PR を作る。** 全レビューが決着してから作るので、これが最初で最後の本文である。
   タイトルはワークフローの返り値の `prTitle` をそのまま使う。本文は `state.py task-body` に
   通す——**stacked PR の 1 本であることと、下から順にレビュー・マージすること、全体の入口が
   stack PR であることを 3 行の案内として先頭に差す**（タスク PR だけを開いた人には、base が
   デフォルトブランチでない理由が分からない）。

   ```bash
   <スクリプト>/state.py task-body --base <ベース> --task <番号> \
     --body-file <返り値の prBodyFile>          # pr-body-final.md のパスを返す
   ```

   ```
   /create-pr base=<起点のブランチ（precheck の --parent と同じ）> head=<タスクブランチ> title="<返り値の prTitle>" body-file=<返ってきた pr-body-final.md>
   ```

   - **`gh pr create` を直接叩かない。** PR を作る作法（push・既存 PR の有無の確認・作成後の
     報告）は `create-pr` に 1 か所へ集めてある（[stack-pr.md](stack-pr.md)「作る（§4）」と
     同じ呼び方である）。**とくに `base` を渡さないと `create-pr` が履歴を遡って別の base を
     選び、PR が載る先が変わる。** `draft=true` は渡さない——決着済みで、人間がレビューできる
     状態にする。
   - **案内に PR の一覧を載せない。** タスク PR の本文を書くのはここ 1 回だけなので、後から
     積まれた PR は載らず古くなる。一覧は stack PR に置き、そこを指すだけにする
     （design-notes.md「なぜ最初に draft の stack PR を作るか」）。
   - **`prBodyFile` が空のとき**（PR 本文エージェントが 2 回とも起動しなかった）は、
     `<ベース>/notes/task<番号>/pr-body.md` に DoD 1 行と引き継ぎノートのパスだけを書いて
     `task-body` に通し、それを本文にして作る（本文が無いことより、成果を GitHub に出す方を
     優先する）。そのうえで `gh pr comment` で「本文の自動生成に失敗した」ことを書き、
     **ユーザーに知らせる**。stack PR の残課題にも 1 行残す。
   - **作成結果の PR 番号を `state.py set --base <ベース> --task <番号> --pr <番号>` で入れる。**
     手順 3 でコンフリクトへ寄っても番号を失わない。
2. **エージェントの worktree を先に外す。** `gh stack add` は対象ブランチへ HEAD を移すので、
   他の worktree がそのブランチを握っていると終了コード 5 で落ちる（実測は
   design-notes.md「gh stack v0.1.0 で確かめたこと」）。手順は §4 ではなくここに来る。

   ```bash
   <スクリプト>/worktree.py list --run <runId>                      # locked が 0 件か
   <スクリプト>/worktree.py rescue --path <パス> --branch <タスクブランチ>   # dirty のときだけ
   <スクリプト>/worktree.py remove --run <runId> --branch <タスクブランチ> --settled
   ```

   - **`locked` が 1 つでもあれば run はまだ終わっていない**（またはロックを外さずに死んでいる）。
     `remove` はロックが残っているものを消さずに拒む。
   - `--settled` は「precheck が通ってレビューが全件決着した」というリードの明示である。
     打ち切ったタスクは `--aborted`。どちらも無いとスクリプトが拒む。
   - `--branch` を足すと、**この worktree にしか無いコミットが残っているもの**も拒む
     （`unpushed`）。決着したブランチなら全部 push 済みなので通る。
   - **未コミットの変更が残っていたら、消す前に `rescue` で保全する。** その worktree で
     `git add -A` → commit → `git push origin HEAD:refs/heads/<タスクブランチ>` を行う
     （コミットメッセージは `wip: 中断時点の保全（検証未実施）` 固定）。保全したら
     `state.py set --task <番号> --decision "中断時点の未検証コミットを保全した（<SHA>）"` を入れる。
3. **stacked PR へ積む。** `stack.py append` が決まった順番を 1 回で実行する
   （`gh stack add` → `gh stack rebase --no-trunk` → `gh stack push` → `gh stack link` →
   `gh stack view --json` で `needsRebase` が全件 false であることの確認）。

   ```bash
   <スクリプト>/stack.py append \
     --tree <スタックツリー> --trunk <base> --branch <タスクブランチ>
   ```

   - **`gh stack rebase` に `--no-trunk` が付いている。** 外すと trunk（`<base>`）を
     fetch して stacked PR の土台ごと動かす経路に入り、ユーザーの作業ツリーが checkout している
     base ブランチを巻き込む。
   - `already-stacked` なら `add` を飛ばし、積み替えと確認だけを行う。**同じ引数で叩き直しても
     同じ結果になる**（実測）。
   - **`trunk_moved: true` は失敗ではない。** 走行中に base ブランチが進むと、土台は trunk より
     古いままになる（`--no-trunk` は土台を rebase しない）。stacked PR の組み立ては済んでいるので
     積み替えは要らない。**仕上げで `stack.py sync` を通す**（[finish.md](finish.md) §8 の 2）。
     ここでは何もしない——1 本積むたびに sync すると、そのたびに全ブランチが force push で
     入れ替わり、積んだ後の検証がどの内容に対するものか分からなくなる。
   - **`stale_record` が空でないなら、そこで止めて直す。** 追跡情報に記録された位置が実物の ref と
     食い違っている（別の場所で `gh stack sync` を通したときに起きる）。古い記録のまま積み足すと
     `gh stack rebase` が古い位置を起点にする。直し方は
     [ledger.md](ledger.md)「stacked PR の追跡情報が壊れたとき」。
   - **ローカルブランチは `append` が origin から作り、upstream も付ける。** 実装エージェントは
     `git push origin HEAD:refs/heads/<ブランチ>` で push するので、リードのリポジトリには
     リモート追跡 ref しか無い。その状態で `gh stack add` を素で叩くと**空のブランチが積まれる**
     （実測は design-notes.md「gh stack v0.1.0 で確かめたこと」）。origin にもローカルにも
     無ければ `append` が止まる。**upstream を付けるのは、そのブランチを checkout した人の
     `git pull` を落とさないためである**（付け忘れた実績がある。issue #39）。
   - **`conflict: true` が返ったら §3 へ。** スタックツリーは rebase 途中の状態で残っている
     （`files` に対象が出る）。
   - `gh stack link` は**追加専用**で、stacked PR の並びに合わない PR の base を張り替える。
     `--open` は渡さないので、draft の PR が勝手にレビュー可能へ上がることはない。
4. **積み終えたら状態を 1 コマンドで入れる**（`stack_order` に 1 行足され、`stacked_on` に
   1 つ下のブランチが入る）。台帳と stack PR 本文はここから書き出されるので、同じことを
   2 か所へ書かない（[ledger.md](ledger.md)「台帳と stack PR 本文は `state.py` が書き出す」）。

   ```bash
   <スクリプト>/state.py set --base <ベース> --task <番号> --status stacked \
     --pr <手順 1 で作った PR の番号> --rejected <返り値の rejected> \
     --reviews "closed <数> 件 / rejected <数> 件" \
     --decision "<返り値の decisions を 1 件ずつ>" --deferral "<返り値の deferrals を 1 件ずつ>"
   ```

5. **台帳と stack PR（task-0 の PR）の本文を書き出して差し替える。溜めずに、1 本積むたびに行う。**
   ユーザーが GitHub で読む進行状況が、stacked PR の実物とずれない
   （[stack-pr.md](stack-pr.md)）。回収しないと決めた残件があれば、先に
   `<ベース>/prose/remaining.md` に足す（打ち切ったタスクの理由は `render` が自動で足す）。

   ```bash
   <スクリプト>/state.py render --base <ベース>
   gh pr edit <stackPR番号> --body-file <ベース>/stack-pr-body.md
   ```

   - **§3 へ寄ってコンフリクトを解消したときも、積み終わったらここへ戻る。** 積んだのに本文が
     古いままだと、ユーザーには「まだ積んでいない」と読める。
   - 台帳（`<ベース>/ledger.md`）も本文の下書きも git の追跡対象外なので commit しない。

## §3. コンフリクトを解消する

積み替えでコンフリクトが出るのは、**そのタスクを起動した時点の stacked PR の先頭から、別のタスクが
先に積まれて先頭が動いた**ときである。

1. `files` に出たパスを解消する。**機械的な両側結合をしない**——共有末尾を含む hunk で
   閉じ括弧を落として構文を壊すことがある。
2. **意図が読めない hunk は、そのタスクの引き継ぎノートを読む**
   （`<ベース>/notes/task<番号>/impl-*.md` に、何をどう変えたかとその理由が残っている）。
   ワークフローのエージェントは終了していて質問できない。
3. 解消したら `git add` して続ける。**commit しない**——rebase の続きは gh stack が進める。

   ```bash
   git -C <スタックツリー> add <解消したパス>
   <スクリプト>/stack.py append --tree <スタックツリー> --trunk <base> --continue
   ```

   積み終わったら **§2 の手順 4・5（`state.py set` → `render` → `gh pr edit`）へ戻る。**

4. **諦めるときは戻せる。** `--abort` は stacked PR の全ブランチを積み替え前の位置に戻す。

   ```bash
   <スクリプト>/stack.py append --tree <スタックツリー> --trunk <base> --abort
   ```

5. 解消の規模が大きい・判断を伴うなら、単発の fix エージェント（`Agent`、`model: "opus"`、
   `isolation: "worktree"`、`run_in_background: false`）に解消を任せ、別の単発レビュー
   エージェントに解消差分を見せてよい。この解消レビューは **review.json を使わず、返り値だけを
   返す**（そのタスクのレビューは既に決着していて、status を動かせる裁定エージェントがいない
   ため）。**stacked PR の操作は任せない**（解消済みブランチを受け取ってリードが `append` する）。
6. **リードがコンフリクト解消でコードを書いたら `<ベース>/prose/decisions.md` に書く**
   （リードがコードに触れる唯一の場面。`render` が台帳と stack PR の「自律判断の記録」に載せる）。

## §4. stacked PR の先頭で検証する

1. **検証コマンド一式と外形動作を、フルで 1 回流す。** stacked PR の先頭には積んだ全タスクが載っている
   ので、ここが「合わせると壊れる」を捕まえる唯一の場所である。**`run_in_background: true` で
   投げて、待たずに §5 へ進む**——終了すると通知が届く。

   ```bash
   git -C <スタックツリー> symbolic-ref --short HEAD    # stacked PR の先頭に載っていることを確かめる
   git -C <スタックツリー> ... && <brief.md の検証コマンド一式>   # run_in_background: true
   ```

   - **背景に回すのは、待っている間に進められることがあるからである。** §5（デフォルトブランチの
     確認と、**このタスクに `blockedBy` を張っていないタスクの起動**）は先に進める。
   - **依存のある後続タスクは、通知を受けてから起動する。** 壊れた先頭の上に積むと、
     どのタスクが壊したのかを切り分けられなくなる。
   - 検証一式が 1 分程度で終わるプロジェクトでは前景で待ってよい（通知を待つ往復のほうが長い）。
2. **落ちたら、原因のブランチを直す。** stacked PR から外すより操作が少なく、成果も落とさない。
   1. 原因のブランチを単発 fix エージェント（`Agent`、`isolation: "worktree"`、
      `run_in_background: false`）に直させ、**同じタスクブランチに push** させる。
   2. `stack.py append --tree <スタックツリー> --trunk <base> --branch <原因のブランチ>` を
      叩き直す（`already-stacked` なので `gh stack rebase --no-trunk` が上の段を順に積み直す）。
   3. 検証をもう一度流す。
   4. `state.py set --task <番号> --decision "<何をどう直したか>"` を残す。
3. **直せないときだけ stacked PR から外す。** 上の段を親へ詰め直す操作が要るので、最後の手段である。
   `gh stack modify` は対話 TUI で自動化できないため、**外す判断はユーザーに上げる**——
   落ちた検証の出力と、外す対象・詰め直す段数を示して確認を取る。外すと決めたら
   `state.py set --task <番号> --status failed --reason "<理由>"` を入れ、
   stack PR の「残課題」に載せる。

## §5. 次を出す

**台帳と stack PR 本文の更新は §2 の手順 4・5 で済んでいる。** ここでは残っている 2 つを行う。

1. **デフォルトブランチのポインタがずれていないかを見る。ずれていても自分で直さない。**

   ```bash
   git -C <スタックツリー> rev-parse <デフォルトブランチ> origin/<デフォルトブランチ>
   ```

   一致しなければ、worktree のエージェントが共有側のポインタを動かした事故である
   （`git checkout -B` を実行した実績がある）。**`git branch -f` で戻さず、ずれた事実と
   両方の SHA をユーザーに報告する**——`git branch -f` は git 2.34.1 では別の worktree で
   checkout 済みのブランチでも通り、ユーザーの HEAD だけを飛ばす（2.51.1 は拒む。
   `git update-ref` はどちらでも通る。実測は
   design-notes.md「なぜデフォルトブランチのポインタを force で戻さないか」）。
   **git が拒むことを当てにしない**——バージョンで変わるので、動かす前に自分で持ち主を見る。
2. 空いた枠に、`blockedBy` が解けているタスクのワークフローを起動する（§4 手順 1 の検証が
   終わっていないうちは、積んだこのタスクに依存しないものだけ）。**起動時の stacked PR の先頭を
   `state.py set --task <番号> --parent <stacked PR の先頭>` で控える**
   （precheck と PR の base に使う）。

**引き継ぎノートと review.json（`<ベース>/notes/`）は消さない。** 立て直しと stack PR の
仕上げまで使う。消すのは [finish.md](finish.md) §8 の最後、ユーザーに確認してからである。

## 却下された残件の回収

裁定が `rejected` にしたもののうち「妥当だがこのタスクでは直さない」とした分は、返り値の
`deferrals` に載って上がってくる（`state.py set --deferral` で入れてある）。**全タスクが終わってからまとめて 1 本の
ブランチで回収する**（件数が多くてもブランチを分けない。往復が 1 回で済む）。

1. `deferrals` を読み、stacked PR の先頭の実コードで**まだ未解決であることを確かめてから**回収する
   （後のタスクで直っていることがあるので、記述だけを根拠にしない）。review.json の
   `rejected` のコメントに、なぜ直さなかったかが残っている。

   ```bash
   <スクリプト>/review.py list --dir <ベース>/notes/task<番号> --all
   ```

2. 未解決の分を 1 本の残件ブランチにまとめ、単発 fix エージェント（`Agent`、`model: "sonnet"`、
   `isolation: "worktree"`、`run_in_background: false`）に直させる。起点は**stacked PR の先頭**にする。
   **PR のタイトルは `[supervisor #<stackPR番号> followup] <種別>: <件名>`**
   （[stack-pr.md](stack-pr.md)「タイトルの接頭辞」。接頭辞をエージェントのプロンプトに封入する）。
3. 小さい差分ならリード自身の diff 確認で足りる。ロジックを含むなら単発のレビューエージェントを
   1 体走らせる（解消レビューと同じく、返り値だけを受ける経路）。
4. §2 の手順 3 で stacked PR の先頭に積む。PR は §2 の手順 1 と同じく **`/create-pr` に作らせる**
   （`gh pr create` を直接叩かない）。

   ```
   /create-pr base=<stacked PR の先頭のブランチ> head=<残件ブランチ> title="[supervisor #<stackPR番号> followup] <種別>: <件名>"
   ```

   `base` を渡すのは、`create-pr` が履歴を遡って別の base を選ぶと積む先が変わるためである。
   本文は `create-pr` が差分から書く（この 1 本は PR 本文エージェントを通していないので、
   `state.py task-body` は使わない）。
5. 大きい残件は回収せず、stack PR の「残課題」としてユーザーに示す。
