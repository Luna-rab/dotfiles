# 知見の台帳

今の autodev（main の `dist/dot-claude/skills/autodev/`）から拾った知見を、作り直しに持ち込むものと持ち込まないものに分ける。
持ち込むのは、進め方を変えても同じく効く外部の仕組みとインフラの事実と、モデルの扱い方として効いた書き方の工夫だけである。進め方の規則（停滞・回数・レビューの体数・完了チェックの項目など）は持ち込まず、新しい設計で決め直す。
出典は、旧実装が残っている固定のコミット `56b72fa` のファイルで、`56b72fa:` に続けて `path:行番号` を書く（main は作り直しをマージすると旧実装を指さなくなるので、ブランチ名では書かない）。path は、`test/` で始まるものはリポジトリの根から、ほかは `dist/dot-claude/skills/autodev/` から数える。`git show 56b72fa:dist/dot-claude/skills/autodev/<path>`（`test/` なら `git show 56b72fa:<path>`）で引く。「ARCH」は [ARCHITECTURE.md](ARCHITECTURE.md)、「DM」は [DOMAIN_MODEL.md](DOMAIN_MODEL.md)。

## 持ち込む

「扱う: §」は、新しい設計書にすでに書いてあることを表す。それ以外は、実装のときにその部品で扱う。ID の欠番は、事実ではない値や振る舞いだったので「持ち込まない」（N-90 から）へ移したもの。

