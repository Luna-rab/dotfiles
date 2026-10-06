"""クラスごとのモデルと effort の設定（`infra/model_config.py`）と `autodev config show` / `set`。

設定ファイルの置き場は XDG_CONFIG_HOME を tmp_path に向けて決め、利用者の ~/.config を読み書きしない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from autodevlib import cli
from autodevlib.domain.value_objects.model_class import (
    Effort,
    ModelChoice,
    ModelClass,
    ModelClasses,
    ModelName,
)
from autodevlib.infra.model_config import ModelConfigError, load_model_classes, models_path

DEFAULT_JSON = {
    "lead": {"model": "opus", "effort": "high"},
    "review": {"model": "opus", "effort": "medium"},
    "implement": {"model": "sonnet", "effort": "medium"},
    "write": {"model": "sonnet", "effort": "medium"},
}


def env(tmp_path: Path) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path)}


def file_of(tmp_path: Path) -> Path:
    return tmp_path / "autodev" / "models.json"


def write(tmp_path: Path, body: object) -> Path:
    path = file_of(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


# --- load_model_classes ---


def test_ファイルが無ければ4つのクラスは既定のモデルとeffort(tmp_path: Path):
    loaded = load_model_classes(env(tmp_path))
    assert loaded.of(ModelClass.LEAD) == ModelChoice(ModelName("opus"), Effort.HIGH)
    assert loaded.of(ModelClass.REVIEW) == ModelChoice(ModelName("opus"), Effort.MEDIUM)
    assert loaded.of(ModelClass.IMPLEMENT) == ModelChoice(ModelName("sonnet"), Effort.MEDIUM)
    assert loaded.of(ModelClass.WRITE) == ModelChoice(ModelName("sonnet"), Effort.MEDIUM)
    assert not file_of(tmp_path).exists()


def test_書いたクラスの書いた欄だけが既定を上書きする(tmp_path: Path):
    write(tmp_path, {"implement": {"effort": "high"}})
    loaded = load_model_classes(env(tmp_path))
    assert loaded.of(ModelClass.IMPLEMENT) == ModelChoice(ModelName("sonnet"), Effort.HIGH)
    default = ModelClasses.default()
    for cls in (ModelClass.LEAD, ModelClass.REVIEW, ModelClass.WRITE):
        assert loaded.of(cls) == default.of(cls)


def test_modelとeffortの両方を書いたクラスは両方替わり別のクラスと組み合わせられる(tmp_path: Path):
    write(
        tmp_path,
        {"lead": {"model": "claude-opus-5-5", "effort": "max"}, "write": {"model": "haiku"}},
    )
    loaded = load_model_classes(env(tmp_path))
    assert loaded == (
        ModelClasses.default()
        .with_choice(ModelClass.LEAD, model=ModelName("claude-opus-5-5"), effort=Effort.MAX)
        .with_choice(ModelClass.WRITE, model=ModelName("haiku"))
    )


def test_置き場はXDG_CONFIG_HOMEの下で空か相対パスなら既定の_config():
    assert models_path({"XDG_CONFIG_HOME": "/tmp/x"}) == Path("/tmp/x/autodev/models.json")
    home = Path.home() / ".config" / "autodev" / "models.json"
    assert models_path({"XDG_CONFIG_HOME": ""}) == home
    assert models_path({}) == home
    assert models_path({"XDG_CONFIG_HOME": "rel/dir"}) == home


def test_XDG_CONFIG_HOMEの下のファイルを読む(tmp_path: Path):
    write(tmp_path, {"review": {"model": "haiku"}})
    assert models_path(env(tmp_path)) == file_of(tmp_path)
    loaded = load_model_classes(env(tmp_path))
    assert loaded.of(ModelClass.REVIEW) == ModelChoice(ModelName("haiku"), Effort.MEDIUM)


@pytest.mark.parametrize(
    "body",
    [
        "{",
        [],
        {"ultra": {}},
        {"lead": {"temperature": 1}},
        {"lead": {"effort": "huge"}},
        {"lead": {"model": ""}},
        {"lead": {"model": 1}},
        {"lead": "opus"},
    ],
    ids=[
        "壊れたJSON",
        "最上位が配列",
        "知らないクラス",
        "知らない欄",
        "不正なeffort",
        "空のmodel",
        "文字列でないmodel",
        "クラスの値がobjectでない",
    ],
)
def test_崩れた設定は既定に戻さずパスを添えたModelConfigErrorにする(tmp_path: Path, body: object):
    path = write(tmp_path, body)
    with pytest.raises(ModelConfigError) as caught:
        load_model_classes(env(tmp_path))
    assert str(path) in str(caught.value)


def test_読めない設定はパスを添えたModelConfigErrorにする(tmp_path: Path):
    # ディレクトリはファイルとして読めない
    path = file_of(tmp_path)
    path.mkdir(parents=True)
    with pytest.raises(ModelConfigError) as caught:
        load_model_classes(env(tmp_path))
    assert str(path) in str(caught.value)


# --- config show / set ---


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    capsys.readouterr()
    try:
        code = cli.main(list(args))
    except SystemExit as exit:
        # argparse の引数の誤り（`_Parser` が 1 で抜ける）
        code = exit.code
    out, err = capsys.readouterr()
    return int(code or 0), out, err


def test_config_showはファイルが無ければ既定の4クラスとパスとexistsのfalseを出す(
    config_home: Path, capsys: pytest.CaptureFixture[str]
):
    code, out, err = run_cli(capsys, "config", "show")
    assert code == 0, err
    assert json.loads(out) == {
        "path": str(file_of(config_home)),
        "exists": False,
        "classes": DEFAULT_JSON,
    }
    assert not file_of(config_home).exists()


def test_config_showは上書きしたクラスを上書き後の値で出す(
    config_home: Path, capsys: pytest.CaptureFixture[str]
):
    write(config_home, {"implement": {"model": "claude-opus-5-5", "effort": "xhigh"}})
    code, out, err = run_cli(capsys, "config", "show")
    assert code == 0, err
    shown = json.loads(out)
    assert shown["exists"] is True
    assert shown["path"] == str(file_of(config_home))
    assert shown["classes"] == {
        **DEFAULT_JSON,
        "implement": {"model": "claude-opus-5-5", "effort": "xhigh"},
    }


def test_config_showは崩れたファイルなら1で止まる(
    config_home: Path, capsys: pytest.CaptureFixture[str]
):
    path = write(config_home, "{")
    code, out, err = run_cli(capsys, "config", "show")
    assert code == 1
    assert out == ""
    assert str(path) in err


def test_config_setはファイルを作って書き続けて別の欄を足しても前に書いた欄を消さない(
    config_home: Path, capsys: pytest.CaptureFixture[str]
):
    path = file_of(config_home)
    code, out, err = run_cli(capsys, "config", "set", "--class", "write", "--model", "haiku")
    assert code == 0, err
    assert json.loads(path.read_text("utf-8")) == {"write": {"model": "haiku"}}
    shown = json.loads(out)
    assert shown["exists"] is True
    assert shown["classes"]["write"] == {"model": "haiku", "effort": "medium"}

    code, out, err = run_cli(capsys, "config", "set", "--class", "write", "--effort", "low")
    assert code == 0, err
    assert json.loads(path.read_text("utf-8")) == {"write": {"model": "haiku", "effort": "low"}}
    assert json.loads(out)["classes"]["write"] == {"model": "haiku", "effort": "low"}

    # ほかのクラスに書いた欄も残る
    code, _, err = run_cli(capsys, "config", "set", "--class", "lead", "--effort", "max")
    assert code == 0, err
    assert json.loads(path.read_text("utf-8")) == {
        "write": {"model": "haiku", "effort": "low"},
        "lead": {"effort": "max"},
    }
    code, out, _ = run_cli(capsys, "config", "show")
    assert json.loads(out)["classes"] == {
        **DEFAULT_JSON,
        "lead": {"model": "opus", "effort": "max"},
        "write": {"model": "haiku", "effort": "low"},
    }


def test_config_setは人が書いた別のクラスの欄を残す(
    config_home: Path, capsys: pytest.CaptureFixture[str]
):
    path = write(config_home, {"implement": {"model": "claude-opus-5-5", "effort": "xhigh"}})
    code, _, err = run_cli(capsys, "config", "set", "--class", "review", "--model", "haiku")
    assert code == 0, err
    assert json.loads(path.read_text("utf-8")) == {
        "implement": {"model": "claude-opus-5-5", "effort": "xhigh"},
        "review": {"model": "haiku"},
    }


BAD_SETS = [
    ("config", "set", "--class", "write"),
    ("config", "set", "--class", "ultra", "--model", "haiku"),
    ("config", "set", "--class", "write", "--effort", "huge"),
    ("config", "set", "--class", "write", "--model", ""),
    ("config", "set", "--class", "write", "--model", "   "),
]
BAD_SET_IDS = ["欄が無い", "知らないクラス", "不正なeffort", "空のmodel", "空白のmodel"]


@pytest.mark.parametrize("args", BAD_SETS, ids=BAD_SET_IDS)
def test_config_setは不正な引数ならファイルが無いときは作らずに1で止まる(
    config_home: Path, capsys: pytest.CaptureFixture[str], args: tuple[str, ...]
):
    code, _, _ = run_cli(capsys, *args)
    assert code == 1
    assert not file_of(config_home).exists()
    assert not (config_home / "autodev").exists()


@pytest.mark.parametrize("args", BAD_SETS, ids=BAD_SET_IDS)
def test_config_setは不正な引数ならあるファイルの中身を変えずに1で止まる(
    config_home: Path, capsys: pytest.CaptureFixture[str], args: tuple[str, ...]
):
    path = write(config_home, {"write": {"model": "sonnet", "effort": "high"}})
    before = path.read_bytes()
    code, _, _ = run_cli(capsys, *args)
    assert code == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "body",
    ["{", [], {"ultra": {}}, {"lead": {"effort": "huge"}}],
    ids=["壊れたJSON", "配列", "知らないクラス", "不正なeffort"],
)
def test_config_setは今のファイルが崩れていたら書かずにパスを出して1で止まる(
    config_home: Path, capsys: pytest.CaptureFixture[str], body: object
):
    path = write(config_home, body)
    before = path.read_bytes()
    code, _, err = run_cli(capsys, "config", "set", "--class", "write", "--model", "haiku")
    assert code == 1
    assert path.read_bytes() == before
    assert str(path) in err
