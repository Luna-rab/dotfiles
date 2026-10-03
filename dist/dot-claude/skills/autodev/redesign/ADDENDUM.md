# 設計書への補足（実装に入る前に決めたこと）

[DOMAIN_MODEL.md](DOMAIN_MODEL.md) と [ARCHITECTURE.md](ARCHITECTURE.md) の再レビュー（`f2e9771..38ff98a`）で見つかった穴への答え。**この文書と設計書の本文が食い違うときは、この文書が正しい。** 本文への反映は、作り直しの最後の文書の段で行う。

## 1. タスクの種類による違い

- FlowValidator の「終わりに `gated` と `pr-body` が作られるか」と、フローの終わりに出る `TaskGated` は、**実装タスクだけ**に掛ける
- 計画タスクと git 管理タスクは、ランが終わるまで続く。終端の状態 `finished` に移るのは `RunFinished` の後で、git 管理タスクは仕上げの並び（WriteOverview → RefreshOverview → ReadyOverview）を終えてから移る
- `FinishRun` の「終端でないタスクがある」は、実装タスクだけを見る
- git 管理タスクは、Stack の仕事の列（§12）から取り出した仕事を処理する。最初の仕事（概要ブランチを切る）は、`RunStarted` を受けたポリシーが列に入れる（概要ブランチの名前とランの base が `RunStarted` にある）。git 管理タスクの `TaskStarted` の後に配られるので、取り出す時には git 管理タスクは開いている
- `StartTask` の「計画タスクか git 管理タスクがすでに始まっている」は、同じ種類のタスクを 2 つ目に始めるときだけ拒む
- `EscalateToRun` を出せるのは、実装タスクの統括・計画タスクの統括（プログラム）・git 管理タスクの統括（プログラム）
- `ChangeScope` は実装タスクにだけ送る

## 2. Run が持つタスクの状態

- Run は、各タスクの状態（`TaskStatus`）の遷移を、1 つのコマンド `UpdateTaskStatus` → 1 つのイベント `TaskStatusChanged{task, from, to, cause}` で知る。出すのはポリシーで、Task・Stack のイベント（`EscalationRaised`・`EscalationResolved`・`EscalationClosed`・`TaskGated`・`GitJobTaken`（積む仕事）・`ScopeChanged` など）を受けて出す
- `MarkStacked` は、`TaskStatus` が `stacking` のときだけ受ける（`TaskStatusChanged` で `stacking` に入っている）
- §9.1 に足す遷移: `gated → dropped`・`stacking → dropped`（積む列に入っていれば列から外す）・`gated → pending`（再計画で範囲が変わった。積む列から外し、`TaskScheduler` が並列の上限と依存を見て、新しいブランチで始め直す。始め直す `TaskStarted` は `reopened` で、ポリシーは `OpenTask` ではなく `ChangeScope` を出す）・`escalated → dropped`（止めたタスクのエスカレーションを閉じたとき）。`gated → running` は受けない
- 最後の実装タスクが、どの道（積んだ・止めた・引き継がれた・破棄した）で終端になっても、Run は `AllTasksSettled` を出す（`AllTasksStacked` から名前を変える）。ラン統括はこれで仕上げに起こされる。実装タスクが 0 件の計画を反映したときも出す。計画が進んでいる間と、破棄した後に閉じ終えて積む列へ戻すまで（`TasksReturnedToQueue`）は出さず、`FinishRun` も拒む（積む列へ戻すのを待つタスクは `stacked` のままだが、終端とみなさない）
- `ApplyPlan` は、終端でないタスクが `dropped`・`discarded` のタスクに依存しているグラフを拒む（`superseded` は引き継ぎ先に付け替える）

## 3. 再計画

- ラン統括の判断に `apply-plan`（止める実装タスクの一覧・破棄する積んだタスクの一覧）を足す。再計画の設計が確定したら（`DesignSettled`）、ラン統括を起こしてこれを返させる。アプリケーション層はこれを 1 つのコマンド `ApplyReplan` に置き換え、Run がまとめて確かめて `TasksStopped`・`TasksDiscarded`・`TasksPlanned` を出す（空の一覧のイベントは出さない）。単独の `DiscardTasks` は無い。初回の反映は、`SettledPlanRecorded`（`replan` が偽）を受けたポリシーが出す `ApplyPlan`
- Run は、確定した提案（止める・破棄する候補）を `DesignSettled` を受けたポリシーから `RecordSettledPlan` → `SettledPlanRecorded` で受け取り、`ApplyReplan` の検査に使う
- 再計画が進んでいる間（計画タスクのフローが終わっていない間）の `RequestReplan` は拒む。ただし、計画タスクのエスカレーション（`ask` など）に応じるものと、確定して反映を待っている提案を退けるものは受ける。退けた提案の再計画のきっかけも、次の反映のときに閉じる（`ReplanRequested.closes_on_apply` に足す）。`TasksPlanned` の後、まだ開いている Run のエスカレーション（`still_open`。同じコマンドで閉じたものは数えない）で、ラン統括を起こし直す
- `RequestReplan` を受けた Run は、計画タスクの未処理のエスカレーションを閉じる `EscalationClosed` を `ReplanRequested` より前に出し、それを受けたポリシーが計画タスクの側を `CloseEscalation` で閉じる（`ReplanRequested` を受けた計画タスクの統括が組むフローを、計画タスクが受けられるように）。確定していない設計の提案は `DiscardProposal` → `DesignProposalAbandoned` で捨てる
- まだ一度も設計が確定していないときの再計画は、`Prepare → Plan → DesignLoop` からやり直す（Replan ではなく Plan）
- ラン統括は、ランの開始時には起こさない（返す判断が無い）。起こすのは、上がってきたイベント・回答の到着・再計画の設計の確定・`AllTasksSettled` のとき

## 4. 合成ステージは、イベントで進める

- 合成ステージ（WriteTests は作らない。ReviewLoop・DesignLoop）の中の進行は、**実行器のスレッドの中のループではなく、Task 集約の状態とポリシーで進める。** 状態を読み書きするのはメインループだけ、という原則に合わせるためである。DOMAIN_MODEL §11.5 の `CompositeStage.execute` の擬似コードは捨てる
- Task は合成ステージの中の位置（どの中のステージか・何ラウンド目か）を `Cursor` に持つ。中のステージが終わったら、Task.handle が次の中のステージ（または合成ステージの終わり）を決めて `StageRequested` を出し、それを受けた実行器が HEAD とセッション id を集めて `BeginStage` を出す。判定（Judge・DesignJudge）の後は、指摘の台帳・Design の結果が `ConcludeReviewRound`・`ConcludeDesignRound` で届くまで止まる
- 停滞のエスカレーションは、ジャッジの判定の後に 1 回だけ出す（§5）
- `design-ambiguous`・`design-reverted` に回答が来たら、回答を入力に足して Revise から続ける

