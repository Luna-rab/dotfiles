"""テストのパスの判定（`core/globs.py`）。

**検査⑤とフック（`hooks/deny-writes.py`）が同じ判定を使う。** ここがずれると、フックが
通した書き込みを検査が落とす（または逆に、実装段がテストを書き換えたまま通る）。
"""

from __future__ import annotations

import pytest
from autodevlib.core import globs

DEFAULTS = list(globs.DEFAULT_TEST_GLOBS)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_a.py",
        "src/pkg/test_a.py",
        "src/pkg/a_test.py",
        "a/b/tests/helper.py",
        "internal/a_test.go",
        "web/src/a.test.ts",
        "web/src/a.test.tsx",
        "web/src/a.spec.ts",
        "web/src/a.spec.tsx",
        "java/FooTest.java",
        "ruby/foo_spec.rb",
        "spec/foo.rb",
    ],
)
def test_既定の指定でテストと見なす(path: str):
    assert globs.matches_any(path, DEFAULTS)


@pytest.mark.parametrize(
    "path",
    [
        "src/pkg/a.py",
        "src/testing.py",
        "docs/tests.md",
        "src/attest.py",
    ],
)
def test_既定の指定でテストと見なさない(path: str):
    assert not globs.matches_any(path, DEFAULTS)


def test_末尾がスラッシュの指定はディレクトリで当てる():
    assert globs.matches("tests/a.py", "tests/")
    assert globs.matches("a/b/tests/c.py", "tests/")
    assert globs.matches("tests", "tests/")
    assert not globs.matches("testsuite/a.py", "tests/")


def test_リポジトリ直下のtestsも当てる():
    """`**/tests/**` の `**/` は 0 段にも当たる。

    `fnmatch` だけでは `**/tests/**` が「`tests` の前に 1 段以上ある」を要求するので、
    直下の `tests/a.py` が漏れる。漏れると実装段がそこを書き換えても誰も止めない。
    """
    assert globs.matches("tests/a.py", "**/tests/**")
    assert globs.matches("a/b/tests/c.py", "**/tests/**")


def test_末尾からの照合で当てる():
    """`fnmatch` は先頭を固定するので、この形は `PurePath.match` しか当てない。"""
    assert globs.matches("a/b/tests/c.py", "tests/*.py")


def test_根から掘る指定で当てる():
    """`PurePath.match` は末尾から見るので、この形は `fnmatch` しか当てない。"""
    assert globs.matches("spec/a/b.rb", "spec/**")


def test_Windows風の区切りを正規化する():
    """フックが受け取るパスは claude が渡すもので、区切りが `\\` のことがある。"""
    assert globs.normalize("a\\b\\test_c.py") == "a/b/test_c.py"
    assert globs.matches_any("tests\\test_a.py", DEFAULTS)


def test_先頭のドットスラッシュを落とす():
    assert globs.normalize("./a/b.py") == "a/b.py"
    assert globs.matches_any("./tests/test_a.py", DEFAULTS)


def test_当たったものだけを取り出す():
    changed = ["src/a.py", "tests/test_a.py", "README.md", "src/b_test.py"]
    assert globs.pick(changed, DEFAULTS) == ["tests/test_a.py", "src/b_test.py"]
    assert globs.pick(changed, ["**/*.md"]) == ["README.md"]


def test_当たらなければ空を返す():
    assert globs.pick(["src/a.py"], DEFAULTS) == []


def test_環境変数は改行区切りで読む():
    """glob に空白が入りうるのでコロンでは区切らない。"""
    assert globs.parse_env("tests/**\n src/*_test.py \n") == ["tests/**", "src/*_test.py"]
    assert globs.parse_env("a b/*.py\ntests/**") == ["a b/*.py", "tests/**"]


def test_環境変数が空なら既定に戻す():
    """指定を渡し忘れた段でテストを無防備にしない。"""
    assert globs.parse_env(None) == DEFAULTS
    assert globs.parse_env("") == DEFAULTS
    assert globs.parse_env("\n  \n") == DEFAULTS
