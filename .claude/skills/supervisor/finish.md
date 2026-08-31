# 仕上げ（§8）と自律判断の記録（§9）

**リードが最後に 1 回だけ読む節である。** 全タスクが `stacked` か `blocked` になったら、
`SKILL.md`「7. 回す」を抜けてここへ来る。立ち上げ（起動前の確認と §1〜§6）は
[lead-setup.md](lead-setup.md) にある。

## 8. 仕上げる

1. **全タスクが `stacked` か `blocked` になっていることを確かめ、実物と突き合わせる。**
   `<スクリプト>/state.py show --base <ベース>` の各タスクの `status` と、
   `<スクリプト>/stack.py show --tree <スタックツリー>` の `branches` を見比べる。
   `stacked` のタスクごとに stacked PR の中にブランチが 1 本あること。数が合わなければ積み漏れである。
   **`needs_rebase` が空であることも確かめる**——1 件でもあれば
   `stack.py append --continue` で積み替えを終わらせる。**`stale_record` も空であること**
   ——ここに何か出ていたら追跡情報が実物とずれているので、先に
   [ledger.md](ledger.md)「stacked PR の追跡情報が壊れたとき」で直す（`branches` の SHA は
   追跡情報に記録された位置なので、ずれたまま読むと積み漏れの判断を間違える）。
2. **`trunk_moved: true` なら `stack.py sync` を通す。次の手順の検証より先に行う。**
   走行中に base ブランチが進んだ印で、失敗ではない。**`gh stack sync` をユーザーに任せない**
   ——別の場所で通されるとローカル ref と追跡情報が取り残され、土台の空コミットが落ちることもある
   （design-notes.md「gh stack v0.1.0 で確かめたこと」）。

   ```bash
   <スクリプト>/stack.py sync --tree <スタックツリー> --trunk <base>
   ```

   - **`blockers` が返ったら 1 つも動いていない。** `trunk-checked-out-elsewhere` は
     **自分で直さない**——ユーザーの worktree が base ブランチを握っていて、sync はその HEAD を
     動かす。worktree のパスと両方の SHA を報告して、更新してもらう。
   - `bottom` の `ok` が false なら、土台が空コミット 1 つでなくなっている。**直すには force push が
     要るので、中身をユーザーに示して判断を渡す**（このままマージすると同じ subject のコミットが
     base ブランチに 1 つ増える）。
3. stacked PR の先頭を checkout した状態で、**`<スタックツリー>` の中で**`<ベース>/brief.md` の
   検証コマンド一式と外形動作をフルで 1 回流す。**`run_in_background: true` で投げ、待つ間に 4 の
   散文を書き進める**（終了すると通知が届く。検証結果は 4 の `prose/verification.md` に書くので、
   6 の `gh pr ready` より前に受け取っていればよい）。

   ```bash
   git -C <スタックツリー> symbolic-ref --short HEAD    # stacked PR の先頭であることを確かめる
   ```

4. **最終版の散文を書く。** `<ベース>/prose/` の 4 ファイルに `Write` で書く
   （書式は [stack-pr.md](stack-pr.md)「最終版の本文」。書く直前にそこを Read する）。

   | ファイル | 中身 |
   | --- | --- |
   | `prose/behavior.md` | 変更による挙動の変化。**観測できる事象で書く** |
   | `prose/checklist.md` | 確認項目。操作手順と観測できる結果 |
   | `prose/verification.md` | 手順 3 の検証結果。コマンドと結果を 1 行ずつ、外形動作も |
   | `prose/decisions.md` / `prose/deferrals.md` | 作業全体に関わる自律判断（§9） |

5. **台帳と stack PR 本文を最終版にする。** タスク一覧・残課題・自律判断の記録は
   `state.json` から書き出されるので、書き写さない。

   ```bash
   <スクリプト>/state.py render --base <ベース> --final
   gh pr edit <stackPR番号> --body-file <ベース>/stack-pr-body.md
   ```

   **台帳は commit されないので、ユーザーが残る形で読める記録はこの本文だけになる。**
