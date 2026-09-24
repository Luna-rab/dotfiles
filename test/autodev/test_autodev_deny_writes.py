"""autodev の `deny-writes.py` の検査。

このフックは autodev の**唯一の強制点**である。テストを書けるのはテスト作成段だけ、
読むだけの段は worktree の中を書けない——どちらもここでしか止まらない。緩めすぎると
検査⑤まで気づかず、締めすぎると段が読むだけのコマンドで止まる。

実行はリポジトリのルートから `uv run pytest`。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys

import pytest
from conftest import SKILL_ROOT

HOOK_PATH = SKILL_ROOT / "hooks" / "deny-writes.py"
TREE = "/w"
GLOBS = "test_*.py\ntests/**"

#: 段ごとの環境変数。driver が `stage_env()` で渡すものと同じ形
TESTGEN = {"AUTODEV_ALLOW_TESTS": "1"}
IMPL: dict[str, str] = {}
READ_ONLY = {"AUTODEV_READ_ONLY": "1"}


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("autodev_deny_writes", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def call(hook, monkeypatch, stage: dict[str, str], tool: str, tool_input: dict) -> int:
    for name in ("AUTODEV_READ_ONLY", "AUTODEV_ALLOW_TESTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTODEV_TEST_GLOBS", GLOBS)
    for name, value in stage.items():
        monkeypatch.setenv(name, value)
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": TREE}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return hook.main()


# --- 実装段: テストだけ守る ---------------------------------------------------


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Edit", {"file_path": f"{TREE}/test_app.py"}),
        ("Write", {"file_path": "test_app.py"}),
        ("MultiEdit", {"edits": [{"file_path": f"{TREE}/tests/test_cli.py"}]}),
        ("Bash", {"command": "echo x > test_app.py"}),
        ("Bash", {"command": "echo x >> test_app.py"}),
        ("Bash", {"command": "sed -i s/a/b/ test_app.py"}),
        ("Bash", {"command": "git checkout test_app.py"}),
    ],
)
def test_impl_cannot_touch_tests(hook, monkeypatch, tool, tool_input):
    assert call(hook, monkeypatch, IMPL, tool, tool_input) == 2


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Edit", {"file_path": f"{TREE}/app.py"}),
        ("Write", {"file_path": "src/app.py"}),
        ("Bash", {"command": "echo x > app.py"}),
        ("Read", {"file_path": f"{TREE}/test_app.py"}),
    ],
)
def test_impl_can_touch_source(hook, monkeypatch, tool, tool_input):
    assert call(hook, monkeypatch, IMPL, tool, tool_input) == 0


def test_testgen_can_touch_tests(hook, monkeypatch):
    got = call(hook, monkeypatch, TESTGEN, "Edit", {"file_path": f"{TREE}/test_app.py"})
    assert got == 0


# --- 読むだけの段: worktree の中を全部守る -----------------------------------


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Edit", {"file_path": f"{TREE}/app.py"}),
        ("Write", {"file_path": "README.md"}),
        ("Bash", {"command": "echo x > app.py"}),
        ("Bash", {"command": "git checkout app.py"}),
    ],
)
def test_read_only_cannot_write_in_tree(hook, monkeypatch, tool, tool_input):
    assert call(hook, monkeypatch, READ_ONLY, tool, tool_input) == 2


def test_read_only_can_write_its_result_outside_the_tree(hook, monkeypatch):
    """**結果の JSON は worktree の外にある。** ここを止めると段が結果を返せない。"""
    got = call(hook, monkeypatch, READ_ONLY, "Write", {"file_path": "/run/result.json"})
    assert got == 0


# --- リダイレクトの読み分け ---------------------------------------------------
#
# ここを緩めると段がソースを書き換えられ、締めると読むだけのコマンドで段が止まる。
# `2>&1` は fd の複製、`/dev/null` はファイルではなく、**引用の中の `>` と `rm` は
# シェルに届かない**。


@pytest.mark.parametrize(
    "command",
    [
        "cat app.py 2>&1 | head",
        'grep -rn "x" src README.md tests 2>/dev/null',
        "git log --oneline > /dev/null",
        "uv run pytest -q 2>&1 | tail -20",
        "PYTHONPATH=src python3 -c \"print('inf,inf ->',summarize([inf,inf]))\"",
        'autodev review new --location src/app.py:43 --body "summarize([inf]) -> range: nan"',
        "python3 -c \"print('rm -rf app.py')\"",
    ],
)
@pytest.mark.parametrize("stage", [IMPL, READ_ONLY], ids=["impl", "read_only"])
def test_reading_commands_are_not_writes(hook, monkeypatch, stage, command):
    assert call(hook, monkeypatch, stage, "Bash", {"command": command}) == 0


@pytest.mark.parametrize(
    "command",
    [
        "cat x > app.py",
        "cat x >app.py",
        "cat x >> app.py",
        "cat x 1> app.py",
        "rm app.py",
    ],
)
def test_read_only_still_catches_real_writes(hook, monkeypatch, command):
    assert call(hook, monkeypatch, READ_ONLY, "Bash", {"command": command}) == 2


def test_unreadable_input_does_not_stop_the_stage(hook, monkeypatch):
    monkeypatch.setenv("AUTODEV_TEST_GLOBS", GLOBS)
    monkeypatch.delenv("AUTODEV_READ_ONLY", raising=False)
    monkeypatch.delenv("AUTODEV_ALLOW_TESTS", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
