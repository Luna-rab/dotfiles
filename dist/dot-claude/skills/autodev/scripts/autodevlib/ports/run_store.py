"""進行状態（state.json）の読み書き。

**state.json は driver だけが書く。** 段には渡さない（渡すと `--dangerously-skip-permissions`
で走る段が進行状態を書き換えられる）。段が返すのは構造化出力で、
それを読んで state に写すのは driver である。

**文面はここに無い。** テンプレートは `templates/*.md` にあり、state.json から塊を
組み立てるのは `core/markdown.py`、その塊をテンプレートのマーカーに埋めるのは
`ports/templates.py` である。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..core import task_order
from . import console, files


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def new_state(
    work: str,
    instruction: str,
    repo: str,
    base: str,
) -> dict[str, Any]:
    return {
        "work": work,
        "instruction": instruction,
        "repo": repo,
        "base": base,
        "stackBranch": f"stack/{work}--task-0",
        "stackPr": None,
        "createdAt": now(),
        "updatedAt": now(),
        "tasks": [],
        "decisions": [],
        "deferrals": [],
        #: いま走っている段。鍵は段の名前（同時に走る 2 体を並べる）
        "running": {},
        #: 答えを待って止まっている段（`{"stage": …, "session": …}`）
        "deferred": None,
        #: まだ答えが置かれていない質問
        "questions": [],
    }


def load(path: str) -> dict[str, Any]:
    data = files.read_json(path)
    if not isinstance(data, dict):
        console.die(f"state.json が読めない: {path}")
    return data


def save(path: str, data: dict[str, Any]) -> None:
    data["updatedAt"] = now()
    files.write_json(path, data)


def task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for item in data["tasks"]:
        if item["id"] == task_id:
            return item
    console.die(f"state.json にそのタスクが無い: {task_id}")


def set_task(data: dict[str, Any], task_id: str, **fields: Any) -> dict[str, Any]:
    item = task(data, task_id)
    if "status" in fields and fields["status"] not in task_order.STATUSES:
        console.die(
            f"status は {' / '.join(task_order.STATUSES)} のいずれかにしてください: {fields['status']}"
        )
    item.update({k: v for k, v in fields.items() if v is not None})
    return item


def add_decision(data: dict[str, Any], kind: str, body: str) -> None:
    """自分の判断で変えた目標・先送りにした作業を残す。

    バックグラウンドに埋もれると「いつの間にか目標が変わった」ことに誰も気づけない。
    土台 PR の本文に出すので、記録が GitHub 側に残る。
    """
    bucket = "deferrals" if kind == "deferral" else "decisions"
    data[bucket].append({"at": now(), "body": body})
