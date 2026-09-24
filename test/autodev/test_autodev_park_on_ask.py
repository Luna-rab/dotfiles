"""autodev の `park-on-ask.py` が、ステージの `ask` を入口のパスで見分けること。

このフックが見分けを外すと、**ステージは聞いて待てない。** `ask` がそのまま走って終了コード 3
（回答がまだ無い）を返し、ステージはそれを読んで `blocked` を報告する。ランは終了コード 2 で
終わるので、外からは「前提が崩れていた」と見え、フックが黙って効いていないことは分からない。

字面は `config/paths.py` の `launcher()` から取る。入口のファイル名を変えたら、ここが落ちる。
"""

from __future__ import annotations

import importlib.util
import sys

import pytest
from autodevlib.config import paths
from conftest import SKILL_ROOT

HOOK_PATH = SKILL_ROOT / "hooks" / "park-on-ask.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("autodev_park_on_ask", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_入口のパスで呼ばれたaskを見分ける(hook):
    """ステージがプレースホルダ表の `<autodev>` をそのまま打った形。"""
    assert hook.asked(f'{paths.launcher()} ask --id range-empty --question "空は None か"') == (
        "range-empty",
        "空は None か",
    )


def test_pythonを前に置いた形も見分ける(hook):
    assert hook.asked(f"python3 {paths.launcher()} ask --id goal") == ("goal", "")


def test_ask以外のサブコマンドは止めない(hook):
    assert hook.asked(f"{paths.launcher()} review list --dir /x") is None


def test_入口を通らないコマンドは止めない(hook):
    assert hook.asked("echo ask --id goal") is None
