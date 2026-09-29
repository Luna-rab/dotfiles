"""計画ステージと、回答待ち（ステージの `ask` と driver の質問）の park / 再開。

**呼び直すかどうかは driver が決めない。** 進めなくなったら、待っている事実と質問を出して
終了コードに載せる。
"""

from __future__ import annotations

import os
from typing import Any, NoReturn

from ..config import paths, stages
from ..core import task_order
from ..ports import console, files, run_store, runner, templates
from . import design
from .context import EXIT_PLAN_BLOCKED, EXIT_WAITING, Ctx, Waiting
from .inputs import load_config, save_config, write_brief
from .stage_call import DESIGN_ID, call, record_judgements


def unanswered(run: paths.Run) -> list[dict[str, Any]]:
    """まだ回答が置かれていない質問。"""
    out = []
    for key in run.question_keys():
        if os.path.exists(run.answer(key)):
            continue
        loaded = files.read_json(run.question(key))
        out.append(loaded if isinstance(loaded, dict) else {"id": key, "question": ""})
    return out


def park(ctx: Ctx, stage_name: str, got: runner.Result) -> NoReturn:
    """ステージが回答を待って止まった。**質問を出して終わる。**

    進めるのは回答が置かれてからで、**回答を作るかどうか・呼び直すかどうかを決めるのは
    呼び出し元のエージェントである。** driver は待っている事実と質問だけを返す。
    """
    st = ctx.st
    st["deferred"] = {"stage": stage_name, "session": got.session_id}
    st["questions"] = unanswered(ctx.run)
    ctx.save()
    console.info(f"{stage_name} ステージが回答を待っている。回答を置いてから呼び直してください。")
    for item in st["questions"]:
        print(f"- [{item.get('id')}] {item.get('question')}")
    print()
    print(f'{paths.launcher()} answer --name {st["name"]} --id <質問 ID> --body "<回答>"')
    raise SystemExit(EXIT_WAITING)


def ask_human(ctx: Ctx, wait: Waiting) -> NoReturn:
    """タスクを進めるのに人の判断が要る。質問を書き出し、回答待ちで終わる。

    ステージの `ask` と同じ置き場（`questions/`）に書くので、回答は同じ `autodev answer` で置ける。
    回答が置かれたら `take_answers()` がタスクの `notes` に写し、タスクは `phase` から続く。
    """
    st = ctx.st
    for item in wait.questions:
        files.write_json(ctx.run.question(item["id"]), item)
    st["deferred"] = {"stage": "task", "task": wait.task_id}
    st["questions"] = unanswered(ctx.run)
    ctx.save()
    console.info(f"{wait.task_id} を進めるのに人の判断が要る。回答を置いてから呼び直してください。")
    for item in st["questions"]:
        print(f"- [{item.get('id')}] {item.get('question')}")
    print()
    print(f'{paths.launcher()} answer --name {st["name"]} --id <質問 ID> --body "<回答>"')
    raise SystemExit(EXIT_WAITING)


def take_answers(ctx: Ctx) -> None:
    """driver が出した質問に回答が揃っていれば、タスクの `notes` と判断ログに写して片付ける。

    まだ揃っていなければ、質問を出し直して回答待ちで終わる。
    """
    st = ctx.st
    deferred = st.get("deferred") or {}
    if deferred.get("stage") != "task":
        return
    waiting = unanswered(ctx.run)
    if waiting:
        st["questions"] = waiting
        ctx.save()
        console.info(f"{deferred.get('task')} はまだ回答を待っている。")
        for item in waiting:
            print(f"- [{item.get('id')}] {item.get('question')}")
        raise SystemExit(EXIT_WAITING)
    owner = str(deferred["task"])
    # 設計の確かめで聞いたことは、タスクではなく設計のステージに渡す
    holder = design.state(st) if owner == DESIGN_ID else run_store.task(st, owner)
    for key in ctx.run.question_keys():
        asked = files.read_json(ctx.run.question(key))
        answered = files.read_json(ctx.run.answer(key))
        question = asked.get("question", "") if isinstance(asked, dict) else ""
        answer = answered.get("answer", "") if isinstance(answered, dict) else ""
        note = f"{question} → {answer}"
        holder.setdefault("notes", []).append(note)
        run_store.add_decision(st, "decision", f"人の判断（{owner}）: {note}")
        if key.endswith("-replan-limit"):
            # 数え直さないと、回答しても次の再計画で同じ質問に戻り、再計画ステージが回答を読めない
            st["replansSinceStack"] = 0
    clear_questions(ctx)
    ctx.save()


def clear_questions(ctx: Ctx) -> None:
    """止まっていたステージが進んだので、質問と回答を片付ける。

    残すと、あとのステージが同じ質問 ID で聞いたときに前の回答が黙って返る。
    """
    for key in ctx.run.question_keys():
        files.remove(ctx.run.question(key))
        files.remove(ctx.run.answer(key))
    ctx.st["deferred"] = None
    ctx.st["questions"] = []


