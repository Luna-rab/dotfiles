"""ステージが読むブリーフ（brief.md と map.md）と、リポジトリ固有の設定（config.json）。"""

from __future__ import annotations

import os
from typing import Any

from ..config import paths
from ..core import globs, markdown
from ..ports import files, templates


def load_config(repo: str, run: paths.Run) -> dict[str, Any]:
    """リポジトリ固有の設定。ラン側 → リポジトリ共通のキャッシュ → 既定値の順に探す。"""
    for path in (run.config, paths.repo_config(repo)):
        loaded = files.read_json(path)
        if isinstance(loaded, dict) and loaded.get("verify"):
            return loaded
    return {"verify": [], "testGlobs": list(globs.DEFAULT_TEST_GLOBS), "protected": []}


def save_config(repo: str, run: paths.Run, config: dict[str, Any]) -> None:
    files.write_json(run.config, config)
    files.write_json(paths.repo_config(repo), config)


def write_brief(run: paths.Run, st: dict[str, Any], config: dict[str, Any]) -> None:
    """ステージが読むブリーフを書き出す。

    **数と規約は config.json と state.json から埋め、自由記述（気をつけること）は計画ステージが
    書いたものを差す。**
    """
    files.write_text(
        run.brief,
        templates.fill(
            "brief",
            {
                "run_name": st["name"],
                "repo": st["repo"],
                "base": st["base"],
                "overview_branch": st["overviewBranch"],
                "verify": markdown.bullets(
                    config.get("verify") or [],
                    "まだ確定していない。計画ステージが CI 定義から拾って結果に載せる。",
                ),
                "test_globs": markdown.bullets(
                    config.get("testGlobs") or list(globs.DEFAULT_TEST_GLOBS), "（既定のみ）"
                ),
                "protected": (
                    "## 変更禁止パス\n\n" + markdown.bullets(config["protected"], "") + "\n\n"
                    if config.get("protected")
                    else ""
                ),
                "notes": templates.read_prose(
                    run, "brief-notes", "（計画ステージがまだ書いていない）"
                ),
            },
        ),
    )


def prepare_inputs(run: paths.Run, st: dict[str, Any], config: dict[str, Any]) -> None:
    write_brief(run, st, config)
    if not os.path.exists(run.map):
        files.write_text(run.map, templates.template("map"))