## 5. 停滞の数え方

- `CountFix` は回数を数えるだけ（`FixCounted`）。`FindingStalled` は、判定を締めたとき（Judge の判定の `RecordJudgement`・Gate の結果の `RecordGateResult`）に、`fixesReceived ≥ STALL_AFTER_FIXES` のまま `open` に残った指摘について、ReviewLedger が出す。`FindingStalled` はただの記録で、停滞のエスカレーションは、同じコマンドの最後に出る `FindingsEvaluated.stalled` を受けた Task が 1 回だけ上げる。上げた指摘は、そこからまた `STALL_AFTER_FIXES` 回直すまで停滞にしない

## 6. Gate

- Gate の項目は、新しい設計の考え方から次のものにする（古い 6 項目は写さない）
  1. そのタスクのコミットが親ブランチから 1 件以上ある
  2. 指摘の台帳に `open` の指摘が無い（`carried`・`rejected`・`closed` は数えない）
  3. フローの最後の ReviewLoop で、その回の `reviewers` に挙げたレビューがすべて走り終えている
  4. フローに TestGen があれば、TestGen のコミット（Expect が期待値を書いたら、そのコミット）の後でテストのファイルが変わっていない
  5. フローに TestGen が無ければ、変わったファイルがすべて「テストが要らないパス」に収まっている（外れたら `untested-change` で上げる）
  6. そのタスクの `verify` がすべて通る（空なら通る）
- 2・6 の落ちは、項目ごとに決まった id の指摘（例: `G-verify`）を開く（すでにあれば開き直す）。開き閉じするのは Gate の結果（`RecordGateResult`。通った項目の指摘は閉じる）で、G- の指摘を判定するのは Gate 自身である（Judge は判定せず、項目 2 にも数えない。数えると Gate と ReviewLoop の間で回り続ける）。修正の回数は同じ指摘に積み上がるので、停滞の判定も効く。1・3・4 の落ちはコードで直せないので `gate-unfixable` で上げる
- `GateFailed` を当てると cursor は直前の ReviewLoop の Fix を指すが、Fix を起動するのは、`RecordGateResult` の `FindingsEvaluated` を受けたポリシーの `ConcludeGateRound` の後である

## 7. ラン共通の成果物

- `brief`・`codemap`・`design`（版の番号）はラン共通の成果物で、計画タスクが作る。`TaskStarted` と `ScopeChanged` は、そのときの版を載せ、タスクはそれを自分の成果物として持つ（実行器の ① はそれで通る）

## 8. 回答とエスカレーション

- ラン統括の `answer` は Run が受ける（`AnswerEscalation` → `EscalationAnswered`）。ユーザーの回答の本文は、ラン統括の判断からではなく、Run が受け取った回答（`QuestionAnswered` → `RecordAnswer` → `AnswerRecorded`）から写す。`EscalationAnswered` を受けたポリシーが、上げてきたタスクへ `ResolveEscalation` を出す。`QuestionId` はその質問を出したエスカレーションに結びつけ、1 回使ったら使えない。ラン統括が自分で答える（`answer`）ときは `origin: run-supervisor` で、QuestionId は付かない
- `EscalationResolved` は、答えたエスカレーションの種類と起きた実行を持つ。defer で止まった計画ステージの ask なら、続きから再開する実行と `tool_use_id` も Task が決めて載せる（反応が `answers/<tool_use_id>.json` を書いてから `ResumeStage`）。`design-*` への回答なら、ポリシーが `ResumeDesign` を出す
- ランが終わった（`RunFinished`）後も、git 管理タスクのエスカレーションとその回答は受ける（仕上げの並びは `RunFinished` の後に走る）
- 再計画の上限を外す `RequestReplan` の QuestionId も、使い回せない
- `ScopeChanged` への応答としてのフローは、回答を待っている間でも受ける（`ScopeChanged` はそのタスクの待っているエスカレーションを閉じる）
- Run の側のエスカレーションも、ラン統括の判断（answer・insert-task・stop-tasks・apply-plan）を処理したときに `EscalationClosed` で閉じる

## 9. 再起動

- driver が起動したら、`running` のまま残っている実行（`Task.running_executions`）を、アプリケーション層が `MarkInterrupted` → `StageInterrupted` で `interrupted` にする。`StageInterrupted` は止めた理由（`InterruptCause`: 起動時の後始末 `startup`・パニック `panic`・タスクを止めた `stopped`・フローを捨てた／ポリシーが止めた `requested`）を持つ
- `ResumeStage` は、終端のタスクの実行には拒む
- 既にあるラン名で呼び直したら（パニックの後も、driver が落ちた後も）、Run は `RunResumed`（終端でないタスクの一覧）を出す。受けたポリシーが各タスクに `ResumeInterrupted` を送り、止めた理由が `startup`・`panic` で今のフローの実行を、Task が続きから再開する

## 10. git 管理タスク

- CutBranch の 4 つ目の場合: 破棄の後に積み直すタスクを、新しいブランチ名（`stack/<ラン名>--task-<番号>-r<切り直した回数>`）で、残した一番上から切り直す。積み直すときの並びは `CutBranch → Rebase → …`（前に積んだブランチ `GitJob.previous` を持つ積む仕事）
- 計画タスクのステージは、再計画のときも含めてすべて `trees/overview` を cwd にする（`claude --resume` は cwd ごとにセッションを探すため、ランの間続く DesignJudge のセッションを見失わない）。Replan と Revise には、`trees/stack-top` の絶対パスを読む場所として渡す

## 11. その他

- **ポリシーはドメイン層に置く**（DOMAIN_MODEL §13 の図はポリシーをアプリケーション層に置いているが、それは誤り）。ポリシーは「イベント → コマンドの一覧」を返す純粋な関数で、ビジネスロジックそのものだからである。アプリケーション層は、ポリシーを受け手として登録して配るだけ。詳しくは [IMPLEMENTING.md](IMPLEMENTING.md) の §1

- `ReviseDesign` は Revise を起動する前に出す。ラウンドの上限に達していれば `DesignRoundsExhausted`、達していなければ `DesignRevisionStarted` を出し、Revise の結果は `ProposeDesign` で入れる
- `reviewers` に同じステージ名を 2 つ書いたら、FlowValidator が拒む
- LEDGER の N-93・N-47・N-92・N-99 は、新しい設計で使う規約として採用する（`BranchName` の規約・TestGen はスタブを置いてよい・フックの入力が読めないときは止める・テストのパスの既定の glob）
- `TaskStatus` の終端の列挙: `stacked`・`dropped`・`superseded`・`discarded`、計画タスクと git 管理タスクは `finished`

## 12. 実装で変えたこと

