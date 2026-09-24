"""ランの仕上げで出す表の列の揃え方（`autodevlib/app/finish.py`）。"""

from __future__ import annotations

from autodevlib.app.finish import pad


def test_全角を2桁として右を埋める():
    assert pad("スタック済み", 14) == "スタック済み  "
    assert pad("失敗", 14) == "失敗" + " " * 10


def test_幅を超える文字列は切らない():
    assert pad("スタック済み", 4) == "スタック済み"
