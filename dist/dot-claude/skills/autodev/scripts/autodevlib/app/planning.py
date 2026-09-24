"""計画段と、答えを待って止まった段の park / 再開。

**呼び直すかどうかは driver が決めない。** 進めなくなったら、待っている事実と質問を出して
終了コードに載せる。
"""

from __future__ import annotations

import os
from typing import Any, NoReturn

from ..config import paths, stages
from ..core import task_order
from ..ports import console, files, run_store, runner, templates
from .context import EXIT_PLAN_BLOCKED, EXIT_WAITING, Ctx
from .inputs import save_config, write_brief
from .stage_call import call, record_judgements


def unanswered(run: paths.Run) -> list[dict[str, Any]]:
    """まだ答えが置かれていない質問。"""
    out = []
    for key in run.question_keys():
        if os.path.exists(run.answer(key)):
            continue
        loaded = files.read_json(run.question(key))
        out.append(loaded if isinstance(loaded, dict) else {"id": key, "question": ""})
    return out


def park(ctx: Ctx, stage_name: str, got: runner.Result) -> NoReturn:
    """段が答えを待って止まった。**質問を出して終わる。**

    進めるのは答えが置かれてからで、**答えを作るかどうか・呼び直すかどうかを決めるのは
    呼んだ側である。** driver は待っている事実と質問だけを返す。
    """
    st = ctx.st
    st["deferred"] = {"stage": stage_name, "session": got.session_id}
    st["questions"] = unanswered(ctx.run)
    ctx.save()
    console.info(f"{stage_name} 段が答えを待っている。答えを置いてから呼び直してください。")
    for item in st["questions"]:
        print(f"- [{item.get('id')}] {item.get('question')}")
    print()
    print(f'{paths.launcher()} answer --work {st["work"]} --id <鍵> --body "<答え>"')
    raise SystemExit(EXIT_WAITING)


def clear_questions(ctx: Ctx) -> None:
    """止まっていた段が進んだので、質問と答えを片付ける。

    残すと、あとの段が同じ鍵で聞いたときに前の答えが黙って返る。
    """
    for key in ctx.run.question_keys():
        files.remove(ctx.run.question(key))
        files.remove(ctx.run.answer(key))
    ctx.st["deferred"] = None
    ctx.st["questions"] = []


def plan(ctx: Ctx, config: dict[str, Any]) -> None:
    st = ctx.st
    known = "## 既に分かっている設定\n\n" + (
        "\n".join(f"- 検証コマンド: `{c}`" for c in config["verify"])
        or "- まだ無い（CI 定義から拾って結果に載せる）"
    )
    deferred = st.get("deferred") or {}
    if deferred.get("stage") == "plan" and deferred.get("session"):
        waiting = unanswered(ctx.run)
        if waiting:
            st["questions"] = waiting
            ctx.save()
            console.info("計画段はまだ答えを待っている。")
            for item in waiting:
                print(f"- [{item.get('id')}] {item.get('question')}")
            raise SystemExit(EXIT_WAITING)
        got = call(ctx, stages.TABLE["plan"], None, "0", resume_from=str(deferred["session"]))
    else:
        got = call(ctx, stages.TABLE["plan"], None, "0", extra=known)

    if got.deferred is not None:
        park(ctx, "plan", got)
    clear_questions(ctx)
    if not got.ok:
        console.die(f"計画段が失敗した: {got.error}（ログ: {got.log}）")

    result = got.result or {}
    if result.get("blocked"):
        # **呼び直すかどうかは呼んだ側が決める。** driver は疑問点を出して終わる
        console.info("計画段が blocked を返した。次の点を決めてから呼び直してください。")
        print("\n".join(f"- {q}" for q in result.get("questions", [])) or "(疑問点の記録なし)")
        raise SystemExit(EXIT_PLAN_BLOCKED)

    tasks = result.get("tasks") or []
    if not tasks:
        console.die("計画段がタスクを 1 件も返さなかった")

    config = {
        "verify": result.get("verify") or config["verify"],
        "testGlobs": result.get("testGlobs") or config["testGlobs"],
        "protected": result.get("protected") or config["protected"],
    }
    if not config["verify"]:
        console.die(
            "検証コマンドが確定しなかった。検査⑥を流せないので走らない"
            f"（`{ctx.run.config}` に手で書いて起動し直すこともできる）"
        )
    save_config(st["repo"], ctx.run, config)
    st["testGlobs"] = config["testGlobs"]
    st["verify"] = config["verify"]
    task_order.add_tasks(st, st["work"], tasks)
    if len(tasks) > 1:
        run_store.add_decision(
            st, "decision", f"1 PR に収まらないと判定し、{len(tasks)} 本に割った（計画段の判断）"
        )
    record_judgements(st, result)
    # 計画段が書いた「気をつけること」を brief に差してから書き出す。**次の段はこれを読む**
    notes = str(result.get("briefNotes") or "").strip()
    if notes:
        templates.write_prose(ctx.run, "brief-notes", notes)
    write_brief(ctx.run, st, config)
