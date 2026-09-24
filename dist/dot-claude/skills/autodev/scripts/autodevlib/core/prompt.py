"""段へ渡す文面の組み立て。

段へ渡す文面は 2 つに分かれる。

- **system へ足すもの**（`system_append`）——破ると取り返しがつかない不変条件だけ。
  利用者のメッセージに混ぜると、長い契約を読む途中で薄まる
- **利用者のメッセージ**（`build_prompt`）——どこで走っているか、契約と前提の場所、
  読み替え表、このタスクの値。**契約の本文は入れない**（段が自分で読む）

読み替え表を添えるのは、契約を読む前にプレースホルダの指す先を確定させるためである。
表が無いと、段がプレースホルダのまま `autodev review` を叩く。

**テンプレートの本文・契約のパス・launcher のパスは呼び出し側が渡す。** ここはファイルを
読まないので、`config/paths.py` も `ports/templates.py` も import しない。
"""

from __future__ import annotations

from string import Template
from typing import Any

# 受け取る `stage` は `config/stages.py` の `Stage` である。**ここは config を import
# しない**ので、型は `Any` で受ける。読むのは `name` `role` `writes_result` の 3 つだけ。

#: 役割ごとの不変条件。**契約の要約ではない。** 破ると取り返しがつかないものだけを置く
#: （要約を置くと、契約と不変条件の 2 か所が出所になり、片方だけ直す事故が起きる）。
INVARIANTS: dict[str, list[str]] = {
    "common": [
        "PR を作らない・更新しない。`gh` を 1 度も呼ばない（GitHub を触るのは driver だけ）。",
        "push しない。commit までで止める。",
        "`<ツリー>` の外にあるファイルを書き換えない。state.json には触らない。",
    ],
    "result": [
        "終わりに **`StructuredOutput` ツールで結果を返す**。形はそのツールの入力スキーマに"
        "書いてある（外れると差し戻されるので、言い直して合わせる）。",
    ],
    "plan": [
        "コードを書き換えない。読むだけで、commit も作らない。",
        "受入条件が一意に定まらないなら、推測で進めず `blocked: true` と疑問点を返す。",
    ],
    "testgen": [
        "テストだけを書く。実装のコードに触らない。",
        "書いたテストを commit する（`<ブランチ>` の上に 1 コミット以上）。",
        "受入条件から書く。実装しやすさに合わせてテストを緩めない。",
    ],
    "impl": [
        "**テストファイルを変更しない。** フックが止める。テストが仕様と矛盾していると"
        "判断したら、直さずに結果の `testConflict` に理由を書いて終える。",
        "範囲を広げない。ついでの整理をしない。",
        "変更を commit する（`<ブランチ>` の上に 1 コミット以上）。",
        "長時間のジョブを起動して待たない。待つ前に commit する。",
    ],
    "review": [
        "指摘は `<autodev> review new` で立てる。ファイルに直接書かない。",
        "**status を動かさない**（動かせるのは裁定だけ。スクリプトが拒む）。",
        "終わりに `<autodev> review done` を必ず呼ぶ。**指摘 0 件でも呼ぶ**"
        "（呼ばないと「走っていない」と区別できず、検査④で落ちる）。",
        "コードを書き換えない。",
    ],
    "judge": [
        "open の全件に決着を付ける（直ったなら `closed`、直さないなら理由つきで `rejected`）。"
        "中間の状態を残さない。",
        "`status` にはコメントを必ず添える（なぜ閉じたかが残らないと、次のラウンドも人間も追えない）。",
        "コードを書き換えない。レビューを新しく立てない。",
    ],
    "pr-body": [
        "本文だけを書く。commit も PR の操作もしない。",
    ],
}

#: 役割の鍵。`fix` は実装と同じ契約・同じ不変条件で走る
ROLE_KEY = {
    "plan": "plan",
    "testgen": "testgen",
    "impl": "impl",
    "fix": "impl",
    "review:normal": "review",
    "review:adversarial": "review",
    "judge": "judge",
    "pr-body": "pr-body",
    "summary": "pr-body",
}