作り直しの集約の段（3a〜3c）・アダプタの段（4b）・ポリシーの段（5a）で、設計書と違う形にしたもの。本文（DOMAIN_MODEL・ARCHITECTURE）と食い違えば、ここが正しい。集約どうしのつなぎ目（どのイベントから、どのコマンドを、どの欄で組むか）は `test/autodev/test_seams.py` の `SEAMS` が正本で、次の段のポリシーはそれを書き写す。

### ステージの結果と受け渡し

- **ステージの結果は、値にしてイベントに載せる。** 結果の JSON でドメインが読む欄は `StageSpec.result`（`ResultField`。LLM のステージは `schemas/` と照らす）に宣言し、`domain/results.py` が `StageResult` に読み替えて `StageCompleted.result` に載せる。読めなければ形の誤りで `StageFailed`。提案の版は JSON に無く、実行器が `proposal` の成果物の在りかに書く。Expect の `defects` は Expect を出どころとする must-fix の指摘にする
- **結果をほかの集約へ渡すステージは、受け取る側の答えが届くまで cursor を進めない（一般の規則）。** どこへどう渡すかは `StageSpec.hands_to`（`Handoff`: 提案・指摘・判定・概要 PR・積んだ 1 本・閉じた所）に宣言する。受け取る側（Design・ReviewLedger・Stack）は、中身の問題（使った版・確定していない提案がある・無い指摘を判定した など）を拒否にせず `ResultRefused` で返し、受けたら `ResultReceived` を返す。ポリシーがそれを `ConfirmHandoff` で Task に知らせ、受けられなければ Task が `result-refused` で上げ、解けたら同じステージを走らせ直す。判定の受け渡しは、判定を締めた後の `Conclude*Round` が受けた知らせを兼ねる。拒否（`Rejected`）は、出した者の取り違えだけになる
- 結果を待つ Task がいない中身の問題（コメントが無い指摘を指す・再計画の移管が移せない）は、`CommentRefused`・`CarryRefused` で受けなかったことを残す
- 見る役と Expect の指摘はまとめて `RecordFindings`、判定と締めは `RecordJudgement`（`EvaluateStall` は無い）で受ける。`FindingsEvaluated` は、Judge の停滞の分類（`ConcludeReviewRound.cause`）・DesignJudge が見た版（`SettleDesign.design`）と設計の分類（`MarkReverted`・`MarkAmbiguous`）を写す
- 提案と一緒に、計画タスクのラン共通の成果物（brief・codemap）を Design へ渡し、`DesignSettled.artifacts`（確定した design の版を足す）から `RecordSettledPlan.artifacts` を組む
- 確かめた結果「変えない」と返して実物なしに終える道を `StageSpec.can_keep` と結果の `unchanged` で持つ（TestGen・Impl。前に作った成果物を使う）。統括からステージへの言葉は `FlowStep.instruction` に書き、実行器がプロンプトに添える（ドメインは中身を読まない）
- Expect が期待値を書き残したテストは、結果の `awaitingExpectations` で返し、期待値待ちの成果物は残る
- 差し込んで引き継いだタスクは、引き継ぎ元の統合で衝突したファイルを、成果物 `conflicts` と `TaskOpened.conflicts` で受け取る（`IntegrationFailed` → `RecordIntegrationFailure` → `InsertTask` の `takes_over` → `TaskStarted`）。ResolveConflict が書いてよいファイルになる
- `EscalateToRun` は理由（`reason`）を持ち、Run の `EscalationRaised.reason` に写す

### git 管理タスクの仕事の列

- **仕事の列は Stack に置く。** 種類（`GitJobKind`: 概要ブランチを切る・タスクのブランチを切る・stack-top を切り直す・概要 PR を作る／書き直す・積む・破棄する・仕上げ）と相手（タスク・ブランチ・base・前に積んだブランチ・閉じる所）は `GitJob` が持つ。取り出してよいかと、取り出したときの base（スタックの一番上）・閉じる所が、スタックの形で決まるためである
- 仕事は `EnqueueStack`・`EnqueueGitJob` で列に入り（`GitJobQueued`）、`TakeNextGitJob` で 1 つずつ取り出す（`GitJobTaken`）。git 管理タスクの統括（プログラム）は、取り出した仕事から `flow.git_job_flow` の並びを組むだけで、`Flow.job` に仕事を持たせる。仕事は git 管理タスクのフローが終わる・捨てられる（`FlowAbandoned`）まで処理中のままで、`FinishGitJob` で終える（`StackRequested`・`StackRequestTaken`・`TakeNextRequest` は無い）
- 概要 PR が無いうちは、概要 PR と一番上を相手にする仕事を取り出さない。**破棄した後は、閉じ終えるまで（`UnstackFrom`）一番上に載せる仕事（切る・積む）を取り出さず、破棄の仕事を先に取り出す**
- 止めたタスクの仕事は列から外す（`GitJobWithdrawn`）。処理中の仕事なら、ポリシーが `AbandonFlow` で git 管理タスクのフローを捨てる
- `RecordConflict`・`RejectRequest`・`AppendEntry`・`RecordOverview`・`UnstackFrom` は、`StageCompleted`・`StageReported` に載せた仕事（`job`）と結果から組む。統合の失敗（`integration-failed`）の後、git 管理タスクはラン統括がエスカレーションを閉じるまで次の仕事へ進まない（列は待たされる）

### Run

- **積む列へ戻す一覧の出どころは Run だけ**（`TasksDiscarded.requeue`）。閉じ終えた知らせ（`StackCutBack`）は一覧を持たず、それを受けたポリシーの `ReturnToQueue` で、Run が自分の一覧を新しいブランチで戻す（`TasksReturnedToQueue`）。前の破棄で戻すのを待つタスクを次の再計画で破棄したら一覧から除き、閉じる前に積み終えたタスクは一覧に足す。戻すのを待つタスクは、`TaskScheduler` に積んだものとして見せない
- 始められる実装タスクは、ポリシーの `StartReadyTasks` を受けた Run が `TaskScheduler` に聞いて決める
- 計画が進んでいる間に差し込んだタスクの番号を提案が書いていたら拒む。`ReplanRequested` で、その覚えを消す（次の提案は差し込んだタスクを見て書く）
- ほかの集約のイベントによる状態の遷移は `UpdateTaskStatus` で受け、許す遷移は `run.REPORTED_TRANSITIONS` の表にある。終端のタスクへの遅れた知らせは何もしない
- 計画にあって始めていないタスク・もう止めたタスクへの `StopTask` は、Task が何もしない

### Design・ReviewLedger

- Design の回答待ちは、ラウンドを使い切った・前の版に戻った・受入条件が曖昧（`MarkAmbiguous` → `DesignAmbiguous`）の 3 つを 1 つの状態にし、回答を持った `ResumeDesign`（`ResetDesignRounds` を一般化したもの）でだけ抜ける。ラウンドは 1 から数え直す
- 指摘にはそれぞれ判定する者がいる（タスクの台帳の R は Judge、G- は Gate、設計の台帳の D は DesignJudge）。設計の台帳は、今の提案（`TrackProposal` で始めた版から後）に付いた指摘だけを数える