### claude -p（AgentRuntime）

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| AR-01 | `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`ANTHROPIC_BASE_URL` は変数ごと消して起動する（空文字でも「設定あり」と読む相手がいる）。残すとサブスクリプションではなく従量課金になる。`CLAUDE_CODE_OAUTH_TOKEN` は claude 自身のものなので通す（無人のマシンでは `claude setup-token` で作って置く） | 56b72fa:README.md:139, 56b72fa:DESIGN.md:12-15, 56b72fa:scripts/autodevlib/app/stage_call.py:93-107, 56b72fa:scripts/autodevlib/ports/proc.py:61-64 | 扱う: ARCH §10・DM §13 AgentRuntime |
| AR-02 | `claude setup-token` のトークンは API キーとして使えない（素の HTTP では `HTTP 429: rate_limit_error`、message は `Error` の 1 語） | 56b72fa:DESIGN.md:379-382 | AgentRuntime（`claude -p` の外から使わない） |
| AR-03 | プロンプトは標準入力から渡す。`--allowedTools` は可変長で、次のオプションまで後ろの引数を全部取る（末尾のプロンプトがツール名として飲まれる。並び順しだいで通るので見つけにくい）。ツールの一覧はカンマでつないで 1 引数にする | 56b72fa:DESIGN.md:388-391, 56b72fa:scripts/autodevlib/ports/runner.py:149-151 | 扱う: DM §11.5 ④・§13 AgentRuntime |
| AR-04 | `--session-id` で id を呼ぶ側が決め、続きは `--resume <id>`。stream-json の入力に書いた `session_id` は無視される | 56b72fa:DESIGN.md:399-400, 56b72fa:DESIGN.md:409, 56b72fa:scripts/autodevlib/ports/runner.py:170, 56b72fa:scripts/autodevlib/app/stage_call.py:114 | 扱う: DM §4 SessionId・§13 AgentRuntime |
| AR-05 | `--input-format stream-json` で起動して標準入力を開いたままにすると、走行中に `control_request` を送れる（SDK 専用ではない） | 56b72fa:DESIGN.md:401-405, 56b72fa:scripts/autodevlib/ports/runner.py:27-30 | AgentRuntime の中断 |
| AR-06 | 標準入力へ `{"type":"control_request",…,"request":{"subtype":"interrupt"}}` を送るとターンが打ち切られ、result（`error_during_execution` / `aborted_streaming`）が返る。kill と違って usage と停止理由が残る | 56b72fa:DESIGN.md:263-271, 56b72fa:scripts/autodevlib/ports/runner.py:32-34 | 扱う: DM §13 AgentRuntime |
| AR-07 | 送る user メッセージには uuid を振る。振らないと interrupt の応答の `still_queued` / `cancelled` が空で返る | 56b72fa:DESIGN.md:401-405, 56b72fa:scripts/autodevlib/ports/runner.py:177-178 | AgentRuntime |
| AR-08 | `cancel_queued` は `system/init` の `capabilities` に `interrupt_cancel_queued_v1` があるときだけ付ける。無い CLI は黙って無視する。版の文字列で比べない（公式もそう指示している） | 56b72fa:DESIGN.md:271-273, 56b72fa:scripts/autodevlib/ports/runner.py:34-36, 56b72fa:scripts/autodevlib/ports/runner.py:377-379 | AgentRuntime |
| AR-09 | `system/init` はターンごとに出る。1 回だけ来る前提で待つと 2 ターン目から詰まる | 56b72fa:DESIGN.md:408 | AgentRuntime |
| AR-10 | result を見たら標準入力を閉じる。閉じないと claude は次の入力を待って終わらない。閉じてもしばらく終わらないことがあるので、待つ時間を決めて kill する（今は 30 秒） | 56b72fa:DESIGN.md:406-407, 56b72fa:scripts/autodevlib/ports/runner.py:38-39, 56b72fa:scripts/autodevlib/ports/runner.py:277, 56b72fa:scripts/autodevlib/ports/runner.py:287-293 | AgentRuntime |
| AR-11 | interrupt を送っても result が返らないことがある。今のコードは 60 秒（`GRACE`）ごとに送り直すだけで、標準出力が開いている間は kill しない（コメントの「terminate する」と食い違う） | 56b72fa:scripts/autodevlib/ports/runner.py:75-76, 56b72fa:scripts/autodevlib/ports/runner.py:311-316 | 扱う: DM §13 AgentRuntime（一定時間で kill する） |
| AR-12 | 読むのは result イベント 1 つでよい（`subtype`・`is_error`・`stop_reason`・`num_turns`・`result`・`usage`・`total_cost_usd`・`permission_denials`）。`is_error` の理由は `result` か `api_error_status` に載る | 56b72fa:DESIGN.md:410-412, 56b72fa:scripts/autodevlib/ports/runner.py:399-434 | AgentRuntime（⑤ の証拠。`RateLimited` の見分けもここで決める） |
| AR-13 | 引数の誤りは標準エラーにだけ出て、claude は JSONL を 1 行も出さずに終わる。result が無ければ標準エラーを理由にする | 56b72fa:scripts/autodevlib/ports/runner.py:211-214 | AgentRuntime（`logs/` に残し `StageFailed` の理由にする） |
| AR-14 | `--max-turns` を超えると終了コード 1・`subtype: error_max_turns`・`terminal_reason: max_turns` で終わる | 56b72fa:DESIGN.md:239, 56b72fa:DESIGN.md:327-329, 56b72fa:DESIGN.md:395-396, 56b72fa:scripts/autodevlib/ports/runner.py:11-12 | AgentRuntime・StageSpec（ターンの上限の欄を足す） |
| AR-15 | `--json-schema` は生成時の制約ではなく事後の検証で、外れた出力の言い直しが `--max-turns` の予算を食う | 56b72fa:scripts/autodevlib/ports/runner.py:22-23 | StageSpec（結果を返すステージのターンの上限を決めるとき） |
| AR-16 | `--json-schema` の検証に失敗しても、終了コード 0・`subtype: success`・`is_error: false` のまま `structured_output` が空で返ることがある（実測）。「スキーマを満たせない」と自由記述で説明して正常終了することもある | 56b72fa:DESIGN.md:330-332, 56b72fa:scripts/autodevlib/ports/runner.py:23-25, 56b72fa:scripts/autodevlib/ports/runner.py:201-205 | 扱う: DM §7.1・§6.7・§11.5 ⑤ |
| AR-17 | `--json-schema` にはファイルパスではなく JSON の本文を渡す（パスだと `--json-schema is not valid JSON` で起動前に落ちる）。draft-07 だけで、`$schema` に 2020-12 を書くと拒まれる。渡すと StructuredOutput ツールが増え、最後の応答がその形になる | 56b72fa:DESIGN.md:319-320, 56b72fa:DESIGN.md:333-335, 56b72fa:scripts/autodevlib/app/stage_call.py:156 | AgentRuntime・StageSpec |
| AR-18 | 結果は構造化出力で受け、ステージにファイルを書かせない（在ることと形を driver が保証できる）。本文も結果の `body` で返させ、別のファイルに書かれても読まない | 56b72fa:DESIGN.md:337-338, 56b72fa:contracts/pr-body.md:68, 56b72fa:contracts/summary.md:142, 56b72fa:scripts/autodevlib/app/stage_call.py:279-281 | 扱う: ARCH §10・DM §4 Guard（worktree の外に書かせない） |
| AR-19 | モデルが報告欄の「無い」を `null` ではなく文字列の `"null"` や `"none"` で返すことがある | 56b72fa:scripts/autodevlib/app/build.py:173-177, 56b72fa:scripts/autodevlib/app/build.py:258, 56b72fa:test/autodev/test_escalation.py:471 | 実行器 ⑤（結果の読み方） |
| AR-20 | `--permission-mode bypassPermissions` で確認を挟まずに走る。cwd の外も読み書きでき、`--add-dir` は要らない。ツール 1 回ごとの承認を同期で挟むと、答える相手がいないと進めない | 56b72fa:DESIGN.md:275-277, 56b72fa:DESIGN.md:397-398, 56b72fa:scripts/autodevlib/ports/runner.py:19-20, 56b72fa:scripts/autodevlib/app/stage_call.py:175-176 | AgentRuntime（外への書き込みは HK-11 で止める） |
| AR-21 | defer で止まったセッションを再開するときはプロンプトを渡さない。渡すと新しいターンが始まり、止まったツール呼び出しが再開されない（実測）。interrupt で止めたセッションを `--resume` するとき何を渡せば続きから進むかは、実測が無い | 56b72fa:DESIGN.md:44-45, 56b72fa:scripts/autodevlib/ports/runner.py:301-302, 56b72fa:scripts/autodevlib/app/stage_call.py:214-216 | 扱う: DM §13 AgentRuntime・ARCH §14 |
| AR-22 | 2 回続けて落ちたセッションを持ち越すと、回答しても同じセッションで落ち続ける。続けるときはセッションを捨てて新しく立てる | 56b72fa:DESIGN.md:177-178, 56b72fa:scripts/autodevlib/app/design.py:352-357, 56b72fa:test/autodev/test_design.py:346 | 実行器（セッションを続ける統括・Judge・DesignJudge・Impl） |
| AR-23 | ステージのエラーの多くは一時的（ネットワーク）で、1 回呼び直すと通る | 56b72fa:scripts/autodevlib/app/stage_call.py:302-305 | 扱う: DM §11.5 ⑥ |
| AR-24 | JSONL は走りながら 1 行ずつ読み、その場でログに書く（打ち切っても記録が残る）。渡したプロンプトと必須ルールもログに残す（再開ではプロンプトを渡さないので、最初の指示が消える）。同じステージを 2 度呼ぶことがあるので、ログを上書きしない | 56b72fa:DESIGN.md:253-254, 56b72fa:scripts/autodevlib/ports/runner.py:283-285, 56b72fa:scripts/autodevlib/app/stage_call.py:141-152, 56b72fa:scripts/autodevlib/app/stage_call.py:243-250, 56b72fa:scripts/autodevlib/config/paths.py:193-195 | AgentRuntime・DM §14 `logs/`（名前は DM §4 ExecutionId で分かれる） |
| AR-25 | 監視の誤り（例外）でステージを止めない。打ち切りを決める監視は driver のコードで、モデルは入らない | 56b72fa:DESIGN.md:370-371, 56b72fa:scripts/autodevlib/ports/runner.py:41, 56b72fa:scripts/autodevlib/ports/runner.py:104-106, 56b72fa:scripts/autodevlib/ports/runner.py:354-361 | 実行器 ④ |
| AR-26 | PreToolUse のフックは stream-json にイベントを出さない。拒まれた呼び出しは次の user イベントの `tool_result` に `is_error: true` と「`PreToolUse:<ツール> hook error`」で始まる本文で残る（Claude Code 2.1.281）。`hook_response` の `exit_code` を数えると SessionStart のフックの失敗まで数える。`tool_result` の本文は文字列のことも `{"type": "text"}` の並びのこともある | 56b72fa:scripts/autodevlib/core/events.py:11-14, 56b72fa:scripts/autodevlib/core/events.py:32, 56b72fa:test/autodev/test_events.py:19-39 | 実行器の監視（ガードに止められた回数） |
| AR-27 | フックに何度も止められるステージは指示書を読み違えていて、ターンの上限まで使っても直らない（今は 10 回で打ち切る） | 56b72fa:DESIGN.md:260-261, 56b72fa:GLOSSARY.md:42-43, 56b72fa:scripts/autodevlib/app/stage_call.py:21-23, 56b72fa:scripts/autodevlib/app/stage_call.py:183-187 | 実行器の監視（打ち切る回数は決め直す） |
| AR-28 | 走っているステージの進み具合（ターン数・直前のツール）は、ステージ 1 回で数百のイベントが流れるので、毎回ではなく数秒ごと（今は 5 秒）に書く | 56b72fa:DESIGN.md:258-259, 56b72fa:scripts/autodevlib/app/stage_call.py:24-25, 56b72fa:scripts/autodevlib/app/stage_call.py:170-171, 56b72fa:scripts/autodevlib/app/context.py:64-69, 56b72fa:scripts/autodevlib/app/context.py:93-94 | 扱う: ARCH §8・DM §7.4・§14 `progress/` |
| AR-29 | ステージ 1 回の固定費は約 26k トークン（システムプロンプト）が下限で、4 ファイルのリポジトリでも 1 ステージの入力は 238,000 前後だった。計画 1 回で入力が 20 万トークンを超える | 56b72fa:DESIGN.md:413-415, 56b72fa:scripts/autodevlib/app/planning.py:141-145, 56b72fa:scripts/autodevlib/app/stage_call.py:218-219 | 統括を起こす費用の見積もり（ARCH §6）・Revise を続きのセッションにする理由（DM §11.1） |
| AR-30 | モデルは別名（`opus` / `sonnet`）で渡すと新しいモデルに自動で乗る | 56b72fa:scripts/autodevlib/config/stages.py:12-13 | StageSpec（モデル） |
| AR-31 | `docker agent` の `harness: type: claude-code` は type / effort / model しか受けず、ツールの制限もターンの上限も指定できない | 56b72fa:DESIGN.md:375-378 | AgentRuntime（`claude -p` を直に起動する理由の 1 つ） |

### PreToolUse のフックとガード

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| HK-01 | フックは `--settings <file>` で外から渡す。worktree に `.claude/settings.local.json` を置くと commit に混ざるので、`guard.json` は worktree の外に置く。設定はランの頭で 1 回書けばよく、何を止めるかはステージごとに渡す | 56b72fa:DESIGN.md:383-385, 56b72fa:scripts/autodevlib/ports/runner.py:15, 56b72fa:scripts/autodevlib/ports/repo.py:149-156, 56b72fa:test/autodev/test_paths.py:51 | 扱う: ARCH §10・DM §14 `guard.json` |
| HK-02 | PreToolUse のフックが終了コード 2 を返すと呼び出しは起きず、標準エラーに書いた理由がモデルに渡る | 56b72fa:DESIGN.md:385, 56b72fa:hooks/deny-writes.py:20, 56b72fa:hooks/deny-writes.py:130-132 | ガードのフック |
| HK-03 | `bypassPermissions` でも PreToolUse のフックは走る（実測） | 56b72fa:hooks/deny-writes.py:4-5 | ガードのフック |
| HK-04 | `--disallowedTools` はツールをセッションから消し（bypassPermissions でも効く）、宛先で分けられない。消せるのは名前のあるツールだけで、Bash のリダイレクトで書く道は残る | 56b72fa:DESIGN.md:243-246, 56b72fa:DESIGN.md:386-387, 56b72fa:scripts/autodevlib/ports/runner.py:43-45, 56b72fa:scripts/autodevlib/config/stages.py:56-59, 56b72fa:hooks/deny-writes.py:16-18, 56b72fa:hooks/deny-writes.py:70-74 | 扱う: ARCH §10（フックで止める） |
| HK-05 | フックの `matcher` は `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash`。MultiEdit は `edits[].file_path`、NotebookEdit は `notebook_path` に宛先がある | 56b72fa:scripts/autodevlib/ports/repo.py:161, 56b72fa:hooks/deny-writes.py:77-83 | ガードのフック |
| HK-06 | Bash のコマンド行は正規表現でなめず `shlex` でトークンに割る。引用の中の `>`・`->`・`rm` はシェルに届かない（`python3 -c "print('rm -rf app.py')"` など）。引用が閉じていなければ空白で割り、止める方へ倒す | 56b72fa:hooks/deny-writes.py:46-48, 56b72fa:hooks/deny-writes.py:94-99, 56b72fa:test/autodev/test_autodev_deny_writes.py:151 | ガードのフック |
| HK-07 | リダイレクトの宛先とコマンド行のパスを混ぜない（混ぜると `grep -rn x src README.md 2>/dev/null` が「README.md へ書く」になる）。宛先から `/dev/…` と fd の複製（`\d*>&\d*`。`2>&1`）を外す。`> file` のように離れた形・`>app.py`・`>>`・`1>` は宛先として拾う | 56b72fa:DESIGN.md:392-394, 56b72fa:hooks/deny-writes.py:50-51, 56b72fa:hooks/deny-writes.py:102-118, 56b72fa:test/autodev/test_autodev_deny_writes.py:151, 56b72fa:test/autodev/test_autodev_deny_writes.py:164 | ガードのフック |
| HK-08 | 引数のファイルを書き換えるコマンド（`tee` `mv` `cp` `rm` `truncate` `dd` `patch`、`sed -i`、`git checkout` / `git restore`）がトークンに現れたら、コマンド行のパスらしい文字列を全部宛先とみなす | 56b72fa:hooks/deny-writes.py:54-57, 56b72fa:hooks/deny-writes.py:121-127, 56b72fa:contracts/implementation.md:19-20, 56b72fa:test/autodev/test_autodev_deny_writes.py:67 | ガードのフック |
| HK-10 | フックでは Bash 越しの書き込みを完全には見つけられず、ファイルの中身（スタブか実装か）も見分けられない。最後の砦は git の差分（コミット数・テストの差分） | 56b72fa:DESIGN.md:248-249, 56b72fa:hooks/deny-writes.py:43-44, 56b72fa:scripts/autodevlib/app/build.py:97-101, 56b72fa:test/autodev/test_autodev_deny_writes.py:84 | ガードのフック・GateEvaluator |
| HK-11 | 今のフックは worktree の中だけを見て、外への書き込みを通す（ステージがランディレクトリに結果を書いていたため）。bypassPermissions で走るステージは `events.db` も書き換えられる | 56b72fa:hooks/deny-writes.py:8-9, 56b72fa:hooks/deny-writes.py:152-154, 56b72fa:scripts/autodevlib/ports/run_store.py:3-5, 56b72fa:test/autodev/test_autodev_deny_writes.py:124 | 扱う: ARCH §10・DM §4 Guard |
| HK-12 | ステージに「PR を作らない・`gh` を呼ばない・push しない・履歴を動かさない（`git rebase`・`git reset --hard`・force push）」と頼んでいるのは文面だけで、フックでは止めていない。`gh` の認証も git の資格情報も、同じユーザーの設定から読める | 56b72fa:scripts/autodevlib/core/prompt.py:28-32, 56b72fa:contracts/implementation.md:90, 56b72fa:test/autodev/test_prompt.py:168 | 扱う: ARCH §10・DM §4 Guard（`gh` と `git push`。rebase・reset を止めるかは実装で決める） |
| HK-13 | git は書き込み権を追跡しない（実行ビットだけ）ので、テストファイルの書き込み権を落としても差分に出ない。落とせるのは `git ls-files` に載ったファイルだけで、新しく作るファイルはフックだけが止める。中断で戻し損ねたら再開の前に戻す | 56b72fa:scripts/autodevlib/ports/repo.py:181-202, 56b72fa:scripts/autodevlib/app/build.py:183-193 | ガード（二重の栓に使うなら） |
| HK-14 | フックと完了チェックは同じ glob の照合を使う。別々に書くと、フックが通したものを完了チェックが落とす（または逆の）ずれが出る | 56b72fa:scripts/autodevlib/core/globs.py:3-4, 56b72fa:test/autodev/test_globs.py:87-93 | GlobPattern（照合を 1 か所に置く） |
| HK-15 | `**` を含む glob は `fnmatch` だけでは扱えない。末尾からの照合（`PurePath.match`）・パス全体（`fnmatch`）・末尾が `/` のディレクトリ名の 3 通りで見る。`**/tests/**` はリポジトリ直下の `tests/` にも当てる。名前が前方一致するだけのディレクトリには当てない。`\` 区切りと先頭の `./` を正規化してから照合する | 56b72fa:scripts/autodevlib/core/globs.py:6-10, 56b72fa:scripts/autodevlib/core/globs.py:38-51, 56b72fa:test/autodev/test_globs.py:49-82 | GlobPattern |
| HK-17 | golden ファイルと snapshot の置き場もテストのパスに入れる。外れていると、実装が期待値を書き換えられる | 56b72fa:contracts/plan.md:183-184, 56b72fa:contracts/testgen.md:110-111 | GlobPattern・ガード |
| HK-18 | フックに glob を環境変数で渡すなら改行で区切る（glob に空白が入りうる）。渡らなかったら既定に戻し、テストを無防備にしない | 56b72fa:scripts/autodevlib/core/globs.py:62-63, 56b72fa:test/autodev/test_globs.py:97, 56b72fa:test/autodev/test_globs.py:103 | ガードのフック |
| HK-19 | フックは claude の子プロセスとして別に起動される。ファイルが無い・import に失敗しても driver は落ちず、ガードが黙って消える | 56b72fa:test/autodev/test_assets.py:48, 56b72fa:test/autodev/test_layers.py:286 | ガードのフック（実在と起動を検査で確かめる） |
| HK-20 | PreToolUse のフックが `defer` を返すと、claude は終了コード 0・`subtype: success` のまま終わり、result に `stop_reason: tool_deferred` と `deferred_tool_use` が載る。`claude -p --resume` すると、同じツール呼び出しで PreToolUse がもう一度走る（実測） | 56b72fa:hooks/park-on-ask.py:4-7, 56b72fa:scripts/autodevlib/ports/runner.py:129-131, 56b72fa:scripts/autodevlib/ports/runner.py:423-427 | 扱う: DM §11.1「計画ステージの ask」・§9.2 |
| HK-21 | `defer` が効くのは、そのターンのツール呼び出しが 1 つだけのとき。ほかのツールと一緒に呼ばれると通常の権限評価に落ちる。指示書で ask を 1 ターンで単独に呼ばせる | 56b72fa:hooks/park-on-ask.py:14-16, 56b72fa:scripts/autodevlib/cli.py:246-247, 56b72fa:contracts/plan.md:96, 56b72fa:contracts/plan.md:107-108 | 扱う: DM §11.1・計画ステージの契約書 |
| HK-22 | フックの判断の優先順位は deny > defer。書き込みを拒むフックと並べても順番を気にしなくてよい | 56b72fa:scripts/autodevlib/ports/repo.py:167-169 | ガードのフック |
| HK-23 | ask の呼び出しは入口のパスで見分ける（`python3` を前に置いた形も）。ファイル名が合わないと何も止まらず、ステージはコマンドの失敗を受けて別の道を取る | 56b72fa:hooks/park-on-ask.py:32-34, 56b72fa:test/autodev/test_autodev_park_on_ask.py:32-48 | ガードのフック（ask） |
| HK-24 | defer するか通すかを回答のファイルの実在だけで決め、中身を解釈しなければ、何度再開しても同じ判断になる。回答は質問と別のディレクトリに置く（同じ場所だと聞いた瞬間に再開する）。回答を id で引くなら id を使い回さない（前の回答が黙って返る） | 56b72fa:hooks/park-on-ask.py:12, 56b72fa:scripts/autodevlib/config/paths.py:164-166, 56b72fa:scripts/autodevlib/app/planning.py:105-108, 56b72fa:DESIGN.md:45-46, 56b72fa:test/autodev/test_paths.py:57 | 扱う: DM §11.1・§14 `answers/` |
| HK-25 | defer の実測: 止まるまで 9 ターン $0.519、回答後 4 ターン $0.907。blocked を返して計画を全部やり直す経路は 20 ターン $1.204 を 2 回払った | 56b72fa:DESIGN.md:30-37, 56b72fa:DESIGN.md:48-50, 56b72fa:contracts/plan.md:96-105 | 扱う: ARCH §13「計画ステージの ask」 |

### git・gh・gh stack（Git・Forge）

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| GH-01 | base との差分が 0 のブランチでは `gh pr create` が `No commits between …` で落ちる。概要ブランチに空のコミットを 1 つ載せる | 56b72fa:DESIGN.md:88, 56b72fa:scripts/autodevlib/ports/repo.py:52-53, 56b72fa:scripts/autodevlib/ports/repo.py:66-67, 56b72fa:scripts/autodevlib/app/publish.py:20-21 | 扱う: DM §11.4 CutBranch |
| GH-02 | ランディレクトリを手で消しても git 側の worktree の登録は残り、ブランチが「別の場所でチェックアウト中」になって作り直せない。worktree を作る前に `git worktree prune` を通す | 56b72fa:DESIGN.md:23-26, 56b72fa:scripts/autodevlib/ports/repo.py:55-58 | Git・CutBranch |
| GH-03 | ブランチを作るときの `already exists`、worktree を足すときの `already used by worktree` は、作り済みとして通す（呼び直しで同じ結果にする） | 56b72fa:scripts/autodevlib/ports/repo.py:59-65 | Git・CutBranch（ARCH §9「決定的なステージの再開」） |
| GH-04 | worktree を消すと、無視されたファイルも一緒に消える（実測）。消す前に push が済んでいることを確かめる | 56b72fa:scripts/autodevlib/ports/repo.py:113-117 | Git（`clean`・`purge`・破棄） |
| GH-05 | `gh stack` のローカルの追跡は worktree ごとに別で、別の worktree で `gh stack view` を叩くと終了コード 2 の "not part of a stack" になる。ステージは worktree を cwd にして走るので、計画ステージより先に worktree が要る | 56b72fa:scripts/autodevlib/ports/repo.py:3-5, 56b72fa:scripts/autodevlib/app/drive.py:3-4, 56b72fa:scripts/autodevlib/app/drive.py:66-67 | 扱う: DM §11.4・§14 `trees/overview/`・§6.2 |
| GH-06 | `gh stack link` はローカルの追跡に依らない（help に "designed for users who manage branches with external tools"）。PR は `gh pr create` で自分のタイトルと本文で作り、link で連ねる。`gh stack submit --auto` は自動生成のタイトルになり、非対話では draft で作られる（`--open` を付けない限り） | 56b72fa:DESIGN.md:416-418, 56b72fa:scripts/autodevlib/ports/forge.py:10-17 | 扱う: DM §11.4 CreatePR・StackLink |
| GH-07 | `gh stack link` には `--base` を必ず渡す。省くと一番下の PR の base がリポジトリの既定ブランチに書き換わり、スタックに入った PR は `gh pr edit --base` で戻せない。つないだ後に概要 PR の base がランの base のままかを確かめる | 56b72fa:DESIGN.md:419-421, 56b72fa:scripts/autodevlib/ports/forge.py:117-118, 56b72fa:scripts/autodevlib/app/publish.py:156-176, 56b72fa:test/autodev/test_task_pr_body.py:89-95 | 扱う: DM §11.4 StackLink |
| GH-08 | `gh stack link` には PR を 2 つ以上渡す。link は足すだけなので、概要 PR から一番上まで全部を下から順に渡してよい | 56b72fa:scripts/autodevlib/ports/forge.py:114-121, 56b72fa:scripts/autodevlib/app/publish.py:141-144 | 扱う: DM §11.4 StackLink・Relink |
| GH-09 | `gh stack link` が落ちても PR は作れている。呼び直しでは同じブランチの PR を使う | 56b72fa:scripts/autodevlib/app/publish.py:145-146 | 扱う: ARCH §9・DM §11.4 CreatePR |
| GH-10 | 概要 PR が draft の間は、上のタスク PR もマージできない。これが「マージしない」の栓になる | 56b72fa:DESIGN.md:139-140, 56b72fa:templates/overview-pr-body-minimal.md:1, 56b72fa:scripts/autodevlib/app/publish.py:34 | 扱う: ARCH §11・DM §6.5 |
| GH-11 | push 済みのブランチを別の親に積み直すと、`gh stack rebase` と force push が要る。今の作りは順に 1 本ずつ積むので要らなかった | 56b72fa:DESIGN.md:422 | 扱う: ARCH §7・§13（新しいブランチ名で切り直す） |
| GH-12 | 新しいタスクの番号は使った番号と重ねない。捨てたタスクのブランチは残るので、同じ名前で作ると古いコミットの上に乗る | 56b72fa:scripts/autodevlib/core/task_order.py:87-88, 56b72fa:scripts/autodevlib/ports/repo.py:71-75, 56b72fa:test/autodev/test_task_order.py:156 | 扱う: DM §4 TaskId・BranchName |
| GH-14 | `gh pr create` の出力の最後の行の URL（`/pull/<番号>`）から PR 番号を取る | 56b72fa:scripts/autodevlib/ports/forge.py:28, 56b72fa:scripts/autodevlib/ports/forge.py:125-130 | Forge |
| GH-15 | 起動前に `claude`・`git`・`gh auth status`・`gh extension list` の `gh-stack` を全部確かめ、1 つでも足りなければ走らない。途中で気づくと worktree と概要 PR だけが残る | 56b72fa:README.md:140, 56b72fa:README.md:160, 56b72fa:scripts/autodevlib/ports/forge.py:35-42, 56b72fa:scripts/autodevlib/cli.py:26-39 | cli の `autodev run`（終了コード 1） |
| GH-16 | base の既定は origin の既定ブランチ（`refs/remotes/origin/HEAD`）。取れなければ origin の main・master の順に探し、無ければ main | 56b72fa:scripts/autodevlib/ports/repo.py:30-38 | StartRun の変換層 |
| GH-17 | ラン名の重複は、origin に概要ブランチが在るかでも見る（ランディレクトリを消してもリモートに残る）。見る前に fetch する | 56b72fa:scripts/autodevlib/cli.py:69-73 | StartRun の変換層 |
| GH-19 | テストを後から変えたかを git の差分で見るなら、基準はテストを書いたコミットで、親ブランチではない。親から見ると、テストを書いたコミット自体がテストの差分になる | 56b72fa:DESIGN.md:423-425, 56b72fa:scripts/autodevlib/core/verdict.py:152-156, 56b72fa:scripts/autodevlib/app/build.py:85-86, 56b72fa:test/autodev/test_verdict.py:195-202 | 扱う: DM §4 ArtifactRef（`tests` の `testsAt`） |
| GH-20 | PR テンプレートは `.github/`・`docs/`・リポジトリ直下を大文字小文字を区別せずに探す。`.github/PULL_REQUEST_TEMPLATE/` に複数あれば 1 つを選ぶ | 56b72fa:contracts/summary.md:23-28 | WriteOverview の契約書 |

### 文面とテンプレート

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| TX-01 | 本文を `string.Template` に通すと `$$` が `$` になり、`${tasks}` のような文字列が消える。ステージが書いた本文はテンプレートに通さず、マーカーは決まった文字列として探して置き換える | 56b72fa:DESIGN.md:298-300, 56b72fa:scripts/autodevlib/ports/templates.py:15-17, 56b72fa:scripts/autodevlib/core/markdown.py:70-71, 56b72fa:scripts/autodevlib/app/publish.py:64, 56b72fa:test/autodev/test_markdown.py:219, 56b72fa:test/autodev/test_overview_body.py:134 | RefreshOverview |
| TX-02 | `safe_substitute` は埋め忘れたマーカーをそのまま残す（例外で落ちない）。テンプレートに `$` をそのまま出すには `$$` と書く。埋めた値の中の `$`・`${…}` を再展開しない | 56b72fa:scripts/autodevlib/ports/templates.py:12-13, 56b72fa:scripts/autodevlib/core/prompt.py:207, 56b72fa:test/autodev/test_prompt.py:110, 56b72fa:test/autodev/test_prompt.py:125 | テンプレートを埋める部品・プロンプトの組み立て |
| TX-03 | マーカーは 1 回の走査で置き換え、差した中身は読み直さない。1 つずつ置き換えると、タスクの題や判断ログに入ったマーカーの文字列・`\1`・`$$` まで置き換わる | 56b72fa:DESIGN.md:301-302, 56b72fa:scripts/autodevlib/core/markdown.py:73-74, 56b72fa:test/autodev/test_markdown.py:232 | RefreshOverview |
| TX-04 | 置き換えるのは 1 行に単独で置いたマーカーだけ（行の前後の空白は許し、同じマーカーが 2 回あれば両方）。本文や指示がマーカーをインラインコードで説明している所まで置き換えると、表のセルや文の中に表が入る | 56b72fa:DESIGN.md:303-304, 56b72fa:scripts/autodevlib/core/markdown.py:70-78, 56b72fa:contracts/summary.md:121, 56b72fa:test/autodev/test_markdown.py:196, 56b72fa:test/autodev/test_markdown.py:201 | RefreshOverview |
| TX-05 | 一覧に無いマーカーは残し、マーカーが無い本文には足さない（置くかはステージが決め、driver は検査しない） | 56b72fa:DESIGN.md:296-297, 56b72fa:scripts/autodevlib/core/markdown.py:91-94, 56b72fa:test/autodev/test_markdown.py:213, 56b72fa:test/autodev/test_markdown.py:227 | RefreshOverview |
| TX-06 | 数と状態は毎回状態から組み立て、ステージには書かせない（出所が 2 つになり片方が古くなる）。ステージの本文はマーカー入りのまま保存し、書き出すたびに保存した本文から埋め直す（埋めた本文で上書きすると、後から積んでも古いまま残る） | 56b72fa:DESIGN.md:141-143, 56b72fa:DESIGN.md:285-287, 56b72fa:scripts/autodevlib/ports/templates.py:6-10, 56b72fa:scripts/autodevlib/app/publish.py:60-62, 56b72fa:test/autodev/test_overview_body.py:153 | 扱う: DM §11.4 WriteOverview・RefreshOverview |
| TX-07 | 表のラベルに無い状態は、隠さずにそのまま出す（隠すと表から 1 行消える） | 56b72fa:test/autodev/test_markdown.py:58 | RefreshOverview・StatusQuery |
| TX-08 | Python の中に Markdown を書かず、文面は `templates/` か agent が書いたものに置く | 56b72fa:DESIGN.md:281-283 | アダプタの規約 |
| TX-09 | ステージへ渡す文面にマーカー（`${`）を 1 つも残さない（残るとステージがパスとして扱う） | 56b72fa:test/autodev/test_prompt.py:64 | プロンプトの組み立て（DM §11.5 ③） |

### ファイル・プロセス・設定

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| FP-01 | コマンドはシェルを通さない（ラン名がそのまま引数に入るので、空白や引用符で組み変わる）。検証コマンドだけは `bash -lc` で流す（パイプやリダイレクトを含みうる） | 56b72fa:scripts/autodevlib/ports/proc.py:36-37, 56b72fa:scripts/autodevlib/ports/evidence.py:60-61 | Git・Forge・ProcessRunner |
| FP-02 | 「指示の文面が入る余地は無い」としていたが、実際には計画・再計画ステージ（LLM）が返した検証コマンドも driver の権限で `bash -lc` に流している | 56b72fa:scripts/autodevlib/ports/evidence.py:60-61, 56b72fa:scripts/autodevlib/app/planning.py:195 | 扱う: ARCH §7・§13（受け入れる） |
| FP-04 | ファイルは一時ファイルに書いてから置き換える。途中で落ちても壊れた中身が残らず、読む側（Monitor・HUD）が書きかけを読まない | 56b72fa:scripts/autodevlib/ports/files.py:12-17, 56b72fa:hooks/park-on-ask.py:82-85 | 反応（質問・回答・進み具合のファイル） |
| FP-05 | driver もフックも標準ライブラリだけで動く（install.sh を通したどのマシンでも `python3` で動く） | 56b72fa:scripts/autodevlib/ports/proc.py:3, 56b72fa:test/autodev/test_layers.py:286 | driver とガードのフック（`sqlite3` も標準ライブラリ） |
| FP-06 | 置き場（スキルの根）は階層を数えて上らず、`SKILL.md` を探して決める。数えると、ファイルを動かしたとき黙ってずれる（5 つのパスが実在しない所を指したまま、ruff・ty・pytest・CLI の起動が全部通った） | 56b72fa:scripts/autodevlib/config/paths.py:58-72, 56b72fa:hooks/deny-writes.py:31-39 | driver とフックのパスの解決 |
| FP-07 | 指示書とスキーマのパスは文字列の連結なので、実在は検査でしか分からない。全ステージで指示書とスキーマ（`type: object` で `properties` を持つ JSON）が読め、プロンプトが組めることを確かめる | 56b72fa:test/autodev/test_assets.py:57, 56b72fa:test/autodev/test_assets.py:65, 56b72fa:test/autodev/test_prompt.py:205 | StageSpec の検査 |
| FP-08 | ランの置き場は `$XDG_STATE_HOME/autodev/<ラン名>/`（既定 `~/.local/state`）。`AUTODEV_STATE_DIR` で根を差し替えられる（検査で `~/.local/state` を汚さない） | 56b72fa:scripts/autodevlib/config/paths.py:31-37, 56b72fa:test/autodev/test_paths.py:63 | 扱う: DM §14（差し替えの変数は実装で足す） |
| FP-09 | リポジトリ固有の設定（検証コマンド・テストのパス・変更禁止パス）は `$XDG_CONFIG_HOME/autodev/repos/<スラッグ>.json` に人が書き、driver は読むだけ。ランをまたいで使い回す。スラッグはパスの `/` を `__`、`:` を `_` にする | 56b72fa:DESIGN.md:437, 56b72fa:scripts/autodevlib/config/paths.py:40-55 | リポジトリごとの設定（ARCH §5 の「テストが要らないパス」も同じ所） |
| FP-10 | ランの作業に合わせて決めた値はランに閉じ、リポジトリ共通の設定に書かない（次のランの既定値になり、無関係な作業を止める） | 56b72fa:scripts/autodevlib/app/inputs.py:22-27, 56b72fa:test/autodev/test_config.py:60 | PlanApplier |
| FP-11 | 結果の「空の一覧」（無い）と「キーを省く」（前の値のまま）を分ける。`or` で選ぶと、空の一覧を返しても前の値が残る | 56b72fa:scripts/autodevlib/app/planning.py:193-197, 56b72fa:contracts/plan.md:181-187, 56b72fa:test/autodev/test_config.py:66, 56b72fa:test/autodev/test_config.py:73 | 結果の取り込み・PlanApplier |
| FP-12 | ラン名は英小文字・数字・`-` の 1〜49 字で、先頭は `-` でない。ブランチ名と置き場のパスに入るので、`../x`・`/` を含む名前・大文字・空白・`_` を拒む | 56b72fa:SKILL.md:27, 56b72fa:GLOSSARY.md:11, 56b72fa:scripts/autodevlib/config/paths.py:28, 56b72fa:scripts/autodevlib/config/paths.py:97-101, 56b72fa:test/autodev/test_paths.py:86-90 | RunName（DM §4 は字の種類だけなので、字数と先頭を足す） |
| FP-13 | driver がステージの途中で落ちると「走っている」記録が残る。1 つの変更を 2 回に分けて保存すると、間で落ちたとき同じ提案を二度写す | 56b72fa:scripts/autodevlib/cli.py:101-102, 56b72fa:scripts/autodevlib/app/replan.py:205-206 | 扱う: DM §7.4（`interrupted` として扱う・1 コマンドを 1 トランザクション） |

### 契約書（モデルの扱い方）

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| CT-01 | 破ると取り返しがつかない必須ルールだけを `--append-system-prompt` に置く。ユーザーのメッセージに混ぜると、長い指示書とコードを読む間に薄まる。必須ルールを指示書の要約にしない（出所が 2 つになり、片方だけ直す事故が起きる）。必須ルールでは役割を名乗らせ、次のステージを自分で呼ばないと書く | 56b72fa:DESIGN.md:357-359, 56b72fa:GLOSSARY.md:58-59, 56b72fa:scripts/autodevlib/core/prompt.py:5-6, 56b72fa:scripts/autodevlib/core/prompt.py:25-26, 56b72fa:scripts/autodevlib/core/prompt.py:116-120, 56b72fa:test/autodev/test_prompt.py:196 | 実行器 ③ |
| CT-02 | 指示書の本文はプロンプトに入れず、パスだけを渡して読ませる。プレースホルダ表を添え、指示書を読む前に指す先を確定させる（無いとプレースホルダのままコマンドを叩き、値が無いと推測する）。指示書が使う名前は表と揃える。cwd が worktree でそこから出ないことも書く | 56b72fa:DESIGN.md:357-359, 56b72fa:templates/prompt.md:1-17, 56b72fa:scripts/autodevlib/core/prompt.py:7-11, 56b72fa:scripts/autodevlib/core/prompt.py:201, 56b72fa:test/autodev/test_overview_body.py:311, 56b72fa:test/autodev/test_overview_body.py:344 | 実行器 ③ |
| CT-03 | 結果の形の出所はスキーマのファイル 1 か所で、指示書は各キーの意味だけを書き、形は書かない。統括の判断の JSON も同じ | 56b72fa:DESIGN.md:322-323, 56b72fa:scripts/autodevlib/config/paths.py:88-90 | StageSpec・統括の契約書 |
| CT-04 | 結果の誤り（綴り・許されない遷移・無い id）は静かに捨てず、理由をステージに返す。黙って消すと次の回で誰も気づけない | 56b72fa:scripts/autodevlib/ports/review_store.py:3-5, 56b72fa:scripts/autodevlib/ports/review_store.py:40-41 | 扱う: DM §7.1（拒否はステージのセッションに理由を返す） |
| CT-05 | 報告（設計に無い形が要る・テストが仕様と矛盾する）を返すステージは、何も作らないことがある | 56b72fa:contracts/testgen.md:33-36, 56b72fa:contracts/implementation.md:31-38, 56b72fa:test/autodev/test_escalation.py:462 | 扱う: DM §6.7・§7.2 ReportStageResult（報告を成果物の確認より先に見る） |
| CT-06 | 質問は「どちらに解釈すべきか」を選択肢の形で書かせ、聞きたいことが複数なら id を分けて 1 つずつ聞かせる | 56b72fa:contracts/plan.md:103-105, 56b72fa:contracts/judge.md:77, 56b72fa:contracts/design-judge.md:66 | 計画ステージ・ラン統括（`ask-user`）の契約書 |
| CT-07 | 長いテストや fuzz を待つ前に commit させる。待つ間に打ち切られると、成果だけが worktree に残り、報告が失われる | 56b72fa:contracts/implementation.md:78-82, 56b72fa:scripts/autodevlib/core/prompt.py:68-70 | 書くステージの契約書 |
| CT-08 | 進行（何を何回呼ぶか・合否）をモデルに持たせると、完了チェックを飛ばして「通った」と言える。進行状態もモデルに書かせない | 56b72fa:DESIGN.md:114-115, 56b72fa:README.md:173, 56b72fa:scripts/autodevlib/ports/run_store.py:3-5 | 扱う: ARCH §11 |
| CT-09 | ステージの報告を完了の根拠にしない。実行中のランに「完了」通知が誤って届き、PR 番号・マージ・失敗談まで含む精巧な捏造レポートだったことがある。件数や failing の自己申告も信じない | 56b72fa:DESIGN.md:129-133, 56b72fa:scripts/autodevlib/core/verdict.py:3-4, 56b72fa:scripts/autodevlib/app/review_loop.py:108-110, 56b72fa:schemas/testgen.json:16-19 | 扱う: DM §11.5「Task はステージの報告を信じない」 |
| CT-10 | テストを通すためにテストを緩める経路は、実装・修正にテストを書かせる限り残る。指摘を直した本人が閉じられると「未解決 0 件」が自己承認になる。出力を承認する側が実装も書き換えると、出力に合わせて期待値を決めたのか区別できない | 56b72fa:DESIGN.md:119-128, 56b72fa:scripts/autodevlib/app/build.py:3-5, 56b72fa:scripts/autodevlib/ports/review_store.py:7-9, 56b72fa:scripts/autodevlib/config/stages.py:53-55 | 扱う: ARCH §11・DM §11.2 のガード |
| CT-11 | 実装の報告・PR の説明・コミットメッセージ・計画が書いた判断は主張として扱わせ、実物を読ませる。「安全」「意図どおり」と刷り込むフレーミングで検出率が 97% から 3.6% に落ちる | 56b72fa:contracts/review.md:13-15, 56b72fa:contracts/design-review.md:20-23, 56b72fa:contracts/design-review.md:32-33 | レビューの契約書 |
| CT-12 | 誤誘導のコメントは LLM をほとんど騙せない（検出率の変化は -5%〜+4%）。見逃しは脆弱性の型で決まるので、競合・TOCTOU・タイミング・複雑な認可・失敗の経路・境界を狙わせる | 56b72fa:contracts/review-adversarial.md:17-28, 56b72fa:contracts/review.md:20-35 | レビューの契約書 |
| CT-13 | 裏の取れない推論だけの指摘は、既存のテストやコマンドの再現で裏づけさせる。敵対的なレビュアー 10 人が存在しない脆弱性を一致して認め、1 回のテスト実行だけが退けた事例がある | 56b72fa:contracts/design-review.md:54-55, 56b72fa:contracts/review.md:65-73, 56b72fa:contracts/review-adversarial.md:30-38, 56b72fa:contracts/review-adversarial.md:60 | レビューの契約書 |
| CT-14 | レビュー同士を議論させない（debate はバイアスを増幅する）。独立に立てさせ、突き合わせは別のステージがする（meta-judge 型の集約のほうが頑健） | 56b72fa:contracts/review.md:99-100, 56b72fa:contracts/review-adversarial.md:59, 56b72fa:contracts/judge.md:42-44 | ReviewLoop（並列のレビューに互いの指摘を渡さない） |
| CT-15 | レビューに前の結論や前の版を見せると、それがフレーミングになって検出が落ちる | 56b72fa:DESIGN.md:134-137, 56b72fa:contracts/review.md:4, 56b72fa:contracts/design-review.md:7-8, 56b72fa:scripts/autodevlib/core/prompt.py:150-153 | 扱う: ARCH §11（毎ラウンドまっさら）・DM §11.1 DesignReview |
| CT-16 | まっさらのレビューに却下済みの論点を渡さないと、却下した論点を重大度を上げて立て直す（issue #27 の r12 → r21）。渡すのは結論ではなく論点なので、引きずられる害は小さい | 56b72fa:DESIGN.md:137-138, 56b72fa:contracts/design-review.md:8-9, 56b72fa:scripts/autodevlib/app/design.py:147-151 | 扱う: DM §11.1 DesignReview |
| CT-17 | 設計は直すたびに一段細かい所に新しい指摘が立ち、同じ指摘を数える停滞の検知に掛からない（1 回 $10〜30 のラウンドが回り続けた。issue #27）。nit 1 件のために書き直すと $5〜30 かかり、書き足した所に次の指摘が立つ | 56b72fa:DESIGN.md:64-66, 56b72fa:scripts/autodevlib/core/review_policy.py:21-23, 56b72fa:scripts/autodevlib/app/design.py:13-15 | DesignLoop（止め方は決め直す） |
| CT-18 | 設計に内部の型の欄・既存テストの追随・エッジケースの規約まで書かせると、設計レビューが詰めにきて往復が終わらない | 56b72fa:DESIGN.md:159-161, 56b72fa:contracts/plan.md:84-87, 56b72fa:contracts/design-review.md:11-13, 56b72fa:contracts/design-review.md:46-52 | Plan・DesignReview の契約書 |
| CT-19 | 見えているテストを飽和させても、同じ機能を別の組み合わせで呼ぶと落ちる（可視テストと held-out テストの差は 43〜48pp）。外から見える振る舞いを確かめさせる | 56b72fa:contracts/testgen.md:15-18, 56b72fa:contracts/testgen.md:66-77 | テストを書くステージの契約書 |
| CT-20 | 期待値を空けるべきテストを仮の値で埋めると、実装がその値に合わせにいく | 56b72fa:contracts/testgen.md:105-109, 56b72fa:contracts/testgen.md:155 | テストを書くステージの契約書 |
| CT-21 | ドキュメントの追記を受入条件に入れると、文字列を読むテストになる。ドキュメントや本番のソースをファイルとして読むテストは、実装の前に落ちるかでは見分けられない（書く前なら落ちる）。足したテストごとに「落とす本番の変更は何か」を答えさせる。検証コマンドにドキュメントの grep を入れない（言い換えで落ち、挙動は何も確かめない） | 56b72fa:contracts/plan.md:57-62, 56b72fa:contracts/plan.md:179-180, 56b72fa:contracts/testgen.md:79-90 | 計画・テストを書くステージの契約書 |
| CT-22 | 「この形の値が返る」しか書けない受入条件は、設計どおりに作ったかしか確かめない。受入条件は設計ファイルではなく指示から書かせる | 56b72fa:contracts/plan.md:47-48, 56b72fa:contracts/design-review.md:27-28 | 計画・設計レビューの契約書 |
| CT-23 | モデルは報告せずにテストを避けて通す実装（特定の入力だけ特別扱い・演算子の差し替え・状態を記録して返す）を書くことがある | 56b72fa:contracts/implementation.md:39-41, 56b72fa:contracts/review-adversarial.md:27-28 | 実装・レビューの契約書 |

### CLI と /autodev

| ID | 知見 | 出典 | 扱う部品 |
| --- | --- | --- | --- |
| CL-01 | 入口の `autodev.py` は PATH に無いので、絶対パスで起動しないと見つからない | 56b72fa:SKILL.md:30-31 | 新しい SKILL.md |
| CL-02 | 1 タスクで 30 分以上かかるので、`autodev run` はバックグラウンドで走らせる | 56b72fa:SKILL.md:37 | 新しい SKILL.md |
| CL-03 | `/autodev` に足りない引数は推測で埋めずユーザーに聞く。指示は受入条件が書ける文にしてから渡す | 56b72fa:SKILL.md:22, 56b72fa:SKILL.md:28 | 新しい SKILL.md |
| CL-04 | `/autodev` のエージェントは、ステージを務めず、合否を決めず、テストを書き換えず、PR を作らず、`gh pr merge` / `gh stack merge` を呼ばない。進行状態を手で書き換えない | 56b72fa:SKILL.md:17, 56b72fa:SKILL.md:79-83 | 新しい SKILL.md・扱う: DM §13 EventStore |
| CL-05 | 既にあるランに `--instruction` を付けて呼ばれたら止める。黙って捨てると、呼んだ側は指示を足したつもりになる | 56b72fa:SKILL.md:67-70, 56b72fa:scripts/autodevlib/cli.py:82-87, 56b72fa:test/autodev/test_escalation.py:746 | 扱う: ARCH §9（既にあるラン名なら再開する）・cli の `autodev run` |
| CL-07 | 作業は対象リポジトリの外の worktree で行い、手元のブランチと作業中のファイルに触らない | 56b72fa:README.md:22-23, 56b72fa:DESIGN.md:451-452 | 扱う: ARCH §10 |
| CL-08 | 機械が読む出力は標準出力、進行の報せは標準エラーに出す | 56b72fa:scripts/autodevlib/ports/console.py:20-21 | cli |
| CL-09 | 端末で全角は 2 桁なので、`str.ljust` では列がそろわない（幅を超える文字列は切らない） | 56b72fa:scripts/autodevlib/app/finish.py:17-20, 56b72fa:test/autodev/test_finish_table.py:8-13 | cli の表示 |
| CL-10 | 呼んだエージェントと HUD は終了コードと `status` で分岐する。終了コードの意味が変わったら SKILL.md と HUD の読み方も直す | 56b72fa:SKILL.md:46, 56b72fa:SKILL.md:74-75, 56b72fa:scripts/autodevlib/app/context.py:15-19, 56b72fa:scripts/autodev.py:23-27 | 扱う: ARCH §9・§12（HUD の読み口） |
| CL-13 | 文書・コード・画面では用語集の語だけを使い、同じものを別の語で呼ばない | 56b72fa:GLOSSARY.md:3-4 | 扱う: DM §2 |

## 持ち込まない

今の作りの規則は、そのままでは新しい設計書に置かない。新しい設計の考え方から決めたものには、行の末尾に印を付ける。

- 「新設計で採用: §」: 新しい設計の考え方から見ても残す理由があり、理由を付けて設計書に置いた（一部だけなら「一部を新設計で採用」）
- 「新設計で決め直した: §」: 同じ問題を、新しい設計で別のやり方に決めた
- 「新設計では採らない: §」: 採らないと決め、その代わりを設計書に書いた

印の無い行の規則は、新しい設計書に無い。要ると分かったら、新しい設計の考え方から理由を書いて足し、ここに印を付ける。

| ID | 何の規則だったか |
| --- | --- |
| N-01 | 終了コード 2 は「計画が blocked」、3 は「要対応のタスクがある」（新設計で決め直した: ARCH §9） |
| N-02 | `status` の `outcome` の値（planning / running / waiting / held / stacked）と、`held` を要確認・失敗のタスクから決める規則 |
| N-03 | 質問は `/autodev` もステージも答えず、受入条件の解釈は必ずユーザーが決める。推測で進めない（新設計で決め直した: ARCH §13「受入条件の曖昧さ」・DM §8.2） |
| N-04 | 質問が複数なら全部回答してから呼び直す。回答が揃うまで終了コード 4 で待ち続ける（新設計で決め直した: ARCH §6・§9） |
| N-05 | ステージが聞けるのは計画ステージだけ。回答はタスクの notes に入り、概要 PR の判断ログにも載る（一部を新設計で採用（聞けるのは計画ステージだけ・回答は出どころ付きで notes に入る）: DM §4 `Decision`・§8.2） |
| N-06 | 計画が blocked を返したら終了コード 2。設計を直す途中の blocked は人に聞いて続ける（新設計で決め直した: ARCH §9・DM §11.1 の ask） |
| N-07 | 計画がタスクを 1 件も返さなければ止める。ラン共通の検証コマンドが空なら設計レビューの前に止める（一部を新設計で採用（初回の計画はタスクを 1 件以上返す。止めるのではなく指示書で求め、拒むのはドメイン）: contracts/_proposal.md。実装タスクが 1 つも無いと `AllTasksSettled` が出ず、ランが終わらないため。検証コマンドが空なら止める規則は採らない（Gate の項目 6 は空なら通る）） |
| N-08 | 割り方（1 タスク・層ごと・task1 で全層を最小に通す）は計画が選び、理由を decisions に書く。設計レビューは割り方の種類を指摘しない |
| N-09 | 1 つの PR に収まる条件（受入条件がテストで一意に判定できる・関心事が 1 つ・途中で trunk に入れても壊れない） |
| N-10 | 設計ファイルに書くもの（公開インターフェース・どのタスクが作りどれが使うか・層をまたぐデータの流れ）。テストから呼ぶ形は計画が設計に書き、テスト作成より前に確かめる |
| N-11 | 受入条件は具体値で、テストで確かめられるものだけ。ドキュメントの追記は dod か scope に書く |
| N-12 | 検証コマンド一式は CI 定義・CLAUDE.md・README・Makefile・package.json から拾う。ラン共通の検証コマンドにはどの時点でも通るものだけを入れ、空にしない（一部を新設計で採用（ラン共通の検証コマンドには、どのタスクを積んだ時点でも通るものだけを入れる）: contracts/_proposal.md。git 管理タスクの Verify が積む直前に毎回流し、落ちると `integration-failed` になるため。拾う先の一覧と「空にしない」は採らない） |
| N-13 | ブリーフの検証コマンド・テストのパス・変更禁止パス・注意点を計画の値で埋める規則。設定はラン → リポジトリ共通 → 既定の順に探し、検証コマンドが空の設定は飛ばす |
| N-14 | コードマップは計画ステージが書く（作業ログにしない）（新設計で採用: DM §11.1・ARCH §13「コードマップを作る所」） |
| N-15 | タスクは依存の順に並べ、`tasks[0]` を一番下に積む。番号の小さい未着手のタスクから 1 本ずつ回す（新設計で決め直した: ARCH §7） |
| N-16 | 要確認か失敗のタスクがあれば後続を回さない |
| N-17 | 2 本目からは直前に積んだブランチの上に作る（新設計で決め直した: ARCH §7。始めた時点のスタックの一番上から切る） |
| N-18 | 計画の結果は提案として持ち、設計の must-fix が 0 件になってから 1 回だけ写す。再計画も提案として設計レビューを通す（新設計で採用: DM §6.4） |
| N-19 | 設計の must-fix が 0 件なら、should-fix と nit を設計ファイルの末尾に書き足して rejected にし、タスクの台帳に立て直さない（新設計で採用: DM §6.4・ARCH §13「設計の must-fix 以外」） |
| N-20 | 設計のラウンドは 1 つの提案で 5 回まで。人に聞いたらラウンドの数を 0 に戻す。確定したら 0 に戻す（新設計で採用（上限 5 を `MAX_DESIGN_ROUNDS` として。上限への回答で 0 に戻し、回答を Revise に渡して続ける）: DM §4・§6.4・ARCH §13「歯止めの値」「設計のラウンドの上限に回答があったとき」） |
| N-21 | 設計の指摘が直しを 2 回受けても未解決なら、分類が無くても人に聞く。設計のジャッジの reverted・ambiguous も人に聞く（一部を新設計で採用（reverted・ambiguous はラン統括へ上げる）: DM §8.2 `design-reverted`・`design-ambiguous`） |
| N-22 | reverted にする基準（前の版で指摘を受けて変えた形に戻った・閉じる指摘と前に閉じた指摘が逆の形を求める。新しい事実で説明できれば reverted にしない）（新設計で採用: contracts/design-judge.md。DesignJudge は `DesignCause` の `reverted` を返す役で（DM §8.2 `design-reverted`）、見分ける基準が無いと判定が揺れる。新しい事実で説明できる戻りまで止めると、正しい直しでもラン統括へ上がって待つ） |
| N-23 | 設計の版は消さず、設計のジャッジがランの間同じセッションで見比べる。driver は版を比べない（新設計で採用: DM §6.4・§11.1・ARCH §13「続けるセッション」） |
| N-24 | 直した提案の設計が空なら前の版を残す（新設計では採らない: DM §11.1。Revise は全文を返すので、空は形の誤りとして `StageFailed`） |
| N-25 | 設計を直すのは計画・再計画の続きのセッションで、直すたびに design は全文、tasks も全部返して丸ごと置き換える |
| N-26 | タスクがすべて light なら設計レビューを飛ばし、飛ばすかは最初の提案で決めて引き継ぐ。design-gap から回った再計画は light でも通す（新設計で決め直した: ARCH §13「設計レビューを飛ばすか」） |
| N-27 | 設計レビューに前の版を読ませず、設計のジャッジにだけ渡す（モデルの扱い方の事実は CT-15）（新設計で採用: ARCH §11「レビューは毎ラウンドまっさら」） |
| N-28 | tier（light / standard）。light はレビュー 1 体、standard の 1 ラウンド目は通常と敵対的、2 ラウンド目からは通常 1 体、docs だけの修正（changeKind）なら 1 体。知らない tier は standard に倒す（新設計で決め直した: ARCH §13「レビューの体数」「ラウンドごとのレビューの顔ぶれ」） |
| N-29 | standard のタスクでは、敵対的レビューがタスク全体で 1 度でも走ったかを完了チェックで見る |
| N-30 | レビューのラウンドに上限を置かない。回り続けるのを止めるのは停滞と再計画の回数 |
| N-31 | 停滞は同じ指摘が修正を 2 回受けても未解決のとき。修正のたびに open の指摘の回数を 1 足し、ラウンドごとに新しく立った指摘は数えない（新設計で採用（`STALL_AFTER_FIXES`）: DM §4・§6.3・ARCH §13「歯止めの値」） |
| N-32 | ジャッジの分類ごとの手（tests はテスト作成が直して修正を続ける・approach は実装を新しいセッションでやり直す・scope は再計画・ambiguous は人に聞く）。分類なしの停滞は再計画（新設計で決め直した: DM §8.2。stall の hint を見てタスク統括が決める） |
| N-33 | ジャッジは停滞を待たずに分類してよい（特にテストの抜けは tests） |
| N-34 | 直す手を変えたら停滞の数を 0 に戻す。再計画でタスクを残すとき、ジャッジのセッションと停滞の数を引き継がない |
| N-35 | 実装と修正は同じセッションを続け、ジャッジは同じタスクの間セッションを続ける（再計画をまたぐと新しく）。期待値を決めるステージは毎回新しいセッション（一部を新設計で採用（Fix は Impl の続き・Judge はタスクの間同じ）: DM §11.2・ARCH §13「続けるセッション」） |
| N-36 | ジャッジは open の全件の状態を決め、中間の状態を残さない。rejected は誤った指摘と nit だけ。状態を変えるときはどう確かめたかをコメントに書く。コードを書かず、新しい指摘を立てない（一部を新設計で採用（状態を変えるときはコメントを残す）: DM §6.3。rejected は誤った指摘と nit だけ・コードを書かず新しい指摘を立てない、も採用: contracts/judge.md・design-judge.md。rejected の意味は DM §9.3 が決めている。ジャッジがコードを書くと自分の直しを自分で閉じ（CT-10）、指摘を立てると毎ラウンドまっさらのレビューと別の出どころが増える） |
| N-37 | 同じ箇所・同じ原因の 2 件は片方を重複として閉じ、重い側に合わせる（一部を新設計で採用（重大度の軽い方を重複として閉じ、重い方を残して判定する）: contracts/judge.md。レビューは独立に立てる（CT-14）ので同じ問題が別の言葉で並ぶ。両方を open に残すと、修正が同じ問題を 2 件として直し、停滞も 2 件で数える） |
| N-38 | 指摘はすべてタスクの中で直し、後回しにしない。正しい must-fix と should-fix は却下しない（一部を新設計で採用（正しい must-fix と should-fix は却下しない）: contracts/judge.md・design-judge.md。`rejected` は「指摘の誤り・nit」と DM §9.3 が決めている。正しい指摘を却下できると、Gate の「open の指摘が無い」が自己承認で通る（LEDGER CT-10）） |
| N-39 | コメントできるステージ（レビュー・実装・計画・再計画・ジャッジ）と、設計の指摘を直すステージが指摘ごとに直し方を残すこと（新設計で決め直した: DM §7.2 `CommentFinding`） |
| N-40 | 指摘の移管: 移した元は moved にして解消の数から外し、移した先で open に立て直し、ジャッジが閉じるまで積まない。移す先は同じランのタスク。移管は写すときに 1 回だけ。移した指摘を移した先の受入条件に書き足す。無い指摘の移管は飛ばす（新設計で採用（移した元は `carried`）: DM §6.3・§7.2・ARCH §13「指摘の移管」） |
| N-41 | 完了チェックの 6 項目（ステージが正常に終わった・コミットが 1 件以上・open が 0 件・走るべきレビューが走った・テスト作成の後にテストの差分が無い・検証コマンドが緑）と、その並び・文言・数えられないときは -1・⑥は①〜⑤の後で 2 段で判定・検証コマンド 0 本で落とす・testsAt が無ければ落とす（新設計で決め直した: DM §10 `GateEvaluator`。項目は新しいフローに合わせて決め直す） |
| N-42 | 完了チェックが落ちたら、落ちた項目を must-fix の指摘にして修正のループへ戻す。コードで直せない項目（レビューが走り終えていない）は人に聞く（新設計で採用（`GateFailed`。コードで直せない落ちは `untested-change` で上げる）: DM §11.3・ARCH §13「Gate が落ちたとき」） |
| N-43 | review.json の実在をレビューが走った証拠にする。レビューは指摘 0 件でも `review done` を呼ぶ（新設計で決め直した: DM §7.1。結果を返し、走ったことは `StageCompleted` に残る） |
| N-44 | ラウンドの番号の決まり（review r1 → judge r1 → fix r1 → review r2、テスト作成と実装は r0、同じラウンドの 2 度目は -2）と、ラウンドを文字列で書く規則（新設計で決め直した: DM §4 `ExecutionId`） |
| N-45 | テスト作成は設計にある形だけを呼び、足りなければ書かず commit もせず designGap を返す。内部の型の欄はテスト作成が決めてよい（一部を新設計で採用（足りない形は `design-gap` で上げる）: DM §8.2。内部の型の欄はテスト作成が決めてよい、も採用: contracts/test-gen.md。設計ファイルに内部の型の欄を書かない（CT-18）ので、テストが内部の型を組み立てるリポジトリでは誰かが決める必要がある） |
| N-46 | テスト作成は 1 コミット以上する。例外はテストの矛盾の報告を誤りと判断したとき（新設計で採用: contracts/test-gen.md。TestGen は `tests` を produces に持ち、実物はコミットである。例外は stages.py の `can_keep`（`unchanged`）で表す） |
| N-47 | テスト作成は設計のシグネチャのスタブを置いてよく、テスト以外に触ったファイルを通常レビューに渡して実装が書かれていれば must-fix にする（フックで見分けられない事実は HK-10） |
| N-48 | テストを削らず、skip を付けず、期待値を緩めない（新設計で採用: contracts/test-gen.md・expect.md。テストを書けるステージがテストを緩めると、合否の基準そのものが下がり、Gate の「テストのファイルが変わっていない」は TestGen のコミットの後しか見ない） |
| N-49 | テストが実装の前に全部通ったら 1 回だけ書き直させ、それでも通れば人に聞く。回答の後は確かめ直さない。2 回目からのテスト作成では確かめない。確かめの途中で落ちたら確かめだけやり直す（新設計で決め直した: DM §8.2 `red-check-failed`。書き直させるかはタスク統括が決める） |
| N-50 | 実装の前の確かめは、テスト作成が返す commands ではなく完了チェックと同じ検証コマンドで流す。lint で落ちても Red とみなす |
| N-51 | 検証コマンドの積み上げ（ラン共通＋番号の小さいタスクが足したもの。後ろのタスクと取り下げたタスクの分は流さない）と、ステージに渡す文面での見せ方（新設計で決め直した: DM §10 `VerifySelector`・ARCH §13「流す検証コマンド」） |
| N-52 | 再計画が空の一覧を返してもラン共通の検証コマンドは置き換えない。タスクの検証コマンドは空で消せる |
| N-53 | 期待値テストの 2 種類（受入条件から言える値は実装の前、言えない値は実装の後に出力を照らして決める）（新設計で採用: DM §11.2 Expect・ARCH §11） |
| N-54 | 期待値を決めるステージを呼ぶのは、各ラウンドの頭で expectedCommands を流して落ちていたときだけ。レビューより前に決める（新設計で採用: DM §11.2・ARCH §13「期待値を決めるステージを走らせる時」） |
| N-55 | 期待値を書いたコミットを testsAt にする |
| N-56 | 期待値にできない出力（expectedDefects）は expect の must-fix 1 件に積み続け、毎ラウンド立て直さない |
| N-57 | 期待値を決めるステージはテスト作成と同じ指示書で、必須ルールは分ける（1 コミット以上を課さない）。出力を写すだけの承認にしない（一部を新設計で採用（出力を写すだけの承認にしない・期待値を書かなければ commit しない）: contracts/expect.md。Expect は TestGen と別のステージで指示書も分けた。出力をそのまま期待値にすると、実装の誤りがテストの基準になる） |
| N-58 | 期待値テストは期待値が決まるまで落ちたままでよく、実装と修正にそう知らせて書かせない（新設計で採用: contracts/impl.md・fix.md。期待値は Expect が実装の後に受入条件と照らして書く（DM §11.2）。実装と修正が期待値を通そうとすると、期待値を更新するコマンドで出力をそのまま写すことになる） |
| N-59 | テストを直した回が expectedTests / expectedCommands を省いたら前の一覧のまま |
| N-60 | テストの矛盾の報告はテスト作成が受入条件と照らして確かめ、誤りなら commit せず理由を実装に渡して実装を 1 回だけ呼び直す（一部を新設計で採用（`test-conflict` で上げる）: DM §8.2。TestGen が報告を受入条件と照らして確かめ、誤りならテストを変えず何も commit せずに `unchanged` を返す）: contracts/test-gen.md・stages.py の `can_keep`。テストを書けるのは TestGen だけなので、報告の正否を確かめられるのも TestGen だけである。誤りのときに変えずに終える道が無いと、`tests` の実物が無いので失敗に数えられる。実装を 1 回だけ呼び直す規則は採らない（呼び直すかはタスク統括がフローで決める）） |
| N-61 | 実装・修正は設計の形を黙って変えず interfaceChange で報告し、driver は報告の時点で再計画に回す。報告せずに変えた実装は通常レビューが must-fix にする（一部を新設計で採用（実装も `design-gap` で上げる）: DM §8.2） |
| N-62 | 設計が変わる再計画の後は、そのタスクをテスト作成からやり直し、何がなぜ変わったか（resumeNote）を渡す。範囲だけの再計画ではやり直さない |
| N-63 | テスト作成・実装も、受入条件が曖昧なら blocked と questions を返す |
| N-64 | 実装は範囲を広げず、ついでの整理をしない。修正は指摘の範囲を超えない。修正は直せないなら範囲の外のどこを変える必要があるかを書く（新設計で採用: contracts/impl.md・fix.md。実装タスクは並列に走り、積むときに 1 本ずつ rebase するので、範囲の外の変更はほかのタスクとの衝突（`integration-failed`）を増やす。範囲の外のどこを変える必要があるかは、ジャッジが `scope` に分類し、タスク統括が `needs-replan` を決める材料になる） |
| N-65 | 変更禁止パス（protected）はどのステージも書き換えず、再計画で範囲を広げても入れない（一部を新設計で採用（変更禁止パスの `GlobPattern`）: DM §4） |
| N-66 | タスクを 1 本も積まないまま再計画を 2 回続けたら人に聞く。積むたびに 0 に戻し、回答でも 0 に戻す。ラン全体の回数では止めない（新設計で採用（`MAX_REPLANS_WITHOUT_STACK`。上限の後はユーザーの回答を添えた再計画だけを受けて 0 に戻す）: DM §4・§6.1・ARCH §13「歯止めの値」） |
| N-67 | タスクを足し続けるランを止める条件は無い |
| N-68 | 再計画はスタック済みのタスクに触らない。tasks には止まったタスクより後ろの未着手を全部書いて丸ごと置き換える。残さないタスクは dropped にする（新設計で決め直した: ARCH §13「再計画で書き換えてよい範囲」） |
| N-69 | 再計画は割り方と設計ファイルを一緒に直し、ゴールは変えない。止まったタスクのコミットはできるだけ活かす |
| N-70 | 報告から呼ばれた再計画は、報告を受入条件と照らし、誤りなら設計を変えず理由を notes に書く。検証コマンドが範囲で直せない理由で落ちていれば直す |
| N-71 | 通常レビューの観点（受入条件をテストが確かめていなければ must-fix・期待値が誤りを写していれば must-fix・DoD のドキュメントの追記が無ければ must-fix）と重大度のアンカー。2 ラウンド目からは直した差分と、その直し方が壊した所を見る（一部を新設計で採用（観点: 受入条件をどのテストも確かめていない・期待値が受入条件と食い違う・DoD のドキュメントの追記が無い。重大度は must-fix・should-fix・nit の意味だけ）: contracts/review.md。Gate は受入条件とテストの対応も期待値の正しさも見ず、ドキュメントは受入条件に入れない（LEDGER CT-21）ので、確かめる所がレビューしか無い。観点ごとに重大度を決める規則と、ラウンドごとの見方は採らない） |
| N-72 | 敵対的レビューに渡さないもの（設計ファイル・実装の報告・コミットメッセージ・PR の説明・通常レビューの指摘・スタブの一覧）。検証コマンドを流し直さない（一部を新設計で採用（設計ファイルを渡さない）: DM §11.2・ARCH §13「敵対的レビューに渡すもの」） |
| N-73 | ジャッジ・修正・再計画・PR 本文は、指摘の台帳と経緯（コメント）を読んで判断する |
| N-74 | 概要ブランチは空のコミットだけを載せた一番下のブランチ。全部積んでから `gh pr ready` で draft を外し、人が `gh stack merge` で下からマージする。マージはしない（新設計で採用: ARCH §1・§11・DM §11.4） |
| N-75 | 概要 PR はタスクが決まってから作り、まとめを呼ぶのは計画の直後と仕上げの 2 回。ほかはマーカーの差し直しだけ（新設計で決め直した: DM §11.4） |
| N-76 | まとめが本文を返していないときの最小のテンプレート。まとめが本文を返さなければ前の本文を使う。PR 本文が空なら件名と DoD だけの最小の本文で PR を作る |
| N-77 | 概要 PR の本文の構成（上の区画は PR テンプレートに従い、下の区画は記録のマーカー）・マーカーの種類（tasks・held・deferrals・decisions）と中身が無いときの文言・末尾の署名（ラン名・更新日時・マージの案内）・先頭の引用・指示の原文を `<details>` に貼る・確認手順の書き方・要対応があるとき held のマーカーを消さない（一部を新設計で採用（上の区画は PR テンプレートに従い、下の区画はマーカーの行をそのまま残す・先頭の引用・指示の原文を `<details>` に置く・末尾の署名。マーカーは tasks・waiting・decisions・deferrals・instruction・signature）: contracts/write-overview.md・templates/overview-pr-body.md。WriteOverview がまとめを書き、RefreshOverview が状態から埋め直す（DM §11.4・LEDGER TX-06）ので、マーカーの行の決まりが要る。PR テンプレートは初めて見るレビュアー向けの区画にしか合わない。中身が無いときの文言と確認手順の書き方は採らない） |
| N-78 | まとめのステージにだけランの base とスタック済みのタスク PR の本文を渡す |
| N-79 | タスク PR の本文の構成（PR テンプレートを読まず決まった構成）・却下した指摘を残す・タスクの一覧を焼き込まない・stacked PR の案内は driver が先頭に差す（新設計で採用（stacked PR の案内は driver が雛形から差す）: contracts/write-pr-body.md・templates/task-pr-body.md。直さないと決めた指摘は、人のレビュアーが差分だけからは知れない。タスク PR の本文は RefreshOverview が埋め直さないので、焼き込んだ一覧は古くなる。案内の文面は TX-08 で templates/ に置く。リポジトリの PR テンプレートは概要 PR の上の区画に使い（N-77）、タスク PR は差分を読むのに要る項目だけの決まった構成にする） |
| N-80 | PR に載る文は、指示もレビュー記録も見ていない人が読む。番号や id で指さず、autodev の中だけで通じる語を使わない（新設計で採用: contracts/write-pr-body.md・write-overview.md・_proposal.md。PR を読む人の手元にあるのは PR の本文・差分・リポジトリだけで、ランディレクトリの記録は見えない。指摘の id やステージの名前は、そこでは引けない） |
| N-81 | ステージが自分で決めたこと（decisions）とスコープ外にしたもの（deferrals）を概要 PR の判断ログに載せる |
| N-82 | ジャッジトークンとテストの解禁を、要るステージにだけ環境変数で渡す（新設計で決め直した: DM §4 `JudgeCapability`・`Guard`） |
| N-83 | 1 ラウンド目の 2 体のレビューが同時に書くので、review.json と state.json の書き込みを flock とロックで直列にする |
| N-84 | 進み具合（phase・redCheck）を state.json に残して呼び直しで続ける。回答の実在を再開の合図にする（driver の再開の話。ask のフックの事実は HK-24） |
| N-85 | タスクを持たないステージの記録は `task0`、設計は `design` に置く（新設計で決め直した: DM §4 `StreamId`・§14） |
| N-86 | 計画ステージが聞くときは `autodev ask` を呼び、終了コード 3 を受けたら blocked を返す（defer の事実は HK-20〜HK-25） |
| N-87 | driver は自分が呼び直されるかを決めない。進めなくなったら終了コードと `status` に理由を載せて終わる（新設計で決め直した: ARCH §6・§9） |
| N-88 | `autodev.py` の docstring にある「ラウンドの上限 3」（古い記述） |
| N-89 | 今の形を確かめるだけのテスト（置き場の求め方・launcher の実行権・結果を返すステージの一覧・テンプレートの書き出し・層と import の向きと循環・外を触る呼び出しの検出・state.json と config.json のパスとキー・ランの一覧・プレースホルダ表の書式・review.json のラウンドの型の揺れ・表の列と状態のラベル・完了チェックの辞書と行の出し方・状態ごとのタスクの数） |
| N-90 | ステージごとのターンの上限（計画 150・再計画 150・設計レビュー 120・設計のジャッジ 130・テスト作成 180・期待値 180・実装 280・修正 230・レビュー 120・ジャッジ 130・PR 本文 60・まとめ 60・既定 80）と、ステージ 1 回の制限時間 3600 秒 |
| N-91 | ステージごとのモデルと思考量（読んで決めるのは opus・文章だけは sonnet。計画・再計画・ジャッジ・設計のジャッジ・設計レビューは high、ほかは medium） |
| N-92 | フックの入力（JSON）が読めないときは通す |
| N-93 | 名前の規約（概要ブランチ `stack/<ラン名>--task-0`・タスクのブランチ `stack/<ラン名>--task-<番号>`・概要 PR のタイトル `[autodev] <ラン名>`・タスク PR のタイトル `[autodev #<概要 PR> <タスク>] <件名>`） |
| N-94 | git・gh の制限時間（`gh` 600 秒・`gh stack link` 900 秒・`git push` と `git fetch` 900 秒・ほかの git 600 秒） |
| N-95 | 検証コマンドは 1 本落ちたらそこで止め、1 本の制限時間は 3600 秒。落ちたらコマンド・終了コードと標準エラーの末尾 12 行を理由に載せる |
| N-96 | `clean` と `purge` の振る舞い（`clean` は worktree だけを外して記録を残す。`purge` は手元のブランチとランディレクトリも消し、PR とリモートには触らない。走っているステージか push していないコミットがあれば何も消さず、`--force` で押し切る。worktree を消すと無視されたファイルも消える事実は GH-04） |
| N-97 | statusline が、走り終えたステージをタスクごとに成否つきで読んで「済・今・これから」を描く |
| N-98 | `status` で、回答待ちをタスクの状態より先に見て出す（計画が聞いて止まったとき「計画中」と出すと、`/autodev` が計画をもう一度起動する） |
| N-99 | テストのパスの既定の glob（`**/test_*.py` `**/*_test.py` `**/tests/**` `**/*_test.go` `**/*.test.ts(x)` `**/*.spec.ts(x)` `**/*Test.java` `**/*_spec.rb` `spec/**`。glob の照合の事実は HK-15） |
