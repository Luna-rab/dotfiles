#!/usr/bin/env python3
"""台帳と stack PR 本文を、1 つの状態ファイル（state.json）から組み立てる。

**このスクリプトは上流の supervisor には無い**（リードのコンテキストを減らすために足した）。
以前は台帳（`<ベース>/ledger.md`）と PR 本文（`<ベース>/stack-pr-body.md`）を、リードが
毎回 `ledger.md` と `stack-pr.md` の書式を読み直して全文書き写していた。タスク 1 本を積む
たびに 2 つの表の同じ行を 2 か所へ書くので、書き写し漏れとコンテキストの両方が積み上がる。

**stack PR** は stacked PR の土台（`stack/<作業名>--task-0`。空コミット 1 つ）に付けた PR で、
全体の計画と進行状況を本文に持つ（../stack-pr.md）。

状態は `<ベース>/state.json` の 1 か所に持ち、**表と定型の節はこのスクリプトが書き出す**。
散文（概要・計画・挙動の変化など）は `<ベース>/prose/*.md` に 1 節 1 ファイルで置き、リードが
`Write` で書く。変わった節だけを書き直せばよい。

サブコマンド:
  init      state.json と prose/ の雛形を作る
  meta      stack PR 番号やセッション ID など、全体の値を後から入れる
  add-task  タスクを 1 件足す（タスク設計のとき）
  set       タスク 1 件の状態・PR 番号・runId・起点・残件などを更新する
  show      state.json をそのまま出す（復旧のときに読む）
  render    ledger.md と stack-pr-body.md を書き出す（`--final` で最終版の節も足す）
  task-body タスク PR の本文に stacked PR の案内を差して書き出す

**stacked PR の並びは `--status stacked` を入れた順に記録される**（`stack_order`）。実物の並びは
`stack.py show` が `gh stack view --json` から読むので、食い違ったらそちらが正しい。

**書くのはリードだけである。** ワークフローのサブエージェントはこのスクリプトを呼ばない。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from typing import Any

from lib.shell import die, emit

#   pending  設計しただけ / running ワークフローが走っている
#   settled  決着したがまだ stacked PR へ積んでいない
#   stacked  stacked PR へ積んでレビュー待ち
#   merged   ユーザーがマージした（リードはマージしない）
#   blocked / failed  打ち切った
STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "settled",
    "stacked",
    "merged",
    "blocked",
    "failed",
)
TIERS: tuple[str, ...] = ("standard", "light")

# prose/<名前>.md に置く散文の節。値は (見出し, 最終版だけの節か, 無いときに出す文)
PROSE: dict[str, tuple[str, bool, str]] = {
    "prelude": ("", False, ""),
    "summary": ("## 概要", False, "<この作業で何が変わるか。挙動の変化を 1〜3 行で>"),
    "plan": (
        "## 全体の計画と DoD",
        False,
        "<作業全体で達成する状態。タスクへの割り方の方針を数行で>",
    ),
    "remaining": ("## 残課題", False, "現時点で無し"),
    "behavior": (
        "## 変更による挙動の変化",
        True,
        "<操作 X をすると、これまでは A だったが、これからは B になる>",
    ),
    "checklist": ("## 確認項目", True, "- [ ] <操作手順> を行うと <観測できる結果> になる"),
    "verification": (
        "## 検証結果",
        True,
        "<brief.md の検証コマンド一式を stacked PR の先頭で流した結果>",
    ),
    "decisions": ("### 変更した最終目標・DoD・スコープ", True, ""),
    "deferrals": ("### 先送り・対象外にした作業", True, ""),
}


def state_path(base: str) -> str:
    return os.path.join(base, "state.json")


def prose_path(base: str, name: str) -> str:
    return os.path.join(base, "prose", f"{name}.md")


def read_state(base: str) -> dict[str, Any]:
    path = state_path(base)
    if not os.path.isfile(path):
        die(
            f"{path} がありません。先に `state.py init` を呼んでください"
            "（セッションが落ちたあとの復旧手順は ../ledger.md にあります）"
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_state(base: str, data: dict[str, Any]) -> str:
    path = state_path(base)
    os.makedirs(base, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def read_prose(base: str, name: str) -> str:
    path = prose_path(base, name)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def find_task(data: dict[str, Any], number: int) -> dict[str, Any]:
    for task in data["tasks"]:
        if task["number"] == number:
            return task
    die(f"task {number} が state.json にありません（`state.py add-task` で足してください）")


def cmd_init(args: argparse.Namespace) -> None:
    """state.json と prose/ の雛形を作る。既にあれば壊さず止まる。"""
    path = state_path(args.base)
    if os.path.isfile(path) and not args.force:
        die(f"{path} が既にあります。作り直すなら --force を付けてください")
    data: dict[str, Any] = {
        "work": args.work,
        # stacked PR の土台のブランチ（stack/<作業名>--task-0）。全タスクブランチの祖先である
        "bottom": args.bottom,
        # stacked PR の土台が向く base ブランチ。gh stack の trunk と同じもの
        "trunk": args.base_branch,
        "default_branch": args.default_branch or args.base_branch,
        "stack_pr": args.stack_pr,
        "lead_session": args.lead_session,
        "created": args.created or datetime.date.today().isoformat(),
        # stacked PR へ積んだ順のタスク番号（下から上へ）。`set --status stacked` が足す
        "stack_order": [],
        "tasks": [],
    }
    write_state(args.base, data)
    os.makedirs(os.path.join(args.base, "prose"), exist_ok=True)
    made = []
    for name in PROSE:
        p = prose_path(args.base, name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("")
            made.append(p)
    emit({"wrote": path, "prose": made}, pretty=True)


def cmd_meta(args: argparse.Namespace) -> None:
    """全体の値を後から入れる（stack PR 番号は §4 で PR を作ってから分かる）。"""
    data = read_state(args.base)
    for key, value in (
        ("stack_pr", args.stack_pr),
        ("lead_session", args.lead_session),
        ("default_branch", args.default_branch),
    ):
        if value is not None:
            data[key] = value
    emit({"wrote": write_state(args.base, data), "stack_pr": data["stack_pr"]}, pretty=True)


def cmd_add_task(args: argparse.Namespace) -> None:
    """タスクを 1 件足す。タスク設計（`../lead-setup.md` §4）で全件をここに入れる。"""
    data = read_state(args.base)
    if any(t["number"] == args.task for t in data["tasks"]):
        die(f"task {args.task} は既にあります（更新は `state.py set`）")
    data["tasks"].append(
        {
            "number": args.task,
            "subject": args.subject,
            "tier": args.tier,
            "deps": [int(d) for d in args.deps.split(",")] if args.deps else [],
            "branch": args.branch,
            # 起動時にこのブランチを切った起点。実装が作る PR の base でもある
            # （`set --parent` で入れる。積んだあとは gh stack link が PR の base を張り替える）
            "parent": None,
            # stacked PR へ積んだときの 1 つ下のブランチ（`set --status stacked` が埋める）
            "stacked_on": None,
            "pr": None,
            "run_id": None,
            "status": "pending",
            "rejected": None,
            "reviews": None,
            "reason": None,
            "dod": args.dod,
            "acceptance": args.acceptance,
            "scope": args.scope,
            "entrypoints": args.entrypoints,
            "contracts": args.contracts,
            "decisions": [],
            "deferrals": [],
        }
    )
    data["tasks"].sort(key=lambda t: t["number"])
    emit({"wrote": write_state(args.base, data), "tasks": len(data["tasks"])}, pretty=True)


def stack_chain(data: dict[str, Any]) -> list[dict[str, Any]]:
    """stacked PR に積んだタスクを、下から上へ並べて返す。"""
    return [find_task(data, n) for n in data.get("stack_order", [])]


def cmd_set(args: argparse.Namespace) -> None:
    """タスク 1 件を更新する。渡した項目だけを書き換える。

    `--decision` と `--deferral` は**足す**（上書きしない）。ワークフローが返した判断を
    ラウンドごとに積むためである。

    `--status stacked` を入れたときは、そのタスクを `stack_order` の末尾に足し、
    1 つ下のブランチを `stacked_on` に書く。**stacked PR の並びを別に渡さなくてよい**
    ——積む順は決着した順で、リードが `stack.py append` を通した順そのものである。
    """
    data = read_state(args.base)
    task = find_task(data, args.task)
    for key, value in (
        ("status", args.status),
        ("pr", args.pr),
        ("run_id", args.run_id),
        ("rejected", args.rejected),
        ("reviews", args.reviews),
        ("reason", args.reason),
        ("branch", args.branch),
        ("parent", args.parent),
    ):
        if value is not None:
            task[key] = value
    if args.status == "stacked":
        order: list[int] = data.setdefault("stack_order", [])
        if task["number"] not in order:
            below = stack_chain(data)
            task["stacked_on"] = below[-1]["branch"] if below else data["bottom"]
            order.append(task["number"])
    for key, values in (("decisions", args.decision), ("deferrals", args.deferral)):
        for value in values or []:
            if value not in task[key]:
                task[key].append(value)
    emit(
        {
            "wrote": write_state(args.base, data),
            "task": task["number"],
            "status": task["status"],
            "pr": task["pr"],
            "stacked_on": task["stacked_on"],
            "stack_order": data.get("stack_order", []),
        },
        pretty=True,
    )


def cmd_show(args: argparse.Namespace) -> None:
    emit(read_state(args.base), pretty=True)


def section(name: str, base: str) -> str:
    """散文の 1 節。中身が無ければ雛形の文を置く（何を書く節なのかが残る）。"""
    heading, _, placeholder = PROSE[name]
    body = read_prose(base, name) or placeholder
    if not heading:
        return body
    return f"{heading}\n\n{body}" if body else ""


def details(summary: str, body: str) -> str:
    """`<details>` で畳んだ 1 節を組み立てる。

    stack PR の本文は人間のレビュワーも読む。先に読ませたいのは PR テンプレートの節
    （概要・変更による挙動の変化・確認項目・残課題）なので、作業の計画と記録
    （全体の計画と DoD・タスク一覧・検証結果・自律判断の記録）はここに畳んで後ろへ置く。

    **`<summary>` の後ろの空行を落とさない。** GitHub は空行が無いと中の markdown を
    描かず、表や箇条書きが記号のまま出る。
    """
    if not body:
        return ""
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def folded_section(name: str, base: str) -> str:
    """散文の 1 節を、`## 見出し` を `<summary>` のラベルに移して畳む。"""
    heading, _, placeholder = PROSE[name]
    return details(heading.lstrip("# "), read_prose(base, name) or placeholder)


def cell(value: Any) -> str:
    return "—" if value in (None, "", []) else str(value)


def pr_cell(value: Any) -> str:
    return "—" if not value else f"#{value}"


def position(data: dict[str, Any], number: int) -> Any:
    """stacked PR の下から数えた位置（土台の 1 つ上を 1 とする）。積んでいなければ None。"""
    order = data.get("stack_order", [])
    return order.index(number) + 1 if number in order else None


def ledger_table(data: dict[str, Any]) -> str:
    rows = [
        "| # | 件名 | tier | 依存 | ブランチ | 起点 | 積んだ位置 | PR | runId | 状態 | 却下した残件 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in data["tasks"]:
        rows.append(
            f"| {t['number']} | {t['subject']} | {t['tier']} | "
            f"{cell(','.join(str(d) for d in t['deps']))} | {cell(t['branch'])} | "
            f"{cell(t.get('parent'))} | {cell(position(data, t['number']))} | "
            f"{pr_cell(t['pr'])} | {cell(t['run_id'])} | {t['status']} | {cell(t['rejected'])} |"
        )
    return "\n".join(rows)


def body_table(data: dict[str, Any]) -> str:
    """stack PR 本文の表。**runId とブランチ名を載せない**（内部の値で、読む意味が無い）。

    「積んだ位置」の列が、下（base 側）から数えた位置である。タスク番号の順とは一致しない
    ——積むのは決着した順だからである。
    """
    rows = [
        "| # | 件名 | tier | 依存 | PR | 積んだ位置 | 状態 | 却下した残件 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in data["tasks"]:
        rows.append(
            f"| {t['number']} | {t['subject']} | {t['tier']} | "
            f"{cell(','.join(str(d) for d in t['deps']))} | {pr_cell(t['pr'])} | "
            f"{cell(position(data, t['number']))} | {t['status']} | {cell(t['rejected'])} |"
        )
    return "\n".join(rows)


def task_details(tasks: list[dict[str, Any]]) -> str:
    """台帳のタスク詳細。設計時に決めた DoD・境界と、決着後の結果を 1 か所に並べる。"""
    blocks = []
    for t in tasks:
        lines = [f"### task {t['number']}: {t['subject']}", ""]
        for label, key in (
            ("DoD", "dod"),
            ("受け入れ基準と検証", "acceptance"),
            ("スコープ境界", "scope"),
            ("調査の入口", "entrypoints"),
            ("隣接タスクとの契約", "contracts"),
            ("レビュー", "reviews"),
            ("打ち切りの理由", "reason"),
        ):
            if t.get(key):
                lines.append(f"- **{label}**: {t[key]}")
        for key in ("decisions", "deferrals"):
            values = t.get(key) or []
            lines.append(f"- **{key}**: none" if not values else f"- **{key}**:")
            lines.extend(f"  - {v}" for v in values)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def collected(tasks: list[dict[str, Any]], key: str) -> list[str]:
    """全タスクの decisions / deferrals を「task<番号>: <中身>」の形で集める。

    バックグラウンドで下された判断なので、集めなければリードの画面にも PR にも残らない
    （`../ledger.md`「自律判断をどこに書くか」）。
    """
    return [f"task{t['number']}: {v}" for t in tasks for v in (t.get(key) or [])]


def autonomy_body(base: str, tasks: list[dict[str, Any]]) -> str:
    """自律判断の記録の中身。散文（リードが書いた分）とタスクから集めた分を並べる。

    見出しを付けずに返すのは、台帳では `## 自律判断の記録` の下に置き、stack PR 本文では
    `<details>` の中に置くためである。
    """
    out = []
    for name, key in (("decisions", "decisions"), ("deferrals", "deferrals")):
        heading, _, _ = PROSE[name]
        lines = [read_prose(base, name)] if read_prose(base, name) else []
        lines.extend(f"- {v}" for v in collected(tasks, key))
        out.append(f"{heading}\n\n" + ("\n".join(lines) if lines else "現時点で無し"))
    return "\n\n".join(out)


def remaining(base: str, tasks: list[dict[str, Any]]) -> str:
    """残課題。打ち切ったタスクは書き忘れないよう state.json から自動で足す。"""
    lines = [read_prose(base, "remaining")] if read_prose(base, "remaining") else []
    lines.extend(
        f"- task{t['number']}（{t['subject']}）を {t['status']} で打ち切った: {cell(t['reason'])}"
        for t in tasks
        if t["status"] in ("blocked", "failed")
    )
    return "## 残課題\n\n" + ("\n".join(lines) if lines else "現時点で無し")


def render_ledger(base: str, data: dict[str, Any]) -> str:
    tasks = data["tasks"]
    head = [
        f"# {data['work']} 台帳",
        "",
        f"- lead-session: {cell(data.get('lead_session'))}",
        f"- stacked PR の土台: {data['bottom']}",
        f"- stack-pr: {cell(data.get('stack_pr'))}",
        f"- trunk（stacked PR の土台が向く base）: {data['trunk']}",
        "- stacked PR の並び（下から）: "
        + (
            " ← ".join([data["bottom"], *(t["branch"] for t in stack_chain(data))])
            if data.get("stack_order")
            else data["bottom"]
        ),
        f"- default-branch: {data['default_branch']}",
        f"- created: {data['created']}",
        "- ベース資料: 同じディレクトリの brief.md / map.md",
        "- 引き継ぎノート: 同じディレクトリの notes/task<番号>/",
        "",
        "**このファイルは `state.py render` が書き出す。手で直さない**"
        "（次の render で消える）。状態は state.json、散文は prose/*.md にある。",
    ]
    parts = [
        "\n".join(head),
        "## 全体のゴールと DoD\n\n" + (read_prose(base, "plan") or "<未記入>"),
        "## タスク一覧\n\n" + ledger_table(data),
        "## タスクの詳細\n\n" + (task_details(tasks) if tasks else "<未記入>"),
        "## 自律判断の記録\n\n" + autonomy_body(base, tasks),
    ]
    return "\n\n".join(parts) + "\n"


def about_stack(data: dict[str, Any]) -> str:
    """本文の先頭に置く「この PR について」。

    **人間のレビュワーが最初に読む節である。** stacked PR は 1 つの作業を依存し合う複数の PR に
    分けたもので、下の PR が入ってから上の PR が入る。上から読み始めると差分の前提がそろって
    いないので、読む順とマージの順をここで示す。GitHub が merge box にスタックを描くのとは別に
    本文へ書くのは、`gh stack` が新しく、merge box だけでは「下から順に」が伝わらないためである。

    関連する PR の一覧は state.json から作るので、1 本積むたびの `render` で最新になる。
    """
    base_pr = f"{pr_cell(data.get('stack_pr'))}（この PR）" if data.get("stack_pr") else "この PR"
    rows = [
        "| 位置 | PR | 内容 | 状態 |",
        "|---|---|---|---|",
        f"| 土台 | {base_pr} | 全体の計画と進行状況 | — |",
    ]
    rows += [
        f"| {position(data, t['number'])} | {pr_cell(t['pr'])} | {t['subject']} | {t['status']} |"
        for t in stack_chain(data)
    ]
    # まだ積んでいないタスク。PR が既にあれば番号を出す（実装が push した時点で PR は立つ）。
    rows += [
        f"| — | {pr_cell(t['pr'])} | {t['subject']} | {t['status']} |"
        for t in data["tasks"]
        if position(data, t["number"]) is None
    ]
    return (
        "## この PR について\n\n"
        "これは **stacked PR**（1 つの作業を、依存し合う複数の PR に分けて積み上げたもの）の"
        f"土台です。base は `{data['trunk']}` で、この PR 自身の差分は空コミット 1 つだけです。\n\n"
        "**レビューとマージは下から順に行ってください。** 下の PR が base に入ってから上の PR が"
        "入ります。`gh stack merge <PR 番号>` を使うと、その PR までをまとめて下から入れられます"
        "（1 本でも入らなければ 1 本も入りません）。\n\n" + "\n".join(rows)
    )


def task_list(data: dict[str, Any]) -> str:
    """タスク一覧（進行状況）の中身。表と、列の読み方。

    base とマージの順序は先頭の `about_stack()` に書くので、ここでは繰り返さない。
    """
    return (
        body_table(data)
        + "\n\n状態は `pending` / `running` / `settled`（決着したがまだ stacked PR へ積んでいない）/ "
        "`stacked`（stacked PR へ積んでレビュー待ち）/ `merged` / `blocked` / `failed`。\n"
        "「積んだ位置」の列は下（base 側）から数えた位置で、タスク番号の順とは一致しない"
        "（積むのは決着した順）。"
    )


def render_body(base: str, data: dict[str, Any], final: bool) -> str:
    """stack PR の本文。

    **先頭は「この PR について」**（`about_stack()`）。stacked PR であることと関連する PR の
    一覧を、レビュワーが何より先に読む位置に置く。

    **続く前半は PR テンプレートの節だけを畳まずに出す**（概要・変更による挙動の変化・
    確認項目・残課題）。人間のレビュワーがこの PR を読むとき、最初に必要なのは
    「この作業で何がどう変わるか」だからである。作業の計画と記録は後半で `<details>` に
    畳む（`details()`）。
    """
    tasks = data["tasks"]
    parts = [about_stack(data), section("prelude", base), section("summary", base)]
    if final:
        parts += [section("behavior", base), section("checklist", base)]
    parts += [
        remaining(base, tasks),
        folded_section("plan", base),
        details("タスク一覧（進行状況）", task_list(data)),
    ]
    if final:
        parts += [
            folded_section("verification", base),
            details("自律判断の記録", autonomy_body(base, tasks)),
        ]
    return "\n\n".join(p for p in parts if p) + "\n"


def task_header(data: dict[str, Any]) -> str:
    """タスク PR の本文の先頭に差す案内。

    タスク PR を読む人は、その 1 本だけを見て「なぜ base がデフォルトブランチでないのか」
    「いつマージできるのか」が分からない。stacked PR であることと読む順、そして全体の入口
    （stack PR）を示す。

    **ここに PR の一覧を焼き込まない。** タスク PR の本文を差し替えるのは決着した 1 回だけで、
    その後に積まれた PR は載らないので古くなる。一覧の置き場は stack PR 1 か所に保つ
    （../design-notes.md「なぜ最初に draft の stack PR を作るか」）。
    """
    return (
        "> **この PR は stacked PR の 1 本です。** 1 つの作業を、依存し合う複数の PR に分けて"
        "積み上げています。\n"
        "> **レビューとマージは下から順に行ってください**"
        "（この PR の base は 1 つ下の PR のブランチです）。\n"
        f"> 作業全体の計画と、関連する PR の一覧は #{data['stack_pr']} にあります。"
    )


def cmd_task_body(args: argparse.Namespace) -> None:
    """PR 本文エージェントが書いた本文の前に案内を差して、タスク PR に載せる本文を書き出す。

    リードが `/create-pr` の `body-file=` に渡すファイルを 1 つ作るだけである。元の `pr-body.md`
    は書き換えない（PR 本文エージェントの成果をそのまま残す）。
    """
    data = read_state(args.base)
    if not data.get("stack_pr"):
        die(
            "stack_pr が state.json に入っていません。"
            "`state.py meta --stack-pr <番号>` で入れてください（../lead-setup.md §4）"
        )
    find_task(data, args.task)  # 実在しないタスク番号で黙って書き出さない
    source = args.body_file or os.path.join(args.base, "notes", f"task{args.task}", "pr-body.md")
    if not os.path.isfile(source):
        die(f"{source} がありません（ワークフローの返り値 prBodyFile を --body-file に渡す）")
    with open(source, encoding="utf-8") as fh:
        body = fh.read().strip()
    out = os.path.join(os.path.dirname(os.path.abspath(source)), "pr-body-final.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(task_header(data) + "\n\n" + body + "\n")
    emit(
        {
            "wrote": out,
            "source": source,
            "task": args.task,
            "next": f"/create-pr base=<起点のブランチ> head=<タスクブランチ> body-file={out}",
        },
        pretty=True,
    )


def cmd_render(args: argparse.Namespace) -> None:
    """台帳と stack PR 本文を書き出す。**両方を同時に書く**（片方だけ古くならない）。"""
    data = read_state(args.base)
    ledger = os.path.join(args.base, "ledger.md")
    body = os.path.join(args.base, "stack-pr-body.md")
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write(render_ledger(args.base, data))
    with open(body, "w", encoding="utf-8") as fh:
        fh.write(render_body(args.base, data, args.final))
    counts: dict[str, int] = {}
    for t in data["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    emit(
        {
            "ledger": ledger,
            "body": body,
            "final": args.final,
            "stack_pr": data.get("stack_pr"),
            "stack_order": data.get("stack_order", []),
            "tasks": counts,
            "next": f"gh pr edit {data.get('stack_pr')} --body-file {body}",
        },
        pretty=True,
    )


def add_base(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--base", required=True, help="ベースディレクトリ（place.py base-dir が返すパス）"
    )


def add_whole_parsers(sub: argparse._SubParsersAction) -> None:
    """作業全体の値を扱うサブコマンド（init / meta / show / render）。"""
    p = sub.add_parser("init", help="state.json と prose/ の雛形を作る")
    add_base(p)
    p.add_argument("--work", required=True, help="作業名")
    p.add_argument(
        "--bottom", required=True, help="stacked PR の土台のブランチ名（stack/<作業名>--task-0）"
    )
    p.add_argument(
        "--base-branch",
        required=True,
        help="stacked PR の土台が向く base ブランチ",
    )
    p.add_argument("--default-branch", help="デフォルトブランチ名（省略時は --base-branch）")
    p.add_argument("--stack-pr", type=int, help="stack PR の番号（後から meta で入れてもよい）")
    p.add_argument("--lead-session", help="リードのセッション ID（/status で確かめる）")
    p.add_argument("--created", help="YYYY-MM-DD（省略時は今日）")
    p.add_argument("--force", action="store_true", help="既にある state.json を作り直す")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("meta", help="stack PR 番号やセッション ID を後から入れる")
    add_base(p)
    p.add_argument("--stack-pr", type=int)
    p.add_argument("--lead-session")
    p.add_argument("--default-branch")
    p.set_defaults(func=cmd_meta)

    p = sub.add_parser("show", help="state.json をそのまま出す")
    add_base(p)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("render", help="ledger.md と stack-pr-body.md を書き出す")
    add_base(p)
    p.add_argument("--final", action="store_true", help="最終版の 4 節も足す（../finish.md §8）")
    p.set_defaults(func=cmd_render)


def add_task_parsers(sub: argparse._SubParsersAction) -> None:
    """タスク 1 件を扱うサブコマンド（add-task / set）。"""
    p = sub.add_parser("add-task", help="タスクを 1 件足す")
    add_base(p)
    p.add_argument("--task", required=True, type=int, help="タスク番号（1 から）")
    p.add_argument("--subject", required=True, help="件名")
    p.add_argument("--tier", required=True, choices=TIERS)
    p.add_argument("--branch", required=True, help="タスクブランチ名")
    p.add_argument("--deps", help="依存するタスク番号をカンマ区切りで（例: 1,2）")
    p.add_argument("--dod", help="達成すべき状態")
    p.add_argument("--acceptance", help="受け入れ基準と検証")
    p.add_argument("--scope", help="スコープ境界")
    p.add_argument("--entrypoints", help="調査の入口")
    p.add_argument("--contracts", help="隣接タスクとの契約")
    p.set_defaults(func=cmd_add_task)

    p = sub.add_parser("task-body", help="タスク PR の本文に stacked PR の案内を差して書き出す")
    add_base(p)
    p.add_argument("--task", required=True, type=int)
    p.add_argument(
        "--body-file",
        help="PR 本文エージェントが書いた本文（既定は <ベース>/notes/task<番号>/pr-body.md）",
    )
    p.set_defaults(func=cmd_task_body)

    p = sub.add_parser("set", help="タスク 1 件の状態や PR 番号を更新する")
    add_base(p)
    p.add_argument("--task", required=True, type=int)
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--pr", type=int, help="タスク PR の番号")
    p.add_argument("--run-id", help="Workflow の返り値の runId")
    p.add_argument("--rejected", type=int, help="却下した残件の件数")
    p.add_argument("--reviews", help="レビューの結果（例: closed 5 件 / rejected 3 件）")
    p.add_argument("--reason", help="blocked / failed の理由")
    p.add_argument("--branch", help="タスクブランチ名を直す場合")
    p.add_argument(
        "--parent",
        help="そのブランチを切った起点のブランチ名（起動時に入れる。PR の base になる）",
    )
    p.add_argument(
        "--decision", action="append", help="自分の判断で変えた目標・DoD・スコープ（足す）"
    )
    p.add_argument("--deferral", action="append", help="先送り・対象外にした作業（足す）")
    p.set_defaults(func=cmd_set)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="state.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add_whole_parsers(sub)
    add_task_parsers(sub)
    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