### ポリシーとつなぎ目（5a）

- **ポリシーは `domain/policies.py` の `POLICIES` で、つなぎ目の表（`test_seams.py` の `SEAMS`）を行ごとに書き写したもの。** 行の名前が受け手の名前・チェックポイントの鍵・コマンドの id（`CommandId.derived`）に入る。アプリケーション層は `RECEIVERS` をこの順に登録するだけにする。計画タスクと git 管理タスクの統括（プログラム）の行も同じ表に置き、タスクの統括として出す（`AcceptFlow`・`EscalateToRun` はタスクの統括にしか出せない）。反応の続き（`resume-ask`）は `FOLLOW_UPS` に分け、反応が副作用を済ませてから呼ぶ
- **再計画が進んでいる間（`ReplanRequested` から `TasksPlanned` まで）は、積む仕事（積む・積み直す）を取り出さない**（Stack の `PauseStacking`・`ResumeStacking`）。再計画は stack-top を読んで書くので、その間に積むと読んだ形とずれ、積む列で待つタスクの範囲が変わっても積まれてしまう。取り出しは `StackingResumed` で頼み、範囲が変わった仕事を列から外す（`TasksPlanned.withdraw`）のを先にする
- **取り下げでも統合の失敗でもなく、結果もまだ受けていない、済ませないとランが終わらない仕事（`GitJobKind.must_finish`。概要 PR の書き直し以外）のフローが捨てられたら、仕事を列の先頭へ戻す**（Stack）。`FinishGitJob.ending` はフローの終わり方（終えた・捨てた）だけを持ち、取り下げ・統合の失敗・結果を受け済みか・戻す回数は Stack が自分の記録で決めて `GitJobFinished.outcome` に残す。ポリシーはイベントの欄しか知らないためである
- 戻すのは `MAX_JOB_RETURNS` 回まで。超えたら仕事を止め（`STUCK`）、後ろの仕事も取り出さずに、git 管理タスクから上げ元の実行の無い `stage-errors` で上げる。ラン統括が答えたら続け（`RetryGitJob`）、答え以外の判断（差し込む・止める など）で閉じたらやめる（`DropGitJob`）。やめてよいのは、やめても相手のタスクを止めるか再計画をやり直せば済む仕事（タスクを切る・積む・stack-top を切り直す。`GitJobKind.can_drop`）だけで、概要ブランチ・概要 PR を作る・破棄・仕上げをやめる `DropGitJob` は Stack が拒む（やめるとランが終わらない）。拒んだ仕事は止めたまま残り、ラン統括が答えるまで後ろの仕事も取り出さない
- **やめられない仕事の上げは、答え以外では閉じられない**（`EscalationRaised.answer_only`。`Escalate` → タスクの側 → `EscalateToRun` → Run の側へ写す）。ラン統括が答え以外の判断（insert-task・stop-tasks・apply-plan の `respondsTo`、replan の `trigger`）で閉じようとすると、Run が拒む（`Run._closable`）。統括自身の判断への拒みなので、理由を添えて同じセッションに差し戻され、答え直せる。閉じさせてから Stack が `DropGitJob` を拒むと、拒んだ理由がどの統括にも届かず、仕事が止まったままになる。上げの本文も `can_drop` で書き分ける
- 止めた仕事をやめる（`drop-stuck-job`）・続ける（`retry-stuck-job`）のは、git 管理タスクの、上げ元の実行が無い `stage-errors` を閉じた・答えたときだけ。見分けるために、Task の `EscalationClosed` に閉じた上げの種類と起きた実行（`kind`・`origin`）を載せる
- 積む仕事のフローを捨てて仕事を残したら、相手のタスクを stacking から gated に戻す（`REPORTED_TRANSITIONS` に足した）。再計画の間に戻った積む仕事の相手も、範囲が変われば積む列から外せる
- 統合の失敗の印（Stack の処理中の仕事・Run の `TaskEntry.integration_failed`）は、その失敗のエスカレーションが開いている間だけ立てる。回答が届いて同じ仕事で統合をやり直すなら下ろす（`RetryIntegration` → `IntegrationRetried` → `ClearIntegrationFailure`）。引き継げる（`takes_over`）のは印が立っている間だけになる
- **フローを捨てたら、どの道（`AbandonFlow`・回答以外の `CloseEscalation`）でも、走っている実行を止め、走らせると決めてまだ始めていない実行をやめ（`StageCancelled`）、そのフローで上げたエスカレーションを一緒に閉じる**（Task）。タスクの側の `EscalationClosed` を受けたポリシーが `CloseRelayedEscalation` で Run の側の中継を閉じる。範囲が変わったとき（`ChangeScope`）も、待っているエスカレーションを `EscalationClosed` で閉じる
- **生きているフローの実行か（今のフローの実行で、そのフローを捨てていない）は `Task._is_live` の 1 つの規則で、** 始める（`BeginStage`）・再開する（`ResumeStage`・`ResumeInterrupted`）・完了の報告（続けられなかった証拠での作り直しも）・受け渡しの答え（`ConfirmHandoff`）のすべてで使う。生きていない実行は進めない
- 始められるタスクが増えうるイベントで `StartReadyTasks` を頼む: 計画を反映した・依存先を積んだ・差し込んだ・止めた（`TasksStopped`）・呼び直された・並列の枠が空いた。最後のものは `TaskGated` ではなく、Run が状態を動かした `TaskStatusChanged`（`TaskScheduler.frees_slot`）で頼む。`TaskGated` を受ける行どうしの処理の順によらず、gated を当てた後に始めるためである
- 回答を待っているタスクから上げ元（`source`）を書かずに `EscalateToRun` を出したら、Run が拒む。上げ元を書かない上げへの回答は notes に残るだけで、待っているエスカレーションへ下りないためである
- 停滞と設計の回答待ちの上げは、判定した実行（Judge・Gate・DesignJudge）の調べる先（結果に添えた `pointers`。Gate は `GateFailed.pointers`）と、Judge の `stallReason`・DesignJudge の `designCause.reason`・`question` を載せる。Task が判定の実行の結果（`Execution.result`）から読む。問いは `EscalationRaised.question` で、計画タスクの統括が `EscalateToRun.question` に写す
- 組で書く結果の欄（`report` と `reportReason`、`stallCause` と `stallReason`、`designCause.kind` が ambiguous なら `question`、reverted なら `question` 無し）の片方だけは形の誤り。`can_keep` のステージが `unchanged` なのに実物を作ったのも形の誤り
- パニックの間と終えた後の `StartReadyTasks` は何もしない（拒まない）。呼び直されたら（`RunResumed`）ポリシーが頼み直す。始めていないタスクへの `ResumeInterrupted` も何もしない
- 引き継げる（`InsertTask.takes_over`）のは、統合の失敗を記録したタスクだけ。初めての計画を反映するまでは差し込まない
- 閉じる前に積み終えたタスクを積み直すかは、`MarkStacked` を受けたときに決めて `TaskMarkedStacked.requeue` に載せる
- 台帳は、もうその状態にある指摘への判定を当て済みとして読み飛ばす。受け取る側（Design）が受けずに走らせ直した判定が、同じ判定を返して受けられずに回り続けないためである。判定する者とコメントは確かめてから、同じ判定の前の件を当てた後の状態と比べる（並び順によらない）

