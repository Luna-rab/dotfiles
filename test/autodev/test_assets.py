"""指示書・スキーマ・テンプレート・フック・launcher が実在すること。

**これらのパスは、他のどの検査も踏まない。** `config/paths.py` の `skill_root()` が 1 階層
ずれると、5 つ全部が実在しない場所を指す。そのとき ruff も ty も CLI の起動も通るので、
気づけるのはランを 1 本潰したあとである。

パスの計算は文字列の連結なので、**実在するかどうかはここでしか分からない。**
"""

from __future__ import annotations

import json
import os

import pytest
from autodevlib.config import paths, stages
from autodevlib.ports import templates
from conftest import SKILL_ROOT

#: 結果を返すステージだけがスキーマを持つ（`claude --json-schema` に渡す）
WITH_SCHEMA = sorted(name for name, s in stages.TABLE.items() if s.writes_result)


def test_置き場はSKILLmdがある場所である():
    """`skill_root()` は `SKILL.md` を見つけるところまで上って決まる。

    階層を数えて上ると、`config/paths.py` を別の階層へ動かしたときに黙ってずれる。
    """
    assert paths.skill_root() == str(SKILL_ROOT)
    assert os.path.isdir(os.path.join(paths.skill_root(), "scripts", "autodevlib"))


def test_SKILLmdが実在する():
    """`skill_root()` はこのファイルを探す。無くなると全部のパスが壊れる。"""
    assert os.path.exists(os.path.join(paths.skill_root(), "SKILL.md"))


def test_launcherが実在して直接起動できる():
    """ステージは `autodev review …` をこの絶対パスで呼ぶ。PATH に頼らない。

    プレースホルダ表の `<autodev>` にそのまま入るので、実行権が落ちるとステージが呼べない。
    """
    assert os.path.exists(paths.launcher()), paths.launcher()
    assert os.access(paths.launcher(), os.X_OK), paths.launcher()


@pytest.mark.parametrize("name", ["deny-writes", "park-on-ask"])
def test_フックが実在する(name: str):
    """フックは claude の子プロセスとして別に起動される。**無くても driver は落ちない。**

    `deny-writes.py` が起動しないと、読むだけのステージが worktree を書き換えられる。
    """
    assert os.path.exists(paths.hook(name)), paths.hook(name)


@pytest.mark.parametrize("name", sorted(stages.TABLE))
def test_全ステージの指示書が実在する(name: str):
    """指示書が無いステージは、何をするかを渡されないまま起動する。"""
    path = paths.contract(stages.TABLE[name].contract)
    assert os.path.exists(path), path
    assert os.path.getsize(path) > 0, path


@pytest.mark.parametrize("name", WITH_SCHEMA)
def test_結果を返すステージのスキーマがjsonとして読める(name: str):
    """`claude --json-schema` に渡すので、読めない JSON はステージの起動時に落ちる。"""
    path = paths.schema(stages.TABLE[name].contract)
    assert os.path.exists(path), path
    with open(path, encoding="utf-8") as fh:
        schema = json.load(fh)
    assert schema["type"] == "object", path
    assert schema["properties"], path


def test_結果を返すステージの一覧が変わっていない():
    """ステージを足してスキーマを置き忘れると、そのステージだけが結果を返せない。"""
    assert WITH_SCHEMA == ["fix", "impl", "judge", "plan", "pr-body", "summary", "testgen"]


def test_テンプレートを全部読める():
    """テンプレートが読めないと `ports/templates.py` の `template()` が `die()` する。"""
    found = sorted(p.stem for p in (SKILL_ROOT / "templates").glob("*.md"))
    assert found, "templates/*.md が 1 つも無い"
    for name in found:
        assert templates.template(name).strip(), name


def test_ステージへ渡す文面のテンプレートが本文から始まる():
    """マーカーだけの空ファイルに差し替わっていないことを見る。"""
    assert templates.template("prompt").startswith("あなたは")
