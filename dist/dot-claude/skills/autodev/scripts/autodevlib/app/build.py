"""コードとテストを書くステージ（テスト作成・実装・修正）を呼ぶ。

**テストを直せるのはテスト作成ステージだけである。** 実装・修正ステージに直させると、テストを
通すためにテストを緩める経路ができる。実装・修正ステージがテストの矛盾を報告したら、どの
ラウンドでもテスト作成ステージに受入条件と照らして確かめさせ、正しければ直させる。

**テスト作成ステージは設計ファイルにある形だけを呼ぶ。** 足りない形があると報告したら
（`designGap`）、テストを書かせずに再計画で設計を直させる。実装・修正ステージが設計の形を
変える必要があると報告したら（`interfaceChange`）、同じく再計画で設計を直させ、テストを
新しい設計に合わせてから実装を続ける。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import stages
from ..core import globs, task_order
from ..ports import console, evidence, repo, run_store
from .context import Ctx, NeedsReplan, Waiting
from .stage_call import call_or_wait, record_judgements


def questions_of(
    task: dict[str, Any], stage_code: str, result: dict[str, Any]
) -> list[dict[str, str]]:
    """ステージが「受入条件が曖昧」と返したときの、人への質問。"""
    asked = [str(q) for q in result.get("questions") or []] or [
        "受入条件が一意に定まらない（ステージが疑問点を書かなかった）"
    ]
    return [
        {"id": f"{task['id']}-{stage_code}-q{index}", "question": question}
        for index, question in enumerate(asked, start=1)
    ]


def make_tests(ctx: Ctx, task: dict[str, Any], label: str, extra: str = "") -> bool:
    """テスト作成ステージ。**このステージだけテストへ書ける**（driver が `AUTODEV_ALLOW_TESTS` を渡す）。

    戻り値はテストのコミットが増えたか。テストの矛盾の報告を誤りと判断したステージは commit しない。

    **そのタスクで初めてテストを書いたときだけ、実装の前にテストが落ちることを確かめる**
    （`confirm_red()`）。2 回目からは実装があるので、落ちなくてよい。

    確かめの途中で止まったら、`redCheck` を見て続きから進める。

    - `running`——検証コマンドを流している間に driver が落ちた。テストは書き直さず、確かめだけやり直す
    - `asked`——「実装の前から通る」を人に聞いた。回答に沿って書き直させ、確かめはもうしない
      （確かめ直すと、「このまま進めてよい」という回答でも同じ質問に戻る）
    """
    red = task.get("redCheck")
    if red == "asked":
        task.pop("redCheck", None)
        return write_tests(ctx, task, label, ANSWERED_NOTE + extra)
    if red == "running":
        confirm_red(ctx, task, label, extra)
        return False
    first = not task.get("testsAt")
    changed = write_tests(ctx, task, label, extra)
    if first:
        confirm_red(ctx, task, label, extra)
    return changed


def write_tests(ctx: Ctx, task: dict[str, Any], label: str, extra: str) -> bool:
    before = repo.head_sha(ctx.run.tree)
    got = call_or_wait(ctx, stages.TABLE["testgen"], task, label, extra=extra)
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, "testgen", result))
    gap = reported(result.get("designGap"))
    if gap:
        # 設計に無い形をテスト作成ステージが決めると、誰も確かめないまま実装を縛る。設計を直させる
        raise NeedsReplan(task["id"], gap, [], kind="design-gap")
    after = repo.head_sha(ctx.run.tree)
    # **テストを書いた時点のコミットを控える。** 完了チェック⑤はここから先でテストが動いていないかを
    # 見る（テスト作成ステージはタスクのブランチに commit するので、parent から見ると必ず差分が出る）
    run_store.set_task(ctx.st, task["id"], testsAt=after)
    task["testNotes"] = str(result.get("notes") or "")
    note_stubs(ctx, task, before, after)
    return before != after


def note_stubs(ctx: Ctx, task: dict[str, Any], before: str | None, after: str | None) -> None:
    """テスト作成ステージがテスト以外に触ったファイルを控える。通常レビューがスタブかを確かめる。

    テスト作成ステージはテスト以外も書ける（スタブを置くため）。フックでは中身を見分けられないので、
    実装のロジックを書いていないかはレビューに任せる。
    """
    if not (before and after) or before == after:
        return
    touched = repo.changed_files(ctx.run.tree, before, after)
    tests = set(globs.pick(touched, ctx.st["testGlobs"]))
    others = [path for path in touched if path not in tests]
    task["stubFiles"] = sorted({*(task.get("stubFiles") or []), *others})


def confirm_red(ctx: Ctx, task: dict[str, Any], label: str, extra: str = "") -> None:
    """実装の前に検証コマンド一式が落ちることを確かめる。**通ってしまったら 1 回だけ書き直させ、
    それでも通るなら人に聞く。**

    テスト作成ステージが返す `commands` ではなく、完了チェック⑥と同じ検証コマンド
    （`task_order.verify_commands()`）を流す。
    ステージが選んだコマンドだと、いつも落ちるコマンド（パスの誤り・テストが 1 本も集まらない）でも
    「落ちた」ことになる。検証コマンドは実装の後に完了チェック⑥で通ることも確かめるので、
    前で落ちて後で通れば、新しいテストが流れている。

    見分けられるのは「全部通った＝新しいテストが 1 本も落ちていない」だけである。lint など
    テスト以外で落ちても Red とみなすので、落ちたことはテストが正しいことの証明にはならない。
    """
    commands = task_order.verify_commands(ctx.st, task)
    if not commands:
        return
    task["redCheck"] = "running"
    ctx.save()
    if failing(ctx, commands):
        task.pop("redCheck", None)
        return
    console.info(f"  {task['id']}: 実装の前なのに検証コマンドが全部通った。テストを書き直させる")
    write_tests(ctx, task, label, green_note(commands) + ("\n\n" + extra if extra else ""))
    if failing(ctx, commands):
        task.pop("redCheck", None)
        return
    task["redCheck"] = "asked"
    raise Waiting(
        task["id"],
        [
            {
                "id": f"{task['id']}-tests-green",
                "question": f"{task['id']} のテストを書き直しても、実装の前から検証コマンドが全部通る。"
                "受入条件がすでに満たされているか、テストが受入条件を主張していない。"
                "進め方（受入条件を書き直す・このタスクは要らない など）を決めてください。",
            }
        ],
    )


ANSWERED_NOTE = (
    "## 実装の前からテストが通る件の回答\n\n"
    "書いたテストが実装の前から通るので、人に聞いた。回答は「このタスク」の「人が決めたこと」にある。"
    "回答に沿ってテストを直す必要があれば直して commit する。直す必要がなければ、テストを変えず commit もしない。\n\n"
)


def failing(ctx: Ctx, commands: list[str]) -> bool:
    return any(not got.ok for got in evidence.run_verify(ctx.run.tree, commands))


def green_note(commands: list[str]) -> str:
    listed = "\n".join(f"- `{c}`" for c in commands)
    return (
        "## 実装の前なのにテストが通った\n\n"
        f"driver が次の検証コマンドを流したが、すべて通った。\n\n{listed}\n\n"
        "書いたテストが受入条件を主張していないか、受入条件がすでに満たされている。受入条件と照らして"
        "テストを書き直し、**実装の前に落ちるテストにして commit する。** 受入条件がすでに満たされて"
        "いるなら、テストを変えずに `blocked: true` と、その旨を `questions` に書く。"
    )


def reported(value: Any) -> str | None:
    """ステージが報告欄に書いた文字列。無いときに `null` ではなく文字列の "null" を返すステージがある。"""
    if not isinstance(value, str) or value.strip().lower() in ("", "null", "none"):
        return None
    return value.strip()


def write_code(
    ctx: Ctx, task: dict[str, Any], stage_code: str, label: str, extra: str = ""
) -> dict[str, Any]:
    """実装（`impl`）か修正（`fix`）を呼ぶ。テストファイルは read-only にして走らせ、終わったら必ず戻す。

    フックはランの頭で書いた `guard.json` を全ステージに渡してあるので、ここで出し入れするのは
    ファイルの書き込み権だけである（フックの裏をかかれても書けないようにする二重の栓）。
    """
    repo.lock_tests(ctx.run.tree, ctx.st["testGlobs"])
    try:
        got = call_or_wait(ctx, stages.TABLE[stage_code], task, label, extra=extra)
    finally:
        repo.unlock_tests(ctx.run.tree, ctx.st["testGlobs"])
    result = got.result or {}
    if result.get("blocked"):
        raise Waiting(task["id"], questions_of(task, stage_code, result))
    record_judgements(ctx.st, result)
    change = reported(result.get("interfaceChange"))
    if change:
        # **報告の時点で**設計を直させる。スタックに追加してからでは、却下されても形を戻せない
        raise NeedsReplan(task["id"], change, [], kind="interface-change")
    return result


def test_conflict(result: dict[str, Any]) -> str | None:
    # 文字列の "null" を報告として扱うと、テスト作成ステージを無駄に呼び直す
    return reported(result.get("testConflict"))


def settle_conflicts(
    ctx: Ctx,
    task: dict[str, Any],
    result: dict[str, Any],
    label: str,
    redo: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """実装・修正ステージの「テストが仕様と矛盾している」という報告を片付ける。

    テスト作成ステージが報告を正しいと判断してテストを直したら、`redo` で同じ手を呼び直す。
    誤りと判断してテストを変えなかったら、その判断を渡して 1 回だけ呼び直し、以後の報告は
    レビューとジャッジに任せる（同じ報告でテスト作成ステージを呼び続けない）。
    """
    conflict = test_conflict(result)
    while conflict:
        extra = (
            "## 実装・修正ステージからの報告\n\n"
            f"{conflict}\n\n"
            "この報告を受入条件と照らして検証し、**正しければテストを直して commit する**。"
            "誤っていれば直さず、理由を結果の `notes` に書く。"
        )
        if not make_tests(ctx, task, label, extra=extra):
            return redo(
                "## テスト作成ステージの判断\n\n"
                "報告したテストの矛盾を、テスト作成ステージは誤りと判断してテストを変えなかった。"
                f"理由: {task.get('testNotes') or '（記録なし）'}\n\nテストに合わせて実装する。"
            )
        result = redo(
            "## テストを直した\n\n報告を受けてテスト作成ステージがテストを直した。直したテストが通るようにする。"
        )
        conflict = test_conflict(result)
    return result


def build(ctx: Ctx, task: dict[str, Any], extra: str = "") -> None:
    """最初の実装。テスト作成と実装はレビュー前なのでラウンド 0 で記録する。"""
    result = write_code(ctx, task, "impl", "0", extra)
    settle_conflicts(
        ctx, task, result, "0", lambda extra: write_code(ctx, task, "impl", "0", extra)
    )