### ガードと検証（4b）

- 書き込みと操作を止めるか通すかの判断は `domain/guard.py`（`Guard.judge_write`・`judge_operation`・`judge_ask`）に置き、フックとアダプタは翻訳だけをする。対象リポジトリの手元の checkout への書き込みも止める。LLM のステージと統括は、GitHub の資格情報を外して起動する（`Guard.withholds_github`）
- `UnionChecker` はバイト列（無いファイルは None）を受ける。片方の書き換え・削除を戻した解き方を落とす。UTF-8 として読めない・バイナリのファイルの衝突は「意味が変わる統合」とする

### 実行器（5b2）

- 実行器は、StageRequested を受けた `begin` で HEAD とセッション id を集めて `BeginStage` を、StageStarted を受けた `run` で `ReportStageResult` を返す（再開の StageStarted も同じ）。利用枠の上限なら `Panic` を返す。プロンプトを組む・走らせる途中で実行器が落ちても、エラーの証拠を返す（札が返らないとメインループが待ち続ける）
- **`--resume` で起こしたか・claude が init を出したか・result を返さずに自分で終わったかを、証拠（`Evidence.resumed`・`initialized`・`ended_without_result`）で渡し、続けられなかったか（`Task._session_lost`）と作り直す（`ExecutionRestarted`）かは Task が `ReportStageResult` を受けて決める。** 続きから始めた実行も、前の実行のセッションを続ける初めての実行も同じ規則で作り直す。続けられなかった印は、`--resume` で起こした claude が **`system/init` を出さずに** result も返さずに終わったこと（`AgentOutcome.initialized` が偽）だけにする。init を出した後に落ちたのは、続けた後で落ちたのであって、作り直すとその実行の仕事を捨てるので、普通の失敗にする。標準エラーの文言（`No conversation found`）は版で変わりうるので印にしない。init を出さないことは実測していない（ARCHITECTURE §14。段 6 で確かめる）。フックに止められ続けて打ち切った（`cut_off_by_denials`）なら、続けられなかったのではないので失敗にする。作り直しを頼むコマンド（`RestartExecution`）は無い（出す者がおらず、走っている実行を `ReportStageResult` の確かめを通さずに作り直す道になっていたので消した）
- **続けるセッションは `Task.session_to_continue` が `StageSpec.session` の宣言から決める。** `--resume` で続けられなかったセッションと、続けて 2 回落ちたセッションは捨てる（LEDGER AR-22）
- **起こし方（初めて・defer から・interrupt から、`--resume` を付けるか）は `Task.how_to_start` が決める。** 元にするのは `Execution.resumed_from`（最後の StageStarted がどの状態から続けたか）と、実行器が渡す「この実行の claude をもう起こした跡（`logs/<ExecutionId>.jsonl`）があるか」である。初めて始めたはずの実行に跡があるのは、配り直した StageStarted で走らせ直すときで、同じセッション id を新しく立てられないので interrupt からとして続ける。defer からはプロンプトを渡さず、interrupt からは短い続きの指示を渡す
- 作り直した実行の次の試みは、begin で HEAD を取る前に、作り直した試みが始めた時点へ worktree を戻す（`Task.reset_before_start`）。作り直しの反応が戻す前に driver が落ちても、戻していない HEAD から始めない
- 成果物の実物の確かめ方: `tests`・`impl` は始めた時点から HEAD が進んだこと（在りかは HEAD）、`proposal`・`codemap`・`pr-body`・`awaiting-expectations` は結果の本文をランディレクトリに書き出したこと、決定的なステージの成果物（`brief`・`red-tests`・`gated`）は中身が走り終えたこと。合否は Task が `expects` で見る。書き直す前のフローの決定的なステージは、期待する証拠が外れたら成果物を足さない
- 親ブランチからのコミット数（Gate の項目 1）と Rebase が載せ直すコミットは、タスクのブランチの根元（`Task.base_commit`。git 管理タスクは仕事の相手のもの）から HEAD までで数える（`StageContext.base_commit` に写す）。ほかのブランチから辿れるかでは数えない。上のタスクを切ったブランチは下のタスクのコミットを含むので、それを除くと 0 件になり、Rebase がブランチを一番上へ黙って戻す。親ブランチの名前も覚えない
- **積み直すタスクを前に積んだブランチから切り直した CutBranch の、切った元は根元にしない**（`CutPoint.roots_branch` が偽。ポリシー `record-cut-base` が `RecordBase` を出さない）。切った元は前のブランチの先端で、そこを根元にすると載せ直すコミットが 0 件になる。根元は前に覚えたまま（切ったとき・最後に載せ直した先）で、Rebase がそこから上を載せ直す
- Gate の項目 4・5 で見るファイルは、GateEvaluator が選ぶ。実行器は、TestGen のコミットの後とタスクのコミットで変わったファイルを、glob の照合の答え（テストのパスか・テストが要らないパスか。`ChangedFile`）を添えてどちらも渡す
- git 管理タスクの Verify（`EvidenceCheck.VERIFY_PASSES`）は、流す検証コマンドが 0 件なら通す。計画はラン共通の `verify` を空にしてよい（LEDGER N-07 は「検証コマンドが空なら止める規則は採らない」とし、`schemas/` の `verify` にも下限が無い）ので、0 件で落とすとどのタスクも積めなくなる
- CheckUnion は、両側を残したと `UnionVerdict.passed` が答えたら rebase を先へ進める（`rebase --continue`）。進めるのはステージの中身として残す。進めるかの答えはドメインが出しており、続けた先でまた衝突したファイルは `UnionChecker.still_conflicted` で解いていないものとして返す。衝突の段が index に無い（解きかけを `git add` した）ファイルは、`UnionChecker.check` が読めないものとして通さない。ステージを分けない理由: 続けるのは確かめた直後の同じ worktree の操作で、間に Task の判断が入る余地が無く、分けると「確かめた」と「続けた」の間で止まった状態を再開の規則に足すことになる
- 結果の `body` をどの PR の本文として書き出すかは `StageSpec.body`（`BodyTarget`: タスク PR・概要 PR）に宣言する。タスク PR の本文だけが成果物 `pr-body` になる。作ったコミットが実物になる成果物は `ArtifactKind.committed`
- 概要 PR の本文は、WriteOverview の結果をマーカー入りのまま `overview.md` に置き、CreateOverviewPR・RefreshOverview が毎回そこから埋める。空のコミットは CreateOverviewPR が、base との差分が 0 のときだけ載せる（CutBranch は載せない）
- フックに拒まれた数が `HOOK_DENIALS_BEFORE_CUTOFF`（10）を超えたら、実行器が interrupt で打ち切り、エラーの証拠にする（LEDGER AR-27）
- interrupt は、claude だけでなく、実行のスレッドが流している子プロセス（git・検証コマンド。`adapters/_proc.StopScope`）もプロセスグループごと止める（SIGTERM、10 秒で SIGKILL。SIGKILL は子がもう終わっていてもグループへ送る。SIGTERM を無視する孫が出力の管を握って残るため）。止めた後のコマンドは流さない。新しいフローのステージを、止めたステージと同じ worktree で同時に走らせない
  - 同じ worktree の次の仕事（begin・restart・abort_rebase、止めた後に再開した同じ実行の run）は、止めた実行が終わるまで待つ。順番待ちと待つのは worktree ごとで、ほかの worktree のタスクは待たせない
  - begin は 1 回に 120 秒（`STOP_WAIT_SECONDS`）待ち、待ち切れなければ始めずに順番待ちの後ろへ回して待ち直す。`STOP_WAIT_ROUNDS`（3）回待ち切れなければ、始められなかったことを `ReportBeginFailure` で Task に渡す。restart・abort_rebase は待ち切れなければ何もしない（次の begin が戻し直し、Rebase・CutBranch が流す前に取りやめる）
  - 止めた実行が終わったと確かめたら、残った `index.lock` を消す（SIGKILL で止めた git は片付けずに終わる）。同じ worktree でほかの実行が走っていれば消さない
  - **始められなかった実行は `ReportBeginFailure` で Task に渡し、走らせて落ちたときと同じ規則（1 回はやり直し、続けて落ちたら stage-errors）で Task が決める。** worktree を戻せない・HEAD が取れない・止めた実行が終わらないときに出す。出さないと、実行は requested のまま誰も進めない
  - 止めた実行は、走っている実行の一覧から外す。止めた後に同じ実行を再開（`ResumeStage`）したら、配り直しと取り違えずに走らせる