6. **`stacked` が 1 件以上なら `gh pr ready <stackPR番号>` でレビュー可能にする。**
   タスク PR は [integration.md](integration.md) §2 でリードが非 draft で作るので、これで
   stacked PR 全体がマージできる状態になる。
   **マージはしない**（ユーザーが行う）。1 件も無ければ `ready` を叩かず draft のまま残す
   （土台のブランチも消さない。あとで続きを頼まれたときの足場になる）。
7. **マージの手順をユーザーに渡す。** stacked PR は**下から順に**マージする決まりで、`gh stack merge` が
   まとめて行う（all-or-nothing。1 本でもマージできなければ 1 本も入らない）。

   ```bash
   gh stack merge <stackPR番号>            # stacked PR 全体を下から順に
   gh stack merge <途中の PR 番号>          # そこまでで止める
   ```

   - **リードは叩かない。** 外向きで後戻りしにくい操作はユーザーが握る
     （design-notes.md「壊してはいけない線引き」）。
   - 個別にマージしてもよい。1 本マージすると GitHub が上の PR の base を自動で張り替える。
   - **`gh stack sync` は手順 2 で通してある。** マージまでに base ブランチがさらに進んで
     もう一度通すことになったら、**同じクローンで `gh stack checkout <stackPR番号>` から引き直して
     から**通すよう伝える。別のクローンで通すとそちらのローカル ref だけが取り残される。
   - 指摘を受けて直すときは、その**タスクブランチに commit して push** し、
     `gh stack rebase` → `gh stack push` で上の段を揃える（`gh stack` の追跡情報は
     スタックツリーの中にある。手順 9 でスタックツリーを外すと、ユーザーは自分の作業ツリーで
     `gh stack checkout <stackPR番号>` から始めることになる）。
8. 何が stacked PR に載ったか・失敗で残ったタスク・自分の判断で変えた目標・先送りにした作業・
   残課題をユーザーにまとめる。**stack PR の URL を必ず添える。**
9. スタックツリーを外す。**state.json・台帳・引き継ぎノート・`gh stack` の追跡情報はこの中に
   あるので一緒に消える。** 消してよいか**ユーザーに確認してから**行う。マージが済む前に消すと、
   ユーザーが追加を頼んだときに再開の足場が無い。**残すと言われたらスタックツリーも残す。**

   ```
   ExitWorktree({ action: "keep" })
   ```

   ```bash
   git -C <スタックツリー> status --porcelain     # 空でなければ中身をユーザーに示す
   git worktree remove .claude/worktrees/supervisor-<作業名>
   ```

   `action: "keep"` にするのは、`path` で入った worktree を `ExitWorktree` が消さない仕様だから
   である（消すのは次の `git worktree remove`）。未コミットの変更があると `git worktree remove` は
   拒む。**`--force` を先に付けず、何が残っているかを示す**（コンフリクト解消の途中で終わって
   いた可能性がある）。**`status` が空でも state.json と台帳は消える**——無視されたファイルは
   `status` に出ず、`git worktree remove` にも拒まれない。

## 9. 自律判断を記録する

**この節は §8 だけでなく、ループの最中にも当てはまる。** ユーザーに確認せず自分で決めたことは、
判断内容と判断材料（根拠・退けた代替案）を残す。とくに次の 2 つは**必ず**残す。

- 最終目標・DoD・スコープを自分の判断で変えた
- やる予定だった作業を先送りにした、または対象外にした

書き先は 2 つに分かれる（詳細は [ledger.md](ledger.md)「自律判断をどこに書くか」）。

| 誰の判断か | 書き先 |
| --- | --- |
| ワークフローが返した `decisions` / `deferrals` | `state.py set --task <番号> --decision "…" --deferral "…"` |
| リードが自分で下した、作業全体に関わる判断 | `<ベース>/prose/decisions.md` / `prose/deferrals.md` |

どちらも `state.py render` が台帳と stack PR 本文の「自律判断の記録」に載せる。
**台帳は commit しないので、`render` と `gh pr edit` を省かない。**
