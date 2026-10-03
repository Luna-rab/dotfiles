"""子プロセスの控え（`adapters/children.py`）: pid と開始時刻と boot_id で、同じプロセスかを見分ける。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from autodevlib.adapters import children


@pytest.fixture
def tracked(tmp_path: Path):
    children.track_in(tmp_path)
    yield tmp_path
    children.track_in(None)


def test_控えを置いた子は生きている間だけ残り終わったら消える(tracked: Path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        children.record(child.pid, ["python", "-c", "sleep"])
        assert children.survivors(tracked) == [children.Child(child.pid, "python -c sleep")]
    finally:
        child.kill()
        child.wait()
    assert children.survivors(tracked) == []
    # もう生きていない控えは消す
    assert list(tracked.glob("*.json")) == []


def test_pidが同じでも開始時刻が違えば別のプロセスとみなす(tracked: Path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        children.record(child.pid, ["python"])
        path = tracked / f"{child.pid}.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        body["start"] = str(int(body["start"]) - 1)
        path.write_text(json.dumps(body), encoding="utf-8")
        assert children.survivors(tracked) == []
    finally:
        child.kill()
        child.wait()


def test_置き場を決めていなければ控えを置かない(tmp_path: Path):
    children.record(1, ["init"])
    children.forget(1)
    assert list(tmp_path.iterdir()) == []
