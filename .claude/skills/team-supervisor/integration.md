# 統合レーン（リード本体が行う）

サブリーダーから承認報告を受けたら、**その 1 本をその場で topic へ取り込む**。溜めない。

**すべてリード本体が自分の作業ツリーで行う。** サブエージェントにマージや PR のクローズを
指示すると安全クラシファイアに確率的に遮断される。同じ理由で
**`gh pr merge` はリードでも使わない**。
`git merge` + `git push` で取り込めば、GitHub は head コミットが base に到達したことを検出して
タスク PR を自動的に MERGED 判定にする。

§1 → §5 の順に行う。**後始末（§4）が取り込み（§2）より後ろにあるのは順序が大事だから**
である。worktree を消すとそのサブリーダーは `SendMessage` で再開できなくなり、§2 手順 5 の
「意味の衝突を本人に聞く」経路が使えなくなる。

## §1. 報告を実物で確かめる（動く前に必ず）

報告は真正性の証拠にならない。実行中の run に「完了」通知が誤って発火し、PR 番号・マージ・
失敗談まで含む**精巧な捏造レポート**が届いた実績がある。報告に基づいて動く前に:

1. **ブランチ・コミット・PR を確かめる。** `git fetch origin` から始めて、ブランチがリモートに
   実在するか、`origin/<base>..origin/<タスクブランチ>` にコミットが載っているか（0 件なら成果が
   無い）、PR の head と base が報告どおりで state が OPEN かを 1 回で見る。

   ```bash
   ~/.claude/skills/team-supervisor/scripts/lane.py verify \
     --branch <タスクブランチ> --base topic/<作業名> --pr <PR 番号>
   ```

   終了コードが 0 でなければ取り込まない（どの項目が合わなかったかが `checks` に出る）。
2. **承認の門を通す。** 未解決スレッド・PENDING レビュー・要求した役割のレビューが提出
   されたかを確かめる。**`--require-roles` にそのタスクの `tier` どおりの役割を渡す**
   （台帳の `tier` を見る。省略するとスクリプトが止まる）。

   ```bash
   # standard
   ~/.claude/skills/team-supervisor/scripts/gh-review.py gate --pr <PR 番号> \
     --require-roles review:normal,review:adversarial

   # light
   ~/.claude/skills/team-supervisor/scripts/gh-review.py gate --pr <PR 番号> \
     --require-roles review:normal
   ```

   終了コードが 0 でなければ取り込まない（`missing_roles` と未解決スレッドの一覧が
   表示される）。PENDING に残ったスレッドは未解決件数に現れないので、両方を見るこの
   コマンドを使う。**`verify` はこれを呼ばない**（GitHub のレビュー状態は gh-review.py が
   一手に扱う）ので、2 つを続けて叩く。

   **未解決スレッドが 0 件であることは「レビューが行われた」ことを意味しない。** レビューを
   1 度も走らせていない PR ではスレッドがそもそも 0 件になるので、`--require-roles` が
   サブリーダーの自己申告を裏付ける唯一の機械的な検査になる（`verify` はブランチ・
   コミット・PR の実在だけを見て、レビューを見ない）。

   終了コードを読むときは**パイプに繋がない**（`| head` に繋ぐと `$?` が `head` のものに
   なる）。

どちらかが非 0 なら取り込まない。**サブリーダーが生きていても終わっていても `SendMessage` で
差し戻す**（終わっていた場合はトランスクリプト全件を復元して再開する。`SKILL.md` §7）。
**この時点で worktree を消してはならない**——消すと再開できなくなる（§4）。

## §2. 取り込む（1 本ずつ・並列にしない）

**並列にマージするとコンフリクトする。** 承認が同時に来ても 1 本ずつ処理する。

1. `git fetch origin` し、topic を checkout して `git pull` で最新化する。
2. **二重取り込みを防ぐ**: `git merge-base --is-ancestor origin/<タスクブランチ> origin/topic/<作業名>`
   が成功したら、既に入っているのでそのブランチは飛ばす（再開・再実行時に効く）。
3. `git merge --no-ff origin/<タスクブランチ>` で取り込む。
4. **コンフリクトが出なければ、ここではビルドだけを流す**（`<ベース>/brief.md` のビルドコマンド。
   `<ベース>` は `lane.py base-dir --work <作業名>` が返すパス）。
   そのブランチの検証一式はレビュー subagent が承認前に自分の worktree で流しているので、
   ここでのフル検証は §5 の 2 で改めて行う。
5. **コンフリクトが出たら**（タスクブランチは承認時点の topic を起点にしたままなので、
   その後に別のタスクが入っていると起きる）:
   - 解消してから、**検証より先にまずビルド**を流す。機械的な解消は共有末尾を含む hunk で
     閉じ括弧を落として構文を壊すことがあり、ビルドが最速で見つける。
   - コンフリクトにならない**片側だけの新規ファイル**が相手側の変更（新しいフィールド等）を
     欠いていないか、全体のコンパイル（テストのビルドを含む）で洗い出す。
   - そのうえで検証コマンド一式と外形動作をフルで流す。
   - **意味の衝突は、そのブランチを作ったサブリーダーに聞く。** 報告を終えて止まっていても
     `SendMessage(to: "task<番号>", ...)` でトランスクリプト全件ごと再開するので、
     「この hunk はどちらの意図か」を本人に確かめられる（`SKILL.md` §7）。
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

