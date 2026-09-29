"""計画ステージが決めた設定の置き場（`app/inputs.py`・`app/planning.py` の `apply_plan`）。

ここが狂うと、あるランのために決めた変更禁止パスや検証コマンドが次のランに残り、無関係な作業を止める。
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from autodevlib.app import planning
from autodevlib.app.context import Ctx
from autodevlib.app.inputs import load_config
from autodevlib.config import paths
from autodevlib.ports import files


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Ctx:
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTODEV_CONFIG_DIR", str(tmp_path / "config"))
    run = paths.Run("demo")
    run.ensure()
    st: dict[str, Any] = {
        "name": "demo",
        "repo": "/src/demo",
        "base": "main",
        "overviewBranch": "stack/demo--task-0",
        "testGlobs": [],
        "verify": [],
        "tasks": [],
        "decisions": [],
        "deferrals": [],
    }
    return Ctx(run=run, st=st)


def plan_result(**over: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fitsOnePr": True,
        "tasks": [{"subject": "範囲指定", "tier": "standard", "dod": "", "acceptance": "1-3"}],
        "design": "",
        "verify": ["uv run pytest -q"],
        "blocked": False,
    }
    result.update(over)
    return result


REPO_DEFAULT = {"verify": ["make test"], "testGlobs": ["**/test_*.py"], "protected": ["install.sh"]}


def run_config(ctx: Ctx) -> dict[str, Any]:
    loaded = files.read_json(ctx.run.config)
    assert isinstance(loaded, dict)
    return loaded


def test_計画ステージが決めた設定をリポジトリ共通の設定に書かない(ctx):
    planning.apply_plan(ctx, dict(REPO_DEFAULT), plan_result(protected=["routes/web.php"]))
    assert run_config(ctx)["protected"] == ["routes/web.php"]
    assert not os.path.exists(paths.repo_config("/src/demo"))


def test_計画ステージが空の変更禁止パスを返したら禁止なしにする(ctx):
    planning.apply_plan(ctx, dict(REPO_DEFAULT), plan_result(protected=[], testGlobs=[]))
    config = run_config(ctx)
    assert config["protected"] == []
    assert config["testGlobs"] == []


def test_計画ステージがキーを省いたらリポジトリ共通の値のまま使う(ctx):
    planning.apply_plan(ctx, dict(REPO_DEFAULT), plan_result())
    config = run_config(ctx)
    assert config["protected"] == ["install.sh"]
    assert config["testGlobs"] == ["**/test_*.py"]
    assert config["verify"] == ["uv run pytest -q"]


def test_ランの設定が無ければリポジトリ共通の設定を読む(ctx):
    files.write_json(paths.repo_config("/src/demo"), REPO_DEFAULT)
    assert load_config("/src/demo", ctx.run) == REPO_DEFAULT