- 統合に失敗したら（`IntegrationFailed`）、反応 `abort-rebase` がそのタスクの worktree の途中の rebase を取りやめる。Rebase と CutBranch は、流す前に途中の rebase を取りやめる（`StageSpec.abandons_rebase`。実行器が流す前に取りやめる）。解きかけを `git add` した rebase は衝突の段が消え、CheckUnion が両側を読めない。CutBranch は、HEAD を切り離した worktree でも切ったブランチに移す
- Rebase は、流す前に途中の rebase を取りやめてから、始めた時点（`start_commit`）へ戻す（`StageSpec.restores_start`。宣言するのは Rebase だけ）。HEAD が始めた時点と違うのは、rebase を終えてから結果が載る前に driver が落ちた流し直しのときだけで、そのときだけ戻る（`reset --keep`。初めて流すときの汚れた worktree は消さない）。`abandons_rebase` を宣言したステージは、begin でも HEAD を取る前に途中の rebase を取りやめる。rebase 途中の HEAD を始めた時点にすると、戻すときにタスクのコミットをブランチから落とす
- CutBranch の切る元は `GitJob.cut_point`（`CutPoint`: 切る元・ランの base か・HEAD を切り離すか）が決める。ランの base だけは origin にあればそちらから切る（手元の base は古いことがある）。autodev が切ったブランチは手元から切る。CutBranch は切った元のコミットを結果の `base` に、Rebase は載せ直した先を `onto` に返す（流し直しでも返す）。`BranchRebased` を出すのは rebase を終えたときだけで、衝突で止まった Rebase では出さず、解いて続けたステージ（`StageSpec.finishes_rebase`。CheckUnion）が完了したときに、同じフローの Rebase が返した先で出す。統合に失敗して取りやめた道では出さない
- Rebase が載せ直すのは、根元から上のコミット（`git rebase --onto <一番上> <Task.base_commit>`）である。積み直すブランチは前に積んだブランチ（`GitJob.previous`）から切るので、破棄した下のタスクのコミットも含むが、それは根元より下にあるので載せない。載せ直すコミットが 0 件なら、Rebase の中身はブランチを動かさずに数だけを返し、Task が失敗に数える（`EvidenceCheck.OWN_COMMITS`。根元が無く数えられない `None` も失敗にする）。載せ直すものがあるかは、中身が流す前に `stages.has_own_commits` に聞く。期待する証拠に外れたときの報告（`on_mismatch`）を書かないステージは、外れたら失敗に数える。§10 の「残した一番上から切り直す」は、切り直した結果が「残した一番上＋そのタスクのコミット」になることを言う

### 配線と統括（5b1）

