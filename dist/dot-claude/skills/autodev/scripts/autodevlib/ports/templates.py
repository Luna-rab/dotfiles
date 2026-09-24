"""テンプレートにマーカーを埋める。**文面は Python の中に書かない。**

文面は `templates/*.md` にあり、`${名前}` のマーカーを driver が埋める。マーカーの埋め方は
2 種類だけである。

- **数と状態は state.json から毎回組み立てる**（`${tasks}` `${held}` `${decisions}`）。
  agent には書かせない——書かせると出所が state.json と本文の 2 つになり、
  「state.json では blocked なのに本文では進行中」がありえる。
- **自由記述は agent が書いたものを差す**（`${prose}` `${notes}`）。値は `<ランディレクトリ>/prose/*.md` に
  置き、書き出すたびに読み直す。agent を呼ぶのは 1 回で、そのあと何回書き出しても同じ文が入る。

`string.Template` の `safe_substitute` を使うので、**埋め忘れたマーカーはそのまま残る**
（例外で落ちない）。テンプレートに `$` をそのまま出したいときは `$$` と書く。
"""

from __future__ import annotations

import os
from string import Template
from typing import Any

from ..config import paths
from . import console, files


def template(name: str) -> str:
    path = os.path.join(paths.skill_root(), "templates", f"{name}.md")
    if not os.path.exists(path):
        console.die(f"テンプレートが無い: {path}")
    return files.read_text(path)


def fill(name: str, values: dict[str, Any]) -> str:
    """テンプレートを読んでマーカーを埋める。"""
    return Template(template(name)).safe_substitute(
        {key: "" if value is None else str(value) for key, value in values.items()}
    )


# --- agent が書いた自由記述 ------------------------------------------------------


def read_prose(run: paths.Run, name: str, empty: str) -> str:
    """`<ランディレクトリ>/prose/<name>.md` を読む。無ければ `empty` を返す。

    **agent を呼び直さずに何度でも書き出せる**ようにファイルへ置いてある。
    """
    path = run.prose(name)
    if not os.path.exists(path):
        return empty
    return files.read_text(path).strip() or empty


def write_prose(run: paths.Run, name: str, body: str) -> str:
    return files.write_text(run.prose(name), body.strip() + "\n")
