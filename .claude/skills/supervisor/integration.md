# 統合レーン（リード本体が行う）

ワークフローから承認の返り値を受けたら、**その 1 本をその場で topic へ取り込む**。溜めない。

**すべてリード本体が自分の作業ツリーで行う。** サブエージェントにマージや PR のクローズを
指示すると安全クラシファイアに確率的に遮断される。同じ理由で
**`gh pr merge` はリードでも使わない**。
`git merge` + `git push` で取り込めば、GitHub は head コミットが base に到達したことを検出して
タスク PR を自動的に MERGED 判定にする（理由は [design-notes.md](design-notes.md)）。

§1 → §5 の順に行う。

## §1. 返り値を実物で確かめる（動く前に必ず）

**完了通知も返り値も真正性の証拠にならない。** 実行中の run に「完了」通知が誤って発火し、
PR 番号・マージ・失敗談まで含む**精巧な捏造レポート**が届いた実績がある。動く前に:

1. **run が本当に終わっているかを確かめる。** 完了通知に入っている `transcriptDir` の
   `journal.jsonl` に `"type":"result"` の行があるか、`ls --time-style=full-iso` で
   transcript の mtime がまだ伸びていないかを見る。**まだ伸びていれば run は生きている。**
   生きている run の後始末（worktree の除去・ブランチの削除）をしてはならない。先に `TaskStop`
   で止め、静止を確かめてから進む。
2. **ブランチ・コミット・PR を確かめる。** `git fetch origin` から始めて、ブランチがリモートに
   実在するか、`origin/<base>..origin/<タスクブランチ>` にコミットが載っているか（0 件なら成果が
   無い）、PR の head と base が返り値どおりで state が OPEN かを 1 回で見る。

   ```bash
   ~/.claude/skills/supervisor/scripts/verify.py \
     --branch <タスクブランチ> --base topic/<作業名> --pr <PR 番号>
   ```

   終了コードが 0 でなければ取り込まない（どの項目が合わなかったかが `checks` に出る）。
3. **承認の門を通す。** 未解決スレッド・PENDING レビュー・要求した役割のレビューが提出
   されたかを確かめる。**`--require-roles` にはワークフローの返り値の `requireRoles` を渡す**
   （`tier` に対応している。省略するとスクリプトが止まる）。

   ```bash
   # standard
   ~/.claude/skills/supervisor/scripts/gh-review.py gate --pr <PR 番号> \
     --require-roles review:normal,review:adversarial

   # light
   ~/.claude/skills/supervisor/scripts/gh-review.py gate --pr <PR 番号> \
     --require-roles review:normal
   ```

   終了コードが 0 でなければ取り込まない（`missing_roles` と未解決スレッドの一覧が
   表示される）。PENDING に残ったスレッドは未解決件数に現れないので、両方を見るこの
   コマンドを使う。**`verify.py` はこれを呼ばない**（GitHub のレビュー状態は gh-review.py が
   一手に扱う）ので、2 つを続けて叩く。

   **未解決スレッドが 0 件であることは「レビューが行われた」ことを意味しない。** レビューを
   1 度も走らせていない PR ではスレッドがそもそも 0 件になるので、`--require-roles` が
   ワークフローの自己申告を裏づける唯一の機械的な検査になる。

   終了コードを読むときは**パイプに繋がない**（`| head` に繋ぐと `$?` が `head` のものになる）。

どれかが非 0 なら取り込まない。`resumeFrom` を組み立ててワークフローを起動し直す
（`SKILL.md` §7「失敗したワークフローを立て直す」）。

## §2. 取り込む（1 本ずつ・並列にしない）

**並列にマージするとコンフリクトする。** 承認が同時に来ても 1 本ずつ処理する。

1. `git fetch origin` し、topic を checkout して `git pull` で最新化する。
2. **二重取り込みを防ぐ**: `git merge-base --is-ancestor origin/<タスクブランチ> origin/topic/<作業名>`
   が成功したら、既に入っているのでそのブランチは飛ばす（再開・再実行時に効く）。
3. `git merge --no-ff origin/<タスクブランチ>` で取り込む。
4. **コンフリクトが出なければ、ここではビルドだけを流す**（`<ベース>/brief.md` のビルドコマンド。
   `<ベース>` は `place.py base-dir --work <作業名>` が返すパス）。
   そのブランチの検証一式はレビューが承認前に自分の worktree で流しているので、
   ここでのフル検証は §5 の 2 で改めて行う。
5. **コンフリクトが出たら**（タスクブランチは承認時点の topic を起点にしたままなので、
   その後に別のタスクが入っていると起きる）:
   - 解消してから、**検証より先にまずビルド**を流す。機械的な解消は共有末尾を含む hunk で
     閉じ括弧を落として構文を壊すことがあり、ビルドが最速で見つける。
   - コンフリクトにならない**片側だけの新規ファイル**が相手側の変更（新しいフィールド等）を
     欠いていないか、全体のコンパイル（テストのビルドを含む）で洗い出す。
   - そのうえで検証コマンド一式と外形動作をフルで流す。
   - **意図が読めない hunk は、そのタスクの引き継ぎノートを読む**
     （`<ベース>/notes/task<番号>/impl-*.md` に、何をどう変えたかとその理由が残っている）。
     ワークフローのエージェントは終了していて質問できない。
   - 解消の規模が大きい・判断を伴うなら、単発の fix エージェント（`Agent`、`model: "opus"`、
     `isolation: "worktree"`、`run_in_background: false`）に解消を任せ、別の単発レビュー
     エージェントに解消差分を見せてよい。
     **マージ自体は任せない**（解消済みブランチを受け取ってリードがマージする）。
   - **リードがコンフリクト解消でコードを書いたら、台帳と最終 PR の「自律判断の記録」に書く**
     （リードがコードに触れる唯一の場面）。
