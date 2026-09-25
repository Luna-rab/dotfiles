"""ステージを 1 回呼ぶ。渡す値・環境変数・セッション・結果の形をここで決める。

**ステージの中身は決めない。** 文面を組むのは `core/prompt.py`、起動するのは `ports/runner.py` で、
ここはその 2 つへ渡すものを揃えて結果を記録に落とす。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from ..config import paths, stages
from ..core import events
from ..core import prompt as prompt_lib
from ..ports import console, files, review_store, run_store, runner, templates
from .context import Ctx, Waiting

#: フックに何回止められたらステージを打ち切るか。指示書を読み違えているステージは、そのまま続けても
#: 直らないので、ターンの上限まで使い切る前に止める
BLOCK_LIMIT = 10
#: 進行を state.json に書き出す間隔（秒）
PROGRESS_EVERY = 5.0


def stage_values(
    ctx: Ctx,
    stage: stages.Stage,
    task: dict[str, Any] | None,
    round_label: str,
    extra: str,
) -> dict[str, Any]:
    run, st = ctx.run, ctx.st
    values: dict[str, Any] = {
        "run_name": st["name"],
        "instruction": st.get("instruction"),
        "tree": run.tree,
        "brief": run.brief,
        "map": run.map,
        "round": round_label,
        "extra": extra,
    }
    if task:
        values.update(
            {
                "task_id": task["id"],
                "tier": task["tier"],
                "subject": task["subject"],
                "dod": task["dod"],
                "acceptance": task["acceptance"],
                "scope": task["scope"],
                "entrypoints": task["entrypoints"],
                "contracts": task["contracts"],
                "branch": task["branch"],
                "parent": task.get("parent"),
                "review": run.review(task["id"]),
                "notes": task.get("notes") or [],
            }
        )
    return values


def stage_env(ctx: Ctx, stage: stages.Stage) -> dict[str, str | None]:
    """ステージごとに渡す環境変数。**渡さないものは `None` で外す**（空文字では
    「設定されている」と読む相手がいる）。

    - ジャッジトークンはジャッジだけ。他のステージが status を動かせると自己承認になる
    - テストの解禁はテスト作成ステージだけ
    - ソースを書き換えないステージは worktree の中を書けない（`hooks/deny-writes.py` が止める）
    - **資格情報は全ステージで外す。** `ANTHROPIC_API_KEY` が残っていると claude が
      サブスクリプションではなく従量課金に切り替わる。無人のマシンで使う
      `CLAUDE_CODE_OAUTH_TOKEN` は claude 自身のものなので通す
    """
    return {
        "AUTODEV_TEST_GLOBS": "\n".join(ctx.st["testGlobs"]),
        "AUTODEV_ALLOW_TESTS": "1" if stage.allow_tests else None,
        "AUTODEV_READ_ONLY": None if stage.edits else "1",
        # `autodev ask` を止めるフックと `autodev ask` 自身が読む
        "AUTODEV_RUN_DIR": ctx.run.dir,
        review_store.JUDGE_TOKEN_ENV: ctx.st["judgeToken"] if stage.judge else None,
        "ANTHROPIC_API_KEY": None,
        "ANTHROPIC_AUTH_TOKEN": None,
        "ANTHROPIC_BASE_URL": None,
    }


def stage_session(task: dict[str, Any] | None, stage: stages.Stage) -> tuple[str | None, bool]:
    """そのセッションを続けるか、新しく立てるか。続けるのは `stage.session` を持つステージだけ。

    id は**呼ぶ側が決める**（`claude --session-id`）。出力から拾わなくてよくなる。
    """
    if not (task and stage.session):
        return None, False
    existing = task.get(stage.session)
    return (existing, True) if existing else (str(uuid.uuid4()), False)


def log_label(run: Any, task_id: str, stage: stages.Stage, round_label: str) -> str:
    """ログ・指示・結果のファイル名に使うラウンド。**既にあれば `-2` `-3` と足して上書きしない。**

    同じラウンドで同じステージを 2 度呼ぶことがある（エラーの呼び直し、テストの直しの後の修正）。
    ステージに渡す `<ラウンド>` は変えない——レビューの走行記録が合わなくなり、完了チェック④が落ちる。
    """
    name = stage.name.replace(":", "-")
    label, count = round_label, 1
    while os.path.exists(run.log(task_id, name, label)):
        count += 1
        label = f"{round_label}-{count}"
    return label


def stage_schema(stage: stages.Stage) -> str | None:
    """ステージの結果の形。**`claude --json-schema` はファイルパスではなく JSON の本文を取る。**"""
    if not stage.writes_result:
        return None
    path = paths.schema(stage.contract)
    loaded = files.read_json(path)
    if not isinstance(loaded, dict):
        console.die(f"結果のスキーマが読めない: {path}")
    return json.dumps(loaded, ensure_ascii=False)


def stage_watch(ctx: Ctx, stage: stages.Stage) -> Any:
    """ステージを走らせながら driver が見る係を作る。**判断するのはこのコードで、モデルは入らない。**

    見るのは 2 つだけである。

    - **進行**（ターン数と直前のツール）を state.json に書く。ステージの途中の様子が外から見える
    - **ガードとの衝突**。`BLOCK_LIMIT` 回止められたステージは打ち切る。指示書を読み違えていて、
      そのまま続けても直らないので、ターンの上限まで使い切る前に止める

    ツール 1 回ごとの許可をここでやらない。同期の関門を挟むと、答える相手が生きていない
    と進めなくなり、無人で回せなくなる。
    """
    seen = {"turns": 0, "tool": "", "blocked": 0}
    wrote = 0.0

    def watch(event: dict[str, Any]) -> str | None:
        nonlocal wrote
        denied = events.guard_denials(event)
        if denied:
            seen["blocked"] = int(seen["blocked"]) + denied
            if seen["blocked"] >= BLOCK_LIMIT:
                return f"フックに {seen['blocked']} 回止められた（指示書を読み違えている）"
        if event.get("type") == "assistant":
            seen["turns"] = int(seen["turns"]) + 1
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    seen["tool"] = str(block.get("name") or "")
        now = time.monotonic()
        if now - wrote >= PROGRESS_EVERY:
            wrote = now
            ctx.progress(stage.name, turns=seen["turns"], tool=seen["tool"])
        return None

    return watch


def call(
    ctx: Ctx,
    stage: stages.Stage,
    task: dict[str, Any] | None,
    round_label: str,
    *,
    extra: str = "",
    resume_from: str | None = None,
) -> runner.Result:
    """ステージを 1 回呼ぶ。**結果がスキーマに合わなければ、そのステージは失敗である。**

    `resume_from` を渡すと、回答を待って止まったステージをそのセッションから再開する。
    **そのときプロンプトは渡さない**——渡すと新しいターンが始まり、止まったツール呼び出しが
    再開されない。
    """
    run = ctx.run
    values = stage_values(ctx, stage, task, round_label, extra)
    task_id = task["id"] if task else "task0"
    if resume_from:
        session, resume, prompt = resume_from, True, ""
    else:
        session, resume = stage_session(task, stage)
        prompt = prompt_lib.build_prompt(
            stage,
            values,
            template=templates.template("prompt"),
            contract_path=paths.contract(stage.contract),
            launcher_path=paths.launcher(),
        )

    system_append = prompt_lib.system_append(stage)
    name = stage.name.replace(":", "-")
    label = log_label(run, task_id, stage, round_label)
    if not resume_from:
        # 再開ではプロンプトを渡さないので、最初に渡した指示の記録を残しておく。
        # 必須ルールはどのステージもほぼ同じなので後ろに置き、ステージごとに違うプロンプトを先に読ませる
        files.write_text(
            run.prompt(task_id, name, label),
            f"# プロンプト\n\n{prompt}\n\n"
            f"# 必須ルール（system prompt に足したもの）\n\n{system_append}\n",
        )
    console.info(
        f"{stage.role}ステージ（{task_id} / r{label}）を{'再開' if resume_from else '起動'}"
    )
    ctx.begin(stage.name, task_id, label)
    ok = False
    try:
        got = runner.run(
            runner.Call(
                stage=stage.name,
                prompt=prompt,
                system_append=system_append,
                cwd=run.tree,
                log_path=run.log(task_id, name, label),
                json_schema=stage_schema(stage),
                model=stage.model,
                effort=stage.effort,
                max_turns=stage.max_turns,
                settings=run.guard,
                session_id=session,
                resume=resume,
                env=stage_env(ctx, stage),
                timeout=stage.timeout,
                watch=stage_watch(ctx, stage),
            )
        )
        ok = got.ok
    finally:
        ctx.end(stage.name, ok=ok)
    if got.result is not None:
        # **記録は driver が書く。** ステージに書かせないので、在ることと形が保証される
        files.write_json(run.result(task_id, name, label), got.result)
    if task is not None and stage.session and got.session_id:
        task[stage.session] = got.session_id

    console.info(f"  → {'ok' if got.ok else 'NG'} / {runner.usage_line(got)}")
    for warning in got.warnings:
        console.info(f"  → {warning}")
    if not got.ok and got.error:
        console.info(f"  → {got.error.splitlines()[0][:200]}")
    return got


def call_or_wait(
    ctx: Ctx,
    stage: stages.Stage,
    task: dict[str, Any],
    round_label: str,
    *,
    extra: str = "",
) -> runner.Result:
    """タスクのステージを呼ぶ。**エラーで終わったら 1 回だけ呼び直し、それでも落ちたら人に聞く。**

    エラーの多くは一時的なもの（ネットワーク、レート制限）である。2 回続けて落ちるなら、
    ステージの外に原因があるので人が見る。回答が置かれたら、タスクは同じ `phase` から続く。
    """
    got = call(ctx, stage, task, round_label, extra=extra)
    if got.ok:
        return got
    console.info(f"{stage.role}ステージがエラーで終わったので、1 回だけ呼び直す")
    got = call(ctx, stage, task, round_label, extra=extra)
    if got.ok:
        return got
    raise Waiting(
        task["id"],
        [
            {
                "id": f"{task['id']}-{stage.name.replace(':', '-')}-error",
                "question": f"{stage.role}ステージが 2 回続けてエラーで終わった: {got.error}"
                f"（ログ: {got.log}）。原因を取り除いたら、続けてよいと回答してください。",
            }
        ],
    )


def record_judgements(st: dict[str, Any], result: dict[str, Any]) -> None:
    """ステージが報告した「自分で決めたこと」「スコープ外にしたもの」を残す。

    バックグラウンドに埋もれると、いつの間にか目標が変わったことに誰も気づけない。
    """
    for entry in result.get("decisions") or []:
        run_store.add_decision(st, "decision", str(entry))
    for entry in result.get("deferrals") or []:
        run_store.add_decision(st, "deferral", str(entry))
