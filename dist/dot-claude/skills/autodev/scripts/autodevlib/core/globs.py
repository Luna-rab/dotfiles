"""テストのパスを判定する 1 か所。

**driver の完了チェック（テストファイルの差分が空）とフック（テストへの書き込みを止める）が
同じ判定を使う。** 別々に書くと、フックが通したものを完了チェックが落とす（または逆）ずれが出る。

`**` を含む glob を素直に扱いたいので `fnmatch` だけに頼らず、次の 3 通りで見る。

1. `PurePath.match`——末尾からの照合。`*_test.py` が `a/b/c_test.py` に当たる
2. `fnmatch`——`tests/**` のようにパス全体を見る形
3. ディレクトリ名での照合——`tests/` のように末尾が `/` の指定
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

DEFAULT_TEST_GLOBS: tuple[str, ...] = (
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
    "**/*_test.go",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/*Test.java",
    "**/*_spec.rb",
    "spec/**",
)


def normalize(path: str) -> str:
    """先頭の `./` を落として POSIX の区切りに寄せる。"""
    return str(PurePosixPath(path.replace("\\", "/").removeprefix("./")))


def matches(path: str, pattern: str) -> bool:
    target = normalize(path)
    if pattern.endswith("/"):
        head = pattern.rstrip("/")
        return target == head or target.startswith(f"{head}/") or f"/{head}/" in f"/{target}"
    if fnmatch(target, pattern):
        return True
    try:
        if PurePosixPath(target).match(pattern):
            return True
    except ValueError:
        pass
    # `**/tests/**` のような指定で、ディレクトリ自身（`tests`）も当てたい
    return pattern.startswith("**/") and fnmatch(target, pattern[3:])


def matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(matches(path, p) for p in patterns)


def pick(paths: list[str], patterns: list[str] | tuple[str, ...]) -> list[str]:
    return [p for p in paths if matches_any(p, patterns)]


def parse_env(value: str | None) -> list[str]:
    """`AUTODEV_TEST_GLOBS` は改行区切りで渡す（glob に空白が入りうるのでコロンは使わない）。"""
    if not value:
        return list(DEFAULT_TEST_GLOBS)
    out = [line.strip() for line in value.splitlines() if line.strip()]
    return out or list(DEFAULT_TEST_GLOBS)