- **タスクのブランチの根元（差分の起点）は `Task.base_commit`、計画タスクのコードを読む所は `Task.code_tree`。** CutBranch の結果の `base`（切った元のコミット）を `WorktreeReady.base` に、Rebase の結果の `onto`（載せ直した先）を `BranchRebased` に載せ、ポリシーが `RecordBase` → `BaseRecorded` で、その worktree を使うタスクに渡す。積み直しで根元が動いても、最後に載せ直した先になる
- 統括が差し戻し（`MAX_CORRECTIONS`）と呼び直し（続けたセッションで `MAX_ATTEMPTS` 回、新しいセッションで 1 回）を使い切ったら、driver が `ReportSupervisorFailure` を出し、Run が `supervisor-failed` を上げる。**受けるのは応じなかった統括の 1 段上**（`EscalationRouter.route`）で、タスク統括ならラン統括、ラン統括なら `/autodev`（ポリシーが `PostQuestion` で質問にし、回答待ちの終了コード 4 で止まる）。答えたら（`EscalationAnswered`・`AnswerRecorded` の `failed_notice`）、応じなかった統括を新しいセッションで、元の知らせと回答を載せた `retry` の知らせで起こし直す。タスク統括への回答は notes に残さない
- StructuredOutput を返さずに普通に終わったターン（result が誤りでない）は、落ちたとみなさず、判断の形の誤りとして同じセッションに差し戻す
- 統括の判断の CommandId は、起こした知らせだけで決める（差し戻した回数を入れない）。拒まれた判断はイベントを出さず id が残らないので、直した判断が同じ id で通る。知らせ 1 つに通る判断は 1 つで、呼び直した driver が控えから起こし直しても、通った判断と同じ id になって `has_command` で弾かれる。通った判断があるかを問う Run・Task の問いは足さない（集約の処理済みの id がその問いになる）
- 統括の Panic の CommandId は、知らせと `Run.resumes`（RunResumed の数）で決める。呼び直した後に同じ知らせでまた利用枠に当たったら、新しいパニックになる。`ReportSupervisorFailure` の id には入れない（同じ知らせの 2 度目は弾く。答えた後の起こし直しは回答のイベントが知らせになる）
- **`supervisor-failed` を受けたかは Run が覚える**（`Run.reported_failure`。閉じた後も）。2 度目の `ReportSupervisorFailure` は何も出さず、呼び直した driver は、受けた知らせで統括を起こし直さない（上げ終えた後の始末の前に落ちた）
- `Panic` は、ランを終えた後も走るタスク（仕上げの並びを走らせる git 管理タスク）が残っている間は受ける。その上げに応じるラン統括が利用枠に当たることがあり、拒むと統括を二度と起こさない
- **ランを終えた後に受けてよいかは、知らせが関わるタスクで決める**（`Run.notice_tasks`）。ラン統括が git 管理タスクのエスカレーション（とその回答）に応じなかったら、その `supervisor-failed` と、それへのユーザーの回答は git 管理タスクのものとして受ける
- **ユーザーが受ける Run のエスカレーション（`RunEscalation.for_user`。ラン統括自身の `supervisor-failed` だけ）に、ラン統括は応えない。** `answer`・`respondsTo`・`trigger` で名指すと拒み、`still-open` にも入れない。`ask-user` は、Questions が「1 つのエスカレーションに回答を待つ質問は 1 つだけ」として拒む（ポリシーがすぐ質問にしているので、重ねた質問になる）
- **タスク統括が同じ知らせに続けて応じなかった数は `EscalationRaised.failures`** で、起こし直しの知らせ（`supervisor-failed` への回答のイベント）にまた応じなければ 1 つ増える。数えるのは同じ知らせの輪だけで、別の知らせなら 1 から数える。`MAX_SUPERVISOR_FAILURES` を超えても受けるのはラン統括のままで（段を飛ばさない）、Run が、ラン統括が自分で書いた `answer` を拒み、ユーザーの回答の QuestionId（`question`）を添えることを求める（`EscalationRouter.requires_user_answer`。`MAX_REPLANS_WITHOUT_STACK` と同じ形）。ラン統括は `ask-user` で聞き、届いた回答を `question` に添えて答え、その答えでタスク統括が `retry` で起きる（ラン統括が毎回「続けて」と答えても輪が止まる）。`stop-tasks`・`replan` で応えてもよい
- **エスカレーションが回答以外で閉じたら（`EscalationClosed`）、それを経路に持つ回答待ちの質問を取り下げる**（ポリシー `withdraw-questions` → `WithdrawQuestions` → `QuestionWithdrawn`）。ラン統括が `ask-user` の後に `stop-tasks`・`replan` で閉じると、質問が回答を待ったまま残り、`awaiting_answer` が真のまま終了コード 4 で止まるためである。`/autodev` には `questions/<QuestionId>.json` の `status: withdrawn` と `reason`（閉じた理由）で見せる
- **質問が answered になった後、回答が Run に届く前にエスカレーションが閉じたら、回答は記録するが、ラン統括を起こさない**（`AnswerRecorded.escalation_closed`）。答える先が無いので、起こすと `answer` が拒まれ続け、`supervisor-failed` から同じ知らせで起こし直す輪になる。印を付けるのは Run で開いたことのあるエスカレーション（`Run.opened_escalations`）への回答だけで、開いたことの無い id（`ask-user` の `escalation` の取り違え）への回答は今までどおり起こす。`<回答の記録>` には `escalationClosed: true` で見せる
- `retry` の知らせは、起こし直しが重なっても最初の知らせを 1 段だけ載せる（途中の回答は載せない）

### status --json の形（段 6）

HUD（statusline・`autodev-watch`）と `/autodev` が読む、外向けの形。組み立てるのは `infra/status.py`（`run_status`・`all_statuses`）と `infra/status_sections.py` で、集約をそのまま JSON にしない。状態の見せ方の判断（ランがどこにいるか・段がどこまで進んだか など）は、集約の問い（`Run.phase`・`Task.step_states`・`Task.flow_finished`・`Task.current_executions`・`Design.proposal_state`・`Questions.open_questions`・`Stack.entry_of`・`Run.applied_design`）が答える。

- `run_status` は 1 つのランのオブジェクト（ランが無ければ FileNotFoundError）、`all_statuses` は状態の置き場にある全ランの配列（ラン名の順）を返す。どちらを CLI のどの呼び方に当てるかは `cli.py` が決める
- `all_statuses` は、`events.db` のある所が読めなければ（ラン名の規則に合わない・壊れた・この版が読めないイベント・欄を作る所の不具合）、そのランを `{"name": "<ディレクトリ名>", "error": "<例外の種類>: <文>"}` で残す。`error` のある要素には、ほかの欄が無い。`events.db` の無い所はランではないので載せない
- 欄を足すだけなら `format` は上げない。読む側は知らない欄を無視する
- 時刻はすべて UTC の ISO 8601（`2026-10-03T01:02:03.456789Z`）。イベントを確定した時刻で、ステージが走り始めた正確な時刻ではない
- **driver が生きているかは、この形からは分からない。** `updated_at` はイベントを確定した時刻、`progress.updated` は進み具合を最後に書いた時刻で、どちらも生存の目安にしない。決定的なステージは進み具合を始めに 1 回しか書かず、LLM のステージも長い Bash の間は書かないので、走っていても古くなる

```json
{
  "format": 1,
  "name": "add-cache",
  "last_seq": 57,
  "updated_at": "2026-10-03T01:02:03.456789Z",
  "rejections": [],
  "run": {"phase": "running", "awaiting_answer": true, "started_at": "…", "repository": "/repo",
          "base": "main", "limit": 2, "resumes": 0},
  "tasks": [
    {"id": "task1", "kind": "implementation", "title": "パーサを足す", "status": "running",
     "terminal": false, "blocked_by": [], "branch": "stack/add-cache--task-1", "pr": null,
     "superseded_by": null, "takes_over": null, "integration_failed": false, "awaiting_requeue": false,
     "flow": {"version": 1, "finished": false, "halted": false, "job": null,
              "steps": [{"stage": "TestGen", "state": "done"},
                        {"stage": "ReviewLoop", "state": "current", "inner": "Judge", "round": 2},
                        {"stage": "Gate", "state": "pending"}]},
     "executions": [{"id": "task1-Judge-r2-a1", "stage": "Judge", "round": 2, "attempt": 1, "flow_version": 1, "step": 1,
                     "status": "running", "started_at": "…",
                     "progress": {"stage": "Judge", "state": "running", "turns": 7, "lastTool": "Read",
                                  "hookDenials": 0, "events": 41, "updated": "…"}}],
     "escalations": []}
  ],
  "stack": {"overview": {"task": "git", "branch": "autodev/add-cache", "pr": 4, "base": "main"},
            "entries": [], "top": "autodev/add-cache", "queue": [], "current": null, "parked": null,
            "paused": false, "cuts_pending": 0},
  "questions": [{"id": "q1", "body": "…", "escalation": "run#7"}],
  "escalations": [{"id": "run#7", "kind": "ask", "task": "planning", "source": "task/planning#5",
                   "for_user": false, "answer_only": false, "failures": 0}],
  "plan": {"planning": false, "planned": true, "applied_design": 1, "replans_without_stack": 0,
           "design": {"versions": [1, 2], "settled": 1,
                      "proposal": {"version": 2, "state": "awaiting", "round": 1,
                                   "awaiting": "design-ambiguous"}}}
}
```

