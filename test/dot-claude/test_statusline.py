"""`statusline.py` の検査。Claude Code と同じく、標準入力に JSON を渡して標準出力を読む。"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import pytest
from conftest import CLAUDE_SCRIPTS

SCRIPT = CLAUDE_SCRIPTS / "statusline.py"


def session(now: float) -> dict:
    return {
        "model": {"display_name": "Opus 5.5"},
        "effort": {"level": "high"},
        "workspace": {"current_dir": "/nonexistent/dotfiles"},
        "context_window": {"used_percentage": 50},
        "cost": {"total_cost_usd": 3.214},
        "rate_limits": {
            # 窓の 6 割が過ぎたところで 72% 使っている → 12 ポイント使いすぎ
            "five_hour": {"used_percentage": 72, "resets_at": now + 5 * 3600 * 0.4 + 30},
            "seven_day": {"used_percentage": 31},
        },
        "pr": {"number": 22, "url": "https://example.com/pull/22", "review_state": "pending"},
    }


def write_run(root, **over) -> None:
    now = dt.datetime.now().astimezone()
    state = {
        "work": "range-field",
        "stackPr": 4,
        "updatedAt": now.isoformat(),
        "running": {
            "review:adversarial": {
                "task": "task2",
                "round": "1",
                "at": (now - dt.timedelta(minutes=4, seconds=12)).isoformat(),
                "turns": 26,
                "tool": "Read",
            }
        },
        "tasks": [
            {"id": "task1", "subject": "パーサの土台", "status": "stacked", "pr": 5},
            {
                "id": "task2",
                "subject": "範囲指定",
                "status": "running",
                "stages": [
                    {"name": "testgen", "round": "0", "ok": True},
                    {"name": "impl", "round": "0", "ok": True},
                    {"name": "review:normal", "round": "1", "ok": True},
                ],
            },
            {"id": "task3", "subject": "CLI", "status": "pending"},
            {"id": "task4", "subject": "移行", "status": "blocked", "reason": "受入条件が曖昧"},
        ],
    }
    state.update(over)
    (root / "range-field").mkdir()
    (root / "range-field" / "state.json").write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture(scope="module")
def statusline():
    spec = importlib.util.spec_from_file_location("statusline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def raw(stdin: str, tmp_path, columns: int) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "COLUMNS": str(columns), "AUTODEV_STATE_DIR": str(tmp_path)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def run(tmp_path, columns: int = 100) -> list[str]:
    out = raw(json.dumps(session(time.time())), tmp_path, columns)
    assert out.returncode == 0, out.stderr
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out.stdout)
    return plain.splitlines()


def test_セッションは使用量_場所_利用枠2本の4行(tmp_path):
    lines = run(tmp_path)
    assert lines[:2] == ["Opus 5.5 · high   ctx ━━━━━───── 50%   $3.21", "dotfiles"]
    assert lines[2].startswith("5h ")
    assert lines[2].endswith(" 72% ▲12 2h00m")
    # 窓の 6 割が過ぎた位置（40 マスの 23 番目）に目盛りを置く
    assert lines[2][3:].index("┃") == 23
    assert lines[3] == "7d ━━━━━━━━━━━━╾─────────────────────────── 31%"


@pytest.mark.parametrize(
    ("pct", "mark", "want"),
    [(50, None, "━━──"), (10, None, "╾───"), (100, None, "━━━━"), (50, 75, "━━─┃"), (0, 0, "┃───")],
)
def test_棒は罫線で0_5マスまで刻む(statusline, pct, mark, want):
    assert statusline.bar(pct, 4, mark).plain == want


def test_幅で埋めないので縮めても行頭は残る(tmp_path):
    """幅を変えても描き直されないので、右寄せや行末の空白で幅を埋めてはいけない。"""
    assert all(line == line.rstrip() for line in run(tmp_path, columns=200))


def test_幅が足りないと優先度の低い部品から落とす(tmp_path):
    lines = run(tmp_path, columns=40)
    assert lines[0] == "Opus 5.5 · high   ctx ━━━━━───── 50%"


def test_入力が壊れていても落ちない(tmp_path):
    assert raw("not json", tmp_path, 100).returncode == 0


def test_幅が足りればタスクリストを右に置く(tmp_path):
    write_run(tmp_path)
    lines = run(tmp_path, columns=160)
    assert len(lines) == 5
    left, right = lines[0].split("  │  ")
    assert left.startswith("Opus 5.5")
    assert right == "autodev range-field ▸ task2 review r1 · 4m12s 26往復 Read · 土台 PR #4"
    assert lines[2].split("  │  ")[1].startswith("  ◼ task2 範囲指定")
    assert lines[4].split("  │  ")[0].strip() == ""


def test_幅が足りなければタスクリストを下に置く(tmp_path):
    write_run(tmp_path)
    lines = run(tmp_path, columns=100)
    assert lines[4].startswith("autodev range-field ▸ task2 review r1")
    assert lines[5].startswith("  ✔ task1 パーサの土台")
    assert lines[5].endswith("#5")
    assert lines[6].endswith("testgen ✔ › impl ✔ › review ◼ › judge › PR")
    assert lines[7] == "  ◻ task3 CLI"
    assert lines[8].endswith("受入条件が曖昧")


def test_走っている段が無くても実行中のタスクがあれば出す(tmp_path):
    write_run(tmp_path, running={})
    lines = run(tmp_path)
    assert lines[4] == "autodev range-field ▸ task2 検査と PR · 土台 PR #4"
    assert lines[6].endswith("review ✔ › judge › PR")


def test_更新が止まったrunは出さない(tmp_path):
    stale = (dt.datetime.now().astimezone() - dt.timedelta(hours=4)).isoformat()
    write_run(tmp_path, running={}, updatedAt=stale)
    assert len(run(tmp_path)) == 4


def test_修正の巡目を添え古い段は省く(statusline):
    task = {
        "id": "task1",
        "stages": [
            {"name": "testgen", "round": "0", "ok": True},
            {"name": "impl", "round": "0", "ok": True},
            {"name": "review:normal", "round": "1", "ok": True},
            {"name": "judge", "round": "1", "ok": True},
            {"name": "fix", "round": "2", "ok": False},
        ],
    }
    running = [("review:normal", {"task": "task1", "round": "2"}, 10.0)]
    got = statusline.pipeline(task, running).plain
    assert got == "… › judge ✔ › fix r2 ✘ › review r2 ◼ › judge › PR"


@pytest.mark.parametrize(
    ("used", "remaining", "want"), [(72, 7200, 12), (10, 9000, -40), (0, 18000, 0)]
)
def test_ペースは使った割合から窓の経過割合を引く(statusline, used, remaining, want):
    assert statusline.pace(used, remaining, 5 * 3600) == want


@pytest.mark.parametrize(("pct", "rgb"), [(0, "#a6e3a1"), (50, "#f9e2af"), (100, "#f38ba8")])
def test_使用率の色は緑から黄を経て赤へ変わる(statusline, pct, rgb):
    assert statusline.pct_color(pct).color.get_truecolor().hex == rgb


def test_gitの状態を1回の呼び出しから数える(statusline, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (repo / "a").write_text("1")
    subprocess.run([*git, "add", "a"], check=True)
    subprocess.run([*git, "commit", "-qm", "a"], check=True)
    (repo / "a").write_text("2")
    (repo / "b").write_text("new")
    (repo / "c").write_text("staged")
    subprocess.run([*git, "add", "c"], check=True)
    assert statusline.git_summary(str(repo)).plain == "main +1 ~1 ?1"