def plan(ctx: Ctx, config: dict[str, Any]) -> None:
    """計画ステージを呼び、結果を**提案**として持つ。state.json に写すのは設計の指摘が
    0 件になってから（`apply_plan()`）。"""
    st = ctx.st
    known = "## リポジトリ共通の既定値\n\n" + (
        "\n".join(f"- 検証コマンド: `{c}`" for c in config["verify"])
        or "- まだ無い（CI 定義から拾って結果に載せる）"
    )
    deferred = st.get("deferred") or {}
    if deferred.get("stage") == "plan" and deferred.get("session"):
        waiting = unanswered(ctx.run)
        if waiting:
            st["questions"] = waiting
            ctx.save()
            console.info("計画ステージはまだ回答を待っている。")
            for item in waiting:
                print(f"- [{item.get('id')}] {item.get('question')}")
            raise SystemExit(EXIT_WAITING)
        got = call(ctx, stages.TABLE["plan"], None, "0", resume_from=str(deferred["session"]))
    else:
        got = call(ctx, stages.TABLE["plan"], None, "0", extra=known)
    take_result(ctx, got, config)


def revise(ctx: Ctx, extra: str) -> None:
    """設計の指摘を、計画ステージに同じセッションの続きで直させ、提案し直す。

    **同じセッションを続ける**のは、読んだコードを読み直させないため（1 回の計画で入力が
    20 万トークンを超える）。タスクはまだ 1 本も始まっていないので、割り方ごと置き換えてよい。
    """
    proposal = design.pending(ctx.st) or {}
    got = call(
        ctx, stages.TABLE["plan"], None, "0", extra=extra, continue_from=proposal.get("session")
    )
    take_result(ctx, got, load_config(ctx.st["repo"], ctx.run))


def take_result(ctx: Ctx, got: runner.Result, config: dict[str, Any]) -> None:
    """計画ステージの結果を確かめて、提案として持つ。進めない結果なら終わる。"""
    if got.deferred is not None:
        park(ctx, "plan", got)
    clear_questions(ctx)
    if not got.ok:
        console.die(f"計画ステージが失敗した: {got.error}（ログ: {got.log}）")

    result = got.result or {}
    if result.get("blocked") and design.pending(ctx.st) is not None:
        # 設計の指摘を直している途中。指示を書き直して呼び直すと続きを失うので、人に聞いて続ける
        asked = [str(q) for q in result.get("questions") or []] or [
            "計画ステージが設計の指摘を直せなかった（疑問点の記録なし）"
        ]
        raise Waiting(
            DESIGN_ID,
            [{"id": f"{DESIGN_ID}-plan-q{n}", "question": q} for n, q in enumerate(asked, 1)],
        )
    if result.get("blocked"):
        # **呼び直すかどうかは呼び出し元のエージェントが決める。** driver は疑問点を出して終わる
        console.info("計画ステージが blocked を返した。次の点を決めてから呼び直してください。")
        print("\n".join(f"- {q}" for q in result.get("questions", [])) or "(疑問点の記録なし)")
        raise SystemExit(EXIT_PLAN_BLOCKED)
    if not result.get("tasks"):
        console.die("計画ステージがタスクを 1 件も返さなかった")
    # 設計レビューを回す前に落とす。検証コマンドが無いと、確かめた割り方でも完了を判定できない
    if not (result.get("verify") or config["verify"]):
        console.die(
            "検証コマンドが確定しなかった。完了チェック⑥を流せないので走らない"
            f"（`{ctx.run.config}` に手で書いて起動し直すこともできる）"
        )
    previous = design.pending(ctx.st) or {}
    design.propose(ctx, "plan", got.session_id or previous.get("session"), result)


def apply_plan(ctx: Ctx, config: dict[str, Any], result: dict[str, Any]) -> None:
    """確かめ終えた計画ステージの結果を state.json に写し、ブリーフを書き出す。**1 回だけ呼ぶ。**"""
    st = ctx.st
    tasks = result.get("tasks") or []
    # `or` で選ぶと、計画ステージが空の一覧（禁止するパスは無い）を返しても前の値が残る
    config = {
        "verify": result.get("verify") or config["verify"],
        "testGlobs": result["testGlobs"] if "testGlobs" in result else config["testGlobs"],
        "protected": result["protected"] if "protected" in result else config["protected"],
    }
    save_config(ctx.run, config)
    st["testGlobs"] = config["testGlobs"]
    st["verify"] = config["verify"]
    task_order.add_tasks(st, st["name"], tasks)
    if len(tasks) > 1:
        run_store.add_decision(
            st,
            "decision",
            f"1 PR に収まらないと判定し、{len(tasks)} 本に割った（計画ステージの判断）",
        )
    record_judgements(st, result)
    # 計画ステージが書いた「気をつけること」を brief に差してから書き出す。**次のステージはこれを読む**
    notes = str(result.get("briefNotes") or "").strip()
    if notes:
        templates.write_prose(ctx.run, "brief-notes", notes)
    write_brief(ctx.run, st, config)
