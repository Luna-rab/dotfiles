"""指示書（`contracts/`）とスキーマ（`schemas/`）の置き場を引き、指示書の「入力」の表を読む。"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from ....domain.value_objects.stage_kind import StageKind


@cache
def skill_root() -> Path:
    """スキルの根。階層を数えて上らず、`SKILL.md` を探して決める。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "SKILL.md").is_file():
            return parent
    raise FileNotFoundError(f"SKILL.md が見つからない（{here} から上）")


def asset_name(kind: StageKind) -> str:
    """ステージの種類の名前から、指示書とスキーマのファイル名（拡張子を除く）。`TestGen` → `test-gen`。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", kind.value).lower()


def contract_path(name: str) -> Path:
    return skill_root() / "contracts" / f"{name}.md"


def schema_text(name: str) -> str:
    """`claude --json-schema` に渡す本文（パスではない）。"""
    return (skill_root() / "schemas" / f"{name}.json").read_text(encoding="utf-8")


#: 指示書のプレースホルダ。`<!--`・`<<<<<<<` は含めない（test_contracts と同じ形）
_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9])<([^<>\s!/`\-=][^<>\n`]*)>")


@cache
def contract_inputs(name: str) -> tuple[str, ...]:
    """指示書の `## 入力` の表の 1 列目に書いたプレースホルダ（表の順）。"""
    text = contract_path(name).read_text(encoding="utf-8")
    section = text.split("## 入力", 1)[1].split("\n## ", 1)[0]
    found: list[str] = []
    for line in section.splitlines():
        if line.startswith("| `<"):
            found += [m for m in _PLACEHOLDER.findall(line.split("|")[1]) if m not in found]
    return tuple(found)