6. ビルドが落ちたら、原因のブランチを特定して単発 fix エージェント（worktree）と独立レビューで
   直させ、直ったブランチを取り込み直す。直せなければ `git reset --hard` で外して、ユーザーへ
   上げる材料にする（先に取り込んだ分は topic に残る）。
7. `git push` で topic を前進させる。

## §3. PR の状態を確かめる

1. push した直後はタスク PR が OPEN のままのことがある。15〜30 秒待って
   `gh pr list --base topic/<作業名> --state open` を確かめる。
2. **OPEN が残っていたら**:
   - `git merge-base --is-ancestor origin/<タスクブランチ> origin/topic/<作業名>` で実体が
     入っているか確かめる
   - 成果物の実在を実物（テストの本数・生成物）で確かめる
   - 実体が入っているのに OPEN なら、ブランチ先端が古いマージコミット等に置き換わった
     stale tip なので、経緯のコメントを付けて PR をクローズしブランチを消す
   - 実体が入っていなければ取り込み漏れなので §2 に戻る

## §4. 後始末（取り込みを終えてから行う）

ワークフローは正常に終わっても `wf_<runId>-*` の worktree を残すことがある。**自分が起動した
run のものだけ**を、runId で名指しして消す。パターンで一括削除しない（他プロセスの worktree を
消した事故がある）。

1. まず何が残っているかを見る。`runId` は `Workflow` の返り値にあり、台帳に控えてある
   （[ledger.md](ledger.md)）。

   ```bash
   ~/.claude/skills/supervisor/scripts/worktree.py list --run <runId>
   ```

   `dirty`（未コミットの変更がある）と `locked`（まだ生きているエージェントが掴んでいる）が
   表示される。**`locked` が 1 つでもあれば run はまだ終わっていない。** 終了通知を待つ。
2. 未コミットの変更が残っていたら、消す前に保全する。

   ```bash
   ~/.claude/skills/supervisor/scripts/worktree.py rescue \
     --path <worktree のパス> --branch <タスクブランチ>
   ```

   その worktree で `git add -A` → commit → `git push origin HEAD:refs/heads/<タスクブランチ>` を
   行う（コミットメッセージは `wip: 中断時点の保全（検証未実施）` 固定。保全であって完成の宣言では
   ないことを履歴に残すため）。クリーンなら何もせず `rescued: false` を返す。保全したら台帳に
   1 行残し、立て直すときの `resumeFrom.sha` に**この SHA は未検証である**ことを添える。
3. 取り込みを終えたら消す。

   ```bash
   ~/.claude/skills/supervisor/scripts/worktree.py remove --run <runId> --merged
   ```

   `--merged` は「このタスクを topic へ取り込み終えた」というリードの明示である。打ち切った
   タスクは `--aborted` で消せる。どちらも無いとスクリプトが拒む。未コミットの変更が残って
   いるものも拒むので、その場合は先に手順 2 を実行する。
4. `git rev-parse <デフォルトブランチ> origin/<デフォルトブランチ>` の一致を確かめる。ずれて
   いたら worktree のエージェントが共有側のポインタを動かした事故なので
   `git branch -f <デフォルトブランチ> origin/<デフォルトブランチ>` で戻す。
5. メインツリーの HEAD がブランチに載っているか確かめる（`git rev-parse --abbrev-ref HEAD` が
   `HEAD` なら detached。`git checkout <デフォルトブランチ>` で戻す）。

**引き継ぎノート（`<ベース>/notes/`）はここで消さない。** 立て直しと最終 PR の作成まで使う。
消すのは `SKILL.md` §8 の最後、ユーザーに確認してからである。

## §5. 台帳を更新して次を出す

1. 台帳（[ledger.md](ledger.md)）のそのタスクの行を `merged` に更新し、`shouldFix` の件数・
   `decisions`・`deferrals` を写す。台帳は `.git` 配下にあるので commit しない。
2. **後続タスクを起動する直前には、検証コマンド一式と外形動作をフルで 1 回流す**（ビルド
   だけでは論理的な衝突が残る。後続はこの topic を起点にするので、ここで確かめる）。
3. 空いた枠に、`blockedBy` が解けているタスクのワークフローを起動する。

## 残った should-fix の回収

`should-fix` は承認の条件ではないので、裁定エージェントが残件として畳んだものが残る。
**全タスクが終わってからまとめて 1 本のブランチで回収する**（件数が多くてもブランチを分けない。
往復が 1 回で済む）。

1. 各タスク PR のスレッド全件から `should-fix` を拾う。**裁定が畳んでいるので `--all` が要る。**

   ```bash
   ~/.claude/skills/supervisor/scripts/gh-review.py threads --pr <PR 番号> --all
   ```

   出力の `severity` フィールドが `should-fix` のスレッドが対象（スクリプトが先頭コメントから
   読み取って返す）。`severity` が `null` の手書きスレッドだけは本文を読んで判断する。
2. **topic の実コードで未解決であることを確かめてから**回収する（レビューの後に直っている
   ことがあるので、スレッドの記述だけを根拠にしない）。
3. 未解決の分を 1 本の残件ブランチにまとめ、単発 fix エージェント（`Agent`、`model: "sonnet"`、
   `isolation: "worktree"`、`run_in_background: false`、PR 作成まで）に直させる。
4. 小さい差分ならリード自身の diff 確認で足りる。ロジックを含むならレビューエージェントを
   1 体走らせる。
5. §2 の手順で取り込む。
6. 大きい残件は回収せず、最終 PR の「残課題」としてユーザーに示す。
