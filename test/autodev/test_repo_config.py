"""リポジトリごとの設定（`infra/repo_config.py`・LEDGER FP-09）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from autodevlib.domain.values import DEFAULT_TEST_GLOBS, GlobPattern, Repository, VerifyCommand
from autodevlib.infra.repo_config import (
    RepoConfig,
    RepoConfigError,
    config_path,
    load_repo_config,
)

REPO = Repository("/home/me/src/github.com/o/r")


def env(tmp_path: Path) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path)}


def write(tmp_path: Path, body: object) -> Path:
    path = config_path(REPO, env(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def test_置き場はXDG_CONFIG_HOMEの下でスラッグはパスのスラッシュとコロンを替えたもの(
    tmp_path: Path,
):
    assert config_path(REPO, env(tmp_path)) == (
        tmp_path / "autodev" / "repos" / "home__me__src__github.com__o__r.json"
    )
    assert config_path(Repository("/c:/x"), env(tmp_path)).name == "c___x.json"
    # 相対パスの XDG_CONFIG_HOME は無効として既定に戻す
    assert config_path(REPO, {"XDG_CONFIG_HOME": "rel"}) == (
        Path.home() / ".config" / "autodev" / "repos" / "home__me__src__github.com__o__r.json"
    )


def test_ファイルが無ければ既定で読み読んだファイルは無いと残す(tmp_path: Path):
    assert load_repo_config(REPO, env(tmp_path)) == RepoConfig()
    assert RepoConfig().test_globs == DEFAULT_TEST_GLOBS


def test_4つの欄を読み省いた欄は既定にしtestGlobsは既定を置き換える(tmp_path: Path):
    path = write(tmp_path, {"verify": ["uv run pytest -q"], "testGlobs": ["spec/**"]})
    config = load_repo_config(REPO, env(tmp_path))
    assert config == RepoConfig(
        verify=(VerifyCommand("uv run pytest -q"),),
        test_globs=(GlobPattern("spec/**"),),
        source=path,
    )
    write(tmp_path, {"protected": ["uv.lock"], "untested": ["docs/**"]})
    assert load_repo_config(REPO, env(tmp_path)).executor_arguments() == {
        "verify": (),
        "test_globs": DEFAULT_TEST_GLOBS,
        "protected_globs": (GlobPattern("uv.lock"),),
        "untested_globs": (GlobPattern("docs/**"),),
    }


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("{", "JSON として読めない"),
        ([], "JSON の object でない"),
        ({"protectedGlobs": []}, "知らない欄がある: protectedGlobs"),
        ({"verify": "uv run pytest"}, "verify が文字列の配列でない"),
        ({"testGlobs": [1]}, "testGlobs が文字列の配列でない"),
        ({"untested": [" "]}, "untested に使えない値がある"),
    ],
)
def test_崩れた設定は既定に戻さずパスと直し方を添えて拒む(
    tmp_path: Path, body: object, reason: str
):
    path = write(tmp_path, body)
    with pytest.raises(RepoConfigError) as error:
        load_repo_config(REPO, env(tmp_path))
    message = str(error.value)
    assert str(path) in message
    assert reason in message
    assert "verify, testGlobs, protected, untested" in message
