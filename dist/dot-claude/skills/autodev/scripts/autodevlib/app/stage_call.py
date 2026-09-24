"""段を 1 回呼ぶ。渡す値・環境変数・セッション・結果の形をここで決める。

**段の中身は決めない。** 文面を組むのは `core/prompt.py`、起動するのは `ports/runner.py` で、
ここはその 2 つへ渡すものを揃えて結果を記録に落とす。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ..config import paths, stages
from ..core import prompt as prompt_lib
from ..ports import console, files, review_store, run_store, runner, templates
from .context import Ctx

#: フックに何回止められたら段を打ち切るか。契約を読み違えている段は、そのまま続けても
#: 直らないので、往復の上限まで使い切る前に止める
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
        "work": st["work"],
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
            }
        )
    return values


def stage_env(ctx: Ctx, stage: stages.Stage) -> dict[str, str | None]:
    """段ごとに渡す環境変数。**渡さないものは `None` で外す**（空文字では
    「設定されている」と読む相手がいる）。

    - 裁定の鍵は裁定の段だけ。他の段が status を動かせると自己承認になる
    - テストの解禁はテスト作成段だけ
    - ソースを書き換えない段は worktree の中を書けない（`hooks/deny-writes.py` が止める）
    - **資格情報は全段で外す。** `ANTHROPIC_API_KEY` が残っていると claude が
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
    """そのセッションを続けるか、新しく立てるか。

    **実装と修正だけ続ける。** レビューは毎ラウンドまっさらにする（前のラウンドで自分が
    書いた言い分が残っていると、同じ差分を読み直す意味が薄れる）。

    id は**呼ぶ側が決める**（`claude --session-id`）。出力から拾わなくてよくなる。
    """
    if not (task and stage.resumable):
        return None, False
    existing = task.get("implSession")
    return (existing, True) if existing else (str(uuid.uuid4()), False)


def stage_schema(stage: stages.Stage) -> str | None:
    """段の結果の形。**`claude --json-schema` はファイルパスではなく JSON の本文を取る。**"""
    if not stage.writes_result:
        return None
    path = paths.schema(stage.contract)
    loaded = files.read_json(path)
    if not isinstance(loaded, dict):
        console.die(f"結果のスキーマが読めない: {path}")
    return json.dumps(loaded, ensure_ascii=False)


def stage_watch(ctx: Ctx, stage: stages.Stage) -> Any:
    """段を走らせながら driver が見る係を作る。**判断するのはこのコードで、モデルは入らない。**

    見るのは 2 つだけである。

    - **進行**（往復数と直前のツール）を state.json に書く。段の途中の様子が外から見える
    - **ガードとの衝突**。`BLOCK_LIMIT` 回止められた段は打ち切る。契約を読み違えていて、
      そのまま続けても直らないので、往復の上限まで使い切る前に止める

    ツール 1 回ごとの許可をここでやらない。同期の関門を挟むと、答える相手が生きていない
    と進めなくなり、無人で回せなくなる。
    """
    seen = {"turns": 0, "tool": "", "blocked": 0}
    wrote = 0.0

    def watch(event: dict[str, Any]) -> str | None:
        nonlocal wrote
        if event.get("subtype") == "hook_response" and event.get("exit_code"):
            seen["blocked"] = int(seen["blocked"]) + 1
            if seen["blocked"] >= BLOCK_LIMIT:
                return f"フックに {seen['blocked']} 回止められた（契約を読み違えている）"
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
    """段を 1 回呼ぶ。**結果がスキーマに合わなければ、その段は失敗である。**

    `resume_from` を渡すと、答えを待って止まった段をそのセッションから再開する。
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

    console.info(
        f"段 {stage.name}（{task_id} / r{round_label}）を{'再開' if resume_from else '起動'}"
    )
    ctx.begin(stage.name, task_id, round_label)
    try:
        got = runner.run(
            runner.Call(
                stage=stage.name,
                prompt=prompt,
                system_append=prompt_lib.system_append(stage),
                cwd=run.tree,
                log_path=run.log(task_id, stage.name.replace(":", "-"), round_label),
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
    finally:
        ctx.end(stage.name)
    if got.result is not None:
        # **記録は driver が書く。** 段に書かせないので、在ることと形が保証される
        files.write_json(run.result(task_id, stage.name.replace(":", "-"), round_label), got.result)

    console.info(f"  → {'ok' if got.ok else 'NG'} / {runner.usage_line(got)}")
    for warning in got.warnings:
        console.info(f"  → {warning}")
    if not got.ok and got.error:
        console.info(f"  → {got.error.splitlines()[0][:200]}")
    return got


def record_judgements(st: dict[str, Any], result: dict[str, Any]) -> None:
    """段が申告した「自分で決めたこと」「先送りにしたもの」を残す。

    バックグラウンドに埋もれると、いつの間にか目標が変わったことに誰も気づけない。
    """
    for entry in result.get("decisions") or []:
        run_store.add_decision(st, "decision", str(entry))
    for entry in result.get("deferrals") or []:
        run_store.add_decision(st, "deferral", str(entry))