サブリーダーの worktree は `<リポジトリ>/.claude/worktrees/agent-<agentId>` にある。agentId は
spawn の返り値にあり、台帳に控えてある（[ledger.md](ledger.md)）。変更が無ければ自動で消え、
未コミットの変更があれば残る。

**worktree を消すと、そのサブリーダーを `SendMessage` で再開できなくなる。** 再開すべきか
どうかの判断基準ではなく、不可逆な作用である。実際に返るエラー:

```
Agent "task2" could not be resumed: This agent cannot be resumed: its worktree no longer
exists, and the fallback directory is not covered by the session's isolation fences.
```

だからこの節は §2（取り込み）と §3（PR の状態確認）の**後ろ**にある。コンフリクトの解消中は
§2 手順 5 で「この hunk はどちらの意図か」をサブリーダー本人に聞くので、その間 worktree が
残っている必要がある。

消してよいのは、**topic への取り込みを終えたタスク**と、再開を 3 回試して打ち切ったタスク
（`.git/team-supervisor/blocked-<agentId>` がある）だけ。

1. **サブリーダーの終了通知が届いてから消す。** 報告を終えてもまだ終了していないことがあり、
   その間は git が worktree をロックしている。`git worktree list` で残留を確かめ、
   **agentId を 1 つずつ指定して**消す。

   ```bash
   ~/.claude/skills/team-supervisor/scripts/lane.py wt-remove --agent <agentId> --merged
   ```

   `--merged` は「このタスクを topic へ取り込み終えた」というリードの明示である。打ち切った
   タスク（`blocked-<agentId>` がある）は `--merged` なしで消せる。どちらでもない worktree は
   スクリプトが拒む。未コミットの変更が残っていても拒むので、その場合は先に保全する（下記）。
   **パターンで一括削除しない**（他のプロセスの worktree を消した事故がある）。

   **`reason: "locked"` が返ったら、その worktree の subagent がまだ生きている。** 終了通知を
   待って同じコマンドを再実行する（他の失敗と区別できるようスクリプトが理由を分けて返す）。
2. `git rev-parse <デフォルトブランチ> origin/<デフォルトブランチ>` の一致を確かめる。ずれて
   いたら worktree のエージェントが共有側のポインタを動かした事故なので
   `git branch -f <デフォルトブランチ> origin/<デフォルトブランチ>` で戻す。
3. メインツリーの HEAD がブランチに載っているか確かめる（`git rev-parse --abbrev-ref HEAD` が
   `HEAD` なら detached。`git checkout <デフォルトブランチ>` で戻す）。

### 未コミットの成果を保全する

落ちたサブリーダーの worktree に未コミットの変更が残っていたら、消す前に保全する。

```bash
~/.claude/skills/team-supervisor/scripts/lane.py wt-rescue \
  --agent <agentId> --branch <タスクブランチ>
```

その worktree で `git add -A` → commit → `git push origin HEAD:refs/heads/<タスクブランチ>` を
行う（コミットメッセージは `wip: 中断時点の保全（検証未実施）` 固定。保全であって完成の宣言では
ないことを履歴に残すため）。クリーンなら何もせず `rescued: false` を返す。

保全したら台帳に 1 行残し、再開したサブリーダーへ「前コミット `<SHA>` は未検証の保全コミットで
ある」と伝える。SHA は出力の `commit` にある。

## §5. 台帳を更新して次を出す

1. 台帳（[ledger.md](ledger.md)）のそのタスクの行を `merged` に更新し、`findings` の件数・
   `decisions`・`deferrals` を写す。台帳は `.git` 配下にあるので commit しない。
2. **後続タスクを spawn する直前には、検証コマンド一式と外形動作をフルで 1 回流す**（ビルド
   だけでは論理的な衝突が残る。後続はこの topic を起点にするので、ここで確かめる）。
3. 空いた枠に、`blockedBy` が解けているタスクを spawn する。

## 残った should-fix の回収

should-fix は承認の条件ではないので、topic に入った後も残る。**全タスクが終わってから
まとめて 1 本のブランチで回収する**（件数が多くてもブランチを分けない。往復が 1 回で済む）。

1. 各タスク PR のスレッド全件から `should-fix` を拾う。

   ```bash
   ~/.claude/skills/team-supervisor/scripts/gh-review.py threads --pr <PR 番号> --all
   ```

   出力の `severity` フィールドが `should-fix` のスレッドが対象（スクリプトが先頭コメントから
   読み取って返す）。`severity` が `null` の手書きスレッドだけは本文を読んで判断する。
2. **topic の実コードで未解決であることを確かめてから**回収する（レビューの後に直っている
   ことがあるので、スレッドの記述だけを根拠にしない）。
3. 未解決の分を 1 本の残件ブランチにまとめ、単発 fix エージェント（`model: "sonnet"`、
   `isolation: "worktree"`、`run_in_background: false`、PR 作成まで）に直させる。
4. 小さい差分ならリード自身の diff 確認で足りる。ロジックを含むならレビューエージェントを
   1 体走らせる。
5. §2 の手順で取り込む。
6. 大きい残件は回収せず、最終 PR の「残課題」としてユーザーに示す。
