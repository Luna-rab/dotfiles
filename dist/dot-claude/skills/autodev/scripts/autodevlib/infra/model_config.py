"""クラスごとのモデルと effort の設定。利用者全体で 1 つ。

置き場は `$XDG_CONFIG_HOME/autodev/models.json`（既定 `~/.config`）。

```json
{"lead": {"model": "opus", "effort": "high"}, "implement": {"effort": "xhigh"}}
```

- どのクラス・欄も省ける。書いたクラスの書いた欄だけが `ModelClasses.default()` を上書きする
- 読めない・形が違う・知らないクラスや欄がある・値が不正なときは、黙って既定に戻さずに
  `ModelConfigError` にする（`repo_config.RepoConfigError` と同じ方針）
- 新しいランを始めるときに 1 回だけ読み、ランに記録する。走っているランには効かない
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..domain.value_objects.base import InvalidValue
from ..domain.value_objects.model_class import Effort, ModelClass, ModelClasses, ModelName

FIELDS = ("model", "effort")


class ModelConfigError(ValueError):
    """設定のファイルが崩れている。文面に、ファイルのパスと直し方を入れる。"""


def models_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    # XDG の仕様では、相対パスの XDG_CONFIG_HOME は無効として既定に戻す
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".config"
    return base / "autodev" / "models.json"


def load_model_classes(env: Mapping[str, str] | None = None) -> ModelClasses:
    path = models_path(env)
    return _apply(path, _read(path) or {})


def set_model_class(
    cls: ModelClass,
    *,
    model: ModelName | None = None,
    effort: Effort | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelClasses:
    path = models_path(env)
    written = _read(path) or {}
    # 今のファイルが崩れていたら、書く前に止める
    _apply(path, written)
    entry = dict(written.get(cls.value, {}))
    if model is not None:
        entry["model"] = model.value
    if effort is not None:
        entry["effort"] = effort.value
    written[cls.value] = entry
    merged = _apply(path, written)
    _write(path, json.dumps(written, ensure_ascii=False, indent=2) + "\n")
    return merged


def _write(path: Path, text: str) -> None:
    """一時ファイルに書いてから差し替える。途中で落ちても、元の中身か新しい中身のどちらかが残る。"""
    # シンボリックリンク（dotfiles に置いた models.json など）は、リンクを残してリンク先を差し替える
    target = path.resolve()
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ModelConfigError(
            f"モデルの設定 {path} に書けない（{error}）。"
            f"{target.parent} に書き込めるようにしてから、もう一度流す"
        ) from error


def _read(path: Path) -> dict[str, Any] | None:
    """書いてある object。ファイルが無ければ None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ModelConfigError(_broken(path, f"読めない（{error}）")) from error
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelConfigError(_broken(path, f"JSON として読めない（{error}）")) from error
    if not isinstance(loaded, dict):
        raise ModelConfigError(_broken(path, "JSON の object でない"))
    return loaded


def _apply(path: Path, written: Mapping[str, Any]) -> ModelClasses:
    names = {cls.value for cls in ModelClass}
    if unknown := sorted(set(written) - names):
        raise ModelConfigError(_broken(path, f"知らないクラスがある: {', '.join(unknown)}"))
    merged = ModelClasses.default()
    for cls in ModelClass:
        if cls.value not in written:
            continue
        entry = written[cls.value]
        if not isinstance(entry, dict):
            raise ModelConfigError(_broken(path, f"{cls.value} が JSON の object でない"))
        if unknown := sorted(set(entry) - set(FIELDS)):
            raise ModelConfigError(
                _broken(path, f"{cls.value} に知らない欄がある: {', '.join(unknown)}")
            )
        merged = merged.with_choice(
            cls, model=_model(path, cls, entry), effort=_effort(path, cls, entry)
        )
    return merged


def _model(path: Path, cls: ModelClass, entry: Mapping[str, Any]) -> ModelName | None:
    if "model" not in entry:
        return None
    value = entry["model"]
    if not isinstance(value, str):
        raise ModelConfigError(_broken(path, f"{cls.value}.model が文字列でない"))
    try:
        return ModelName(value)
    except InvalidValue as error:
        raise ModelConfigError(_broken(path, f"{cls.value}.model が使えない（{error}）")) from error


def _effort(path: Path, cls: ModelClass, entry: Mapping[str, Any]) -> Effort | None:
    if "effort" not in entry:
        return None
    try:
        return Effort(entry["effort"])
    except ValueError as error:
        raise ModelConfigError(
            _broken(path, f"{cls.value}.effort が {entry['effort']!r} で、使える値でない")
        ) from error


def _broken(path: Path, reason: str) -> str:
    return (
        f"モデルの設定 {path} が {reason}。"
        f"クラスは {', '.join(cls.value for cls in ModelClass)}、欄は model（空でない文字列）と "
        f"effort（{', '.join(effort.value for effort in Effort)}）だけにして直すか、"
        "ファイルを消して既定で走らせる"
    )