| 欄 | 意味 | 取りうる値 |
| --- | --- | --- |
| `format` | 形の版 | `1` |
| `last_seq`・`updated_at` | 最後のイベントの通し番号と確定した時刻。イベントが無ければ `0`・`null` | |
| `rejections` | 拒んだコマンド（`logs/rejected.jsonl`）。`at`・`type`・`command_id`・`issuer`・`reason` | |
| `run.phase` | ランがどこにいるか。`panicked` は仕上げの途中にも起きるので、`finishing` より先に見せる | `not-started`（RunStarted が無い）・`planning`（初めての計画を反映する前）・`running`・`panicked`（呼び直すまで進まない）・`finishing`（ランを終え、git 管理タスクの仕上げが残る）・`finished`（終了コード 0 で終える） |
| `run.awaiting_answer` | `/autodev` の回答を待つ質問がある。ほかのタスクが進んでいても真になる | `true`・`false` |
| `run.resumes` | 呼び直された回数 | |
| `tasks[]` | 計画タスク・git 管理タスク（始めていれば）、実装タスク（番号の順） | |
| `tasks[].kind` | タスクの種類 | `planning`・`implementation`・`git` |
| `tasks[].status` | Run が持つ状態（ADDENDUM §2・§11） | `pending`・`running`・`escalated`・`gated`・`stacking`・`stacked`・`dropped`（止めた）・`superseded`（引き継がれた）・`discarded`（積んだ後に破棄した）・`finished`（計画・git 管理タスクの終わり） |
| `tasks[].terminal` | 終端の状態か。`stacked` も終端に数える | |
| `tasks[].pr`・`branch` | 今スタックに積んである PR の番号（積んでいなければ `null`）と、タスクのブランチ。計画タスクと git 管理タスクは常に `null`（概要 PR は `stack.overview`） | |
| `tasks[].awaiting_requeue` | 破棄した所より上にあり、閉じ終えたら積み直すのを待っている（`stacked` のまま） | |
| `tasks[].flow` | 今のフロー。無ければ `null`（始めていない・範囲が変わって組み直しを待つ） | |
| `flow.steps[].state` | 段の進み具合。`current` の段が合成ステージなら、`inner`（中のステージ）と `round` が付く | `done`・`skipped`（飛ばした。衝突しなかった Rebase の後など）・`current`・`pending` |
| `flow.finished`・`flow.halted` | フローを終えた・捨てた（置き換えを待つ） | |
| `flow.job` | git 管理タスクの処理している仕事（`id`・`kind`・`task`・`branch`）。ほかは `null` | `kind`: `cut-overview`・`cut-task`・`cut-stack-top`・`open-overview`・`rewrite-overview`・`stack`・`discard`・`finish` |
| `tasks[].executions[]` | 今のフローの実行と、書き直す前のフローでまだ走っている実行（始めた順）。`stage` は `StageKind` の値。`step` は `flow_version` の版のフローの段の添字で、`flow_version` が `flow.version` と違えば今の `flow.steps` の段ではない | `status`: `requested`（始めるのを待つ）・`running`・`completed`・`reported`（報告で終えた）・`failed`・`interrupted`・`deferred`（ask の回答待ち）・`abandoned`・`restarted`・`refused`（結果を受けてもらえなかった） |
| `executions[].started_at` | 最後に StageStarted を確定した時刻（再開したら再開した時刻）。まだなら `null` | |
| `executions[].progress` | 実行器が書く進み具合（`progress/<id>.json`）。`status` が `running` でない・ファイルが無ければ `null`。`updated` は最後に書いた時刻で、生存の目安にしない。`turns`・`lastTool`・`hookDenials`・`events` は LLM のステージだけ | |
| `tasks[].escalations[]` | タスクの側で開いているエスカレーション（`id`・`kind`・`origin` の実行） | `kind`: 下の `escalations[].kind` と同じ |
| `stack.overview`・`entries[]` | 概要 PR と積んだ PR（下から）。`task`・`branch`・`pr`・`base` | |
| `stack.queue`・`current`・`parked` | git 管理タスクの仕事の列・処理中の仕事・戻す回数の上限で止めた仕事（形は `flow.job`） | |
| `stack.paused`・`cuts_pending` | 再計画の間で積まない・破棄して閉じ終えていない数 | |
| `questions[]` | 回答を待っている質問（`id`・`body`・`escalation`）。答えた・取り下げた質問は載せない（`questions/<id>.json` を見る） | |
| `escalations[]` | Run の側で開いているエスカレーション。`for_user` が真ならラン統括は応えず、`/autodev` の回答で閉じる。`answer_only` が真なら答え以外では閉じない。`failures` は supervisor-failed で続けて応じなかった数 | `kind`: `stall`・`design-gap`・`test-conflict`・`ask`・`red-check-failed`・`untested-change`・`gate-unfixable`・`stage-errors`・`design-ambiguous`・`design-reverted`・`design-rounds-exhausted`・`integration-failed`・`needs-replan`・`needs-human`・`question`・`result-refused`・`supervisor-failed` |
| `plan.planning` | 計画が進んでいる（初回はランの開始から、再計画は頼まれてから、反映するまで） | |
| `plan.planned`・`applied_design` | 計画を反映したか・最後に反映した設計の版 | |
| `plan.replans_without_stack` | 1 本も積まずに続けた再計画の数 | |
| `plan.design.versions`・`settled` | 入った設計の版（増えるだけ）と、最後に確定した版 | |
| `plan.design.proposal` | 確定していない提案。無ければ `null`。`round` は今の提案の何ラウンド目か | `state`: `judging`・`revising`・`awaiting`。`awaiting`: `design-rounds-exhausted`・`design-reverted`・`design-ambiguous`（`state` が `awaiting` のときだけ。ほかは `null`） |

`/autodev` が終了コード 0 の後に見分けること: 積んだのは `kind` が `implementation` で `status` が `stacked` のタスク、止めたのは `dropped`、破棄したのは `discarded`。回答待ち（終了コード 4）なら `run.awaiting_answer` が真で、`questions[]` に答える。