def _invariants_for(stage: Any) -> list[str]:
    out = list(INVARIANTS["common"])
    if stage.writes_result:
        out += INVARIANTS["result"]
    return out + INVARIANTS[ROLE_KEY[stage.name]]


def system_append(stage: Any) -> str:
    """`--append-system-prompt` に渡す文面。**破ると取り返しがつかないものだけ。**

    system 側に置くのは、段が長い契約とコードを読む間もここが薄まらないようにするためである。
    プレースホルダ（`<ツリー>` など）の指す先は、利用者のメッセージの読み替え表にある。
    """
    lines = [
        f"あなたは autodev の **{stage.role}** の段である。進行を決めるのは driver で、"
        "あなたは自分の段だけを務める。次の段を自分で呼ばない。",
        "",
        "## 何があっても守ること",
        "",
    ]
    lines += [f"- {item}" for item in _invariants_for(stage)]
    return "\n".join(lines)


def _table(stage: Any, values: dict[str, Any], launcher_path: str) -> str:
    """読み替え表。契約の中の表記が、この run で何を指すか。"""
    rows = [
        ("<作業名>", values["work"]),
        ("<タスク>", values.get("task_id") or "(なし)"),
        ("<ラウンド>", values.get("round") or "0"),
        ("<役割>", stage.role),
        ("<ツリー>", values["tree"]),
        ("<起点>", values.get("parent") or "(なし)"),
        ("<ブランチ>", values.get("branch") or "(なし)"),
        ("<前提>", values["brief"]),
        ("<地図>", values["map"]),
        ("<レビュー>", values.get("review") or "(なし)"),
        ("<autodev>", launcher_path),
    ]
    lines = ["| 表記 | 値 |", "| --- | --- |"]
    lines += [f"| `{key}` | `{value}` |" for key, value in rows]
    return "\n".join(lines)


def _task_block(values: dict[str, Any]) -> str:
    if not values.get("task_id"):
        return ""
    lines = [
        "## このタスク",
        "",
        f"- 番号: `{values['task_id']}`（リスク階層 `{values.get('tier', 'standard')}`）",
        f"- 主題: {values.get('subject', '')}",
        f"- DoD（達成すべき状態）: {values.get('dod', '')}",
        f"- 受入条件: {values.get('acceptance', '')}",
    ]
    for label, key in (
        ("範囲", "scope"),
        ("入口", "entrypoints"),
        ("他タスクとの約束", "contracts"),
    ):
        if values.get(key):
            lines.append(f"- {label}: {values[key]}")
    return "\n".join(lines) + "\n\n"


def build_prompt(
    stage: Any,
    values: dict[str, Any],
    *,
    template: str,
    contract_path: str,
    launcher_path: str,
) -> str:
    """段に渡す利用者のメッセージ。**契約の本文と不変条件は入れない。**

    文面は `templates/prompt.md` にある。ここで組むのは、テンプレートに差す 3 つの塊
    （読み替え表・このタスク・起動時の指示）だけである。

    `template` はそのテンプレートの本文で、読むのは呼び出し側である。
    `safe_substitute` なので、**埋め忘れたマーカーはそのまま残る**（例外で落ちない）。
    """
    instruction = (values.get("instruction") or "").strip()
    marks: dict[str, Any] = {
        "role": stage.role,
        "work": values["work"],
        "tree": values["tree"],
        "contract": contract_path,
        "brief": values["brief"],
        "map": values["map"],
        "table": _table(stage, values, launcher_path),
        "task_block": _task_block(values),
        "instruction_block": (
            f"## 指示（起動時に人間が渡したもの）\n\n{instruction}\n\n" if instruction else ""
        ),
        "extra": (values.get("extra") or "").strip(),
    }
    return Template(template).safe_substitute(
        {key: "" if value is None else str(value) for key, value in marks.items()}
    )
