"""コマンドの実行。

標準ライブラリだけで動く（autodev は install.sh を通したどのマシンでも `python3` で動く）。
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class Run:
    """コマンド 1 回の結果。`code` が 0 以外でも例外にしない（検査が自分で読む）。"""

    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run(
    argv: list[str],
    cwd: str | None = None,
    env: dict[str, str | None] | None = None,
    stdin: str | None = None,
    timeout: int | None = None,
) -> Run:
    """コマンドを 1 回実行して結果を返す。

    **シェルを通さない。** 作業名がそのまま引数に入るので、
    シェルを通すと空白や引用符の混ざった値でコマンドが組み変わる。

    `env` の値に `None` を入れると、**その変数を子プロセスから外す**（`merged_env()`）。
    """
    merged = merged_env(env)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=merged,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,  # 終了コードは Run に載せて、検査が自分で読む
        )
    except FileNotFoundError:
        return Run(127, "", f"コマンドが見つからない: {argv[0]}")
    except subprocess.TimeoutExpired:
        return Run(124, "", f"制限時間を超えた（{timeout} 秒）: {' '.join(argv)}")
    return Run(proc.returncode, proc.stdout, proc.stderr)


def merged_env(env: dict[str, str | None] | None) -> dict[str, str]:
    """いまの環境に `env` を重ねる。**値が `None` の変数は外す。**

    空文字を入れる形では足りない——`ANTHROPIC_API_KEY=""` でも「設定されている」と
    読む相手がいる。
    """
    merged: dict[str, str] = dict(os.environ)
    for key, value in (env or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def shell_join(argv: list[str]) -> str:
    """ログと画面に出すためのコマンド表記。実行には使わない。"""
    return " ".join(shlex.quote(a) for a in argv)
