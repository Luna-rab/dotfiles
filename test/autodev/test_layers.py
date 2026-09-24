"""層の規則を機械で守る。

autodev は `core`（純粋）・`ports`（外との境目）・`config`（置き場と表）・`app`（流れ）に
分かれている。**この分かれ方は import の向きだけで保たれている。** `core` が 1 行
`subprocess` を import すると、`core/verdict.py` の 6 検査を git とコマンド無しでは
試せなくなる。そのとき他の検査は全部通るので、試せなくなったことは誰にも見えない。

依存は一方通行である。

    cli → app → (core, ports, config)

`core` から外へ出る矢印は無い。`core` 同士の import は許す。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from conftest import SCRIPTS_ROOT, SKILL_ROOT

PACKAGE = SCRIPTS_ROOT / "autodevlib"
#: パッケージの名前。import の向きを判定する起点
ROOT = "autodevlib"
#: パッケージの外にある入口とフック。層に属さないが、第三者パッケージを持ち込めば
#: `python3` 単体では動かなくなる。**フックは claude の子プロセスとして別に起動され、
#: import に失敗しても driver は落ちない**ので、ここで見る
SCRIPTS = sorted([*SCRIPTS_ROOT.glob("*.py"), *(SKILL_ROOT / "hooks").glob("*.py")])

#: 層ごとに import してはいけない層。同じ層の中（`core` 同士など）は許す。
#: **層を増やしたらここも増やす。** 表に無い層は `.get` の既定値で何も禁じられない
FORBIDDEN_LAYERS: dict[str, tuple[str, ...]] = {
    # `autodevlib/__init__.py` 自身。何も import しない
    "": ("core", "ports", "config", "app", "cli"),
    "core": ("ports", "config", "app", "cli"),
    "ports": ("app", "cli"),
    # 置き場と段の表は読まれる側で、他の層を読まない
    "config": ("core", "ports", "app", "cli"),
    "app": ("cli",),
    # 入口は全部の層を呼べる。引数を読んで app へ渡し、`cmd_status` が core の
    # `outcome_of` と `counts` で状態を出す
    "cli": (),
}

#: `core` に持ち込んではいけないモジュール。持ち込むと `core` から外を叩ける
BANNED_IMPORTS = frozenset({"subprocess", "glob", "shutil", "tempfile", "io", "socket", "sqlite3"})

#: `core` で呼んではいけない `os` の操作。**`os` か `os.path` で修飾されたもの**と
#: 組み込みの `open()` だけを見る。修飾を見ずに名前だけで弾くと、`core/globs.py` の
#: `path.replace("\\", "/")` を `os.replace` と間違える
OS_CALLS = frozenset(
    {
        "system",
        "popen",
        "exists",
        "isdir",
        "isfile",
        "islink",
        "listdir",
        "scandir",
        "walk",
        "stat",
        "lstat",
        "getsize",
        "makedirs",
        "mkdir",
        "rmdir",
        "remove",
        "unlink",
        "rename",
        "replace",
        "chmod",
        "chdir",
        "getcwd",
        "symlink",
        "readlink",
        "open",
    }
)


def _collect() -> tuple[dict[str, Path], set[str]]:
    """`autodevlib` の全モジュールと、そのうちパッケージであるもの。"""
    found: dict[str, Path] = {}
    packages: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        name = ".".join([ROOT, *parts])
        found[name] = path
        if is_package:
            packages.add(name)
    return found, packages


MODULES, PACKAGES = _collect()
CORE = sorted(m for m in MODULES if m.startswith(f"{ROOT}.core.") or m == f"{ROOT}.core")


def layer_of(module: str) -> str:
    """そのモジュールが属する層。`autodevlib/cli.py` のような直下は `cli` になる。"""
    parts = module.split(".")
    return parts[1] if len(parts) >= 2 else ""


def tree(module: str) -> ast.Module:
    return ast.parse(source(module), filename=str(MODULES[module]))


def source(module: str) -> str:
    return MODULES[module].read_text(encoding="utf-8")


def imported(module: str, node: ast.Import | ast.ImportFrom) -> list[str]:
    """その import 文が指すモジュールの絶対名。相対 import も解く。

    `from X import Y` は絶対形でも相対形でも `Y` まで並べる。`Y` がモジュールを指すことが
    あり、捨てると `from autodevlib import ports` が `autodevlib` だけになって向きの判定に
    載らない。
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        package = module if module in PACKAGES else module.rsplit(".", 1)[0]
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - (node.level - 1)])
        head = f"{base}.{node.module}" if node.module else base
    elif node.module:
        head = node.module
    else:
        return []
    return [head, *(f"{head}.{alias.name}" for alias in node.names)]


def imports_in(module: str, code: str) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            out += imported(module, node)
    return out


def imports_of(module: str) -> list[str]:
    return imports_in(module, source(module))


def internal_deps(module: str) -> set[str]:
    """同じパッケージの中で依存しているモジュール。"""
    found: set[str] = set()
    for target in imports_of(module):
        if target == module or not target.startswith(f"{ROOT}."):
            continue
        if target in MODULES:
            found.add(target)
        elif target.rsplit(".", 1)[0] in MODULES:
            # `from ..core.verdict import Evidence` の `Evidence` の側
            found.add(target.rsplit(".", 1)[0])
    found.discard(module)
    return found


def dotted(node: ast.expr) -> str | None:
    """`os.path.exists` のような参照を文字列にする。添字や呼び出しが挟まれば None。"""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def outside_calls(code: str) -> list[str]:
    """外を触る呼び出しを挙げる。

    `os` / `os.path` で修飾されたものと組み込みの `open()` を見る。別名で import した
    もの（`import os as o`、`from os import path`）は修飾された形に戻して見る。
    """
    node = ast.parse(code)
    aliases: dict[str, str] = {}
    for item in ast.walk(node):
        if isinstance(item, ast.Import):
            for alias in item.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(item, ast.ImportFrom) and item.module in ("os", "os.path"):
            for alias in item.names:
                aliases[alias.asname or alias.name] = f"{item.module}.{alias.name}"
    found: list[str] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        name = dotted(item.func)
        if name is None:
            continue
        # 引き当ては先頭の要素で行う。`from os import path` の `path.exists` は
        # `aliases` に完全一致では載らない
        head, _, rest = name.partition(".")
        if head in aliases:
            name = f"{aliases[head]}.{rest}" if rest else aliases[head]
        qualified = name.startswith(("os.", "os.path.")) and name.rsplit(".", 1)[1] in OS_CALLS
        if name == "open" or qualified:
            found.append(name)
    return found


def test_coreのモジュールを6つ以上集める():
    """`core` が 1 つも見つからないなら、この検査は何も守っていない。"""
    assert len(CORE) >= 6, CORE
    assert f"{ROOT}.core.verdict" in MODULES
    assert f"{ROOT}.ports.evidence" in MODULES


def test_appとcliのモジュールを集める():
    """走査から漏れた層は 1 件も検査されないのに、全部緑で返る。

    ディレクトリの名前を打ち間違えたり `app/` を動かしたりすると、向きの検査が
    黙って消える。
    """
    assert f"{ROOT}.cli" in MODULES
    app = [m for m in MODULES if m.startswith(f"{ROOT}.app.")]
    assert len(app) >= 9, app
    assert f"{ROOT}.app.drive" in MODULES


# --- core は外を触らない -----------------------------------------------------


@pytest.mark.parametrize("module", CORE)
def test_coreは外を叩くモジュールを持ち込まない(module: str):
    """`core` がこれらを持つと、6 検査を git とコマンド無しで試せなくなる。"""
    roots = {target.split(".")[0] for target in imports_of(module)}
    bad = roots & BANNED_IMPORTS
    assert bad == set(), f"{module} が {', '.join(sorted(bad))} を import した"


@pytest.mark.parametrize("module", CORE)
def test_coreは外を叩く呼び出しをしない(module: str):
    """`os.path.join` のような文字列としてのパス計算は許す。実物を触る呼び出しを弾く。"""
    found = outside_calls(source(module))
    assert found == [], f"{module} が {', '.join(found)} を呼んだ"


@pytest.mark.parametrize("module", CORE)
def test_coreはPathを持ち込まない(module: str):
    """`PurePath` 系は文字列の計算だけだが、`Path` はファイルシステムを触れる。

    別名で持ち込むと `pathlib.Path` の呼び出しが `pl.Path` になって判定に載らないので、
    `import pathlib as pl` 自体を弾く。
    """
    for node in ast.walk(tree(module)):
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            bad = [a.name for a in node.names if not a.name.startswith("Pure")]
            assert bad == [], f"{module} が pathlib.{', '.join(bad)} を import した"
        if isinstance(node, ast.Import):
            renamed = [a.asname for a in node.names if a.name == "pathlib" and a.asname]
            assert renamed == [], f"{module} が pathlib を {', '.join(renamed)} として import した"
        if isinstance(node, ast.Call) and dotted(node.func) == "pathlib.Path":
            pytest.fail(f"{module} が pathlib.Path を呼んだ")


# --- import の向き -----------------------------------------------------------


@pytest.mark.parametrize("module", sorted(MODULES))
def test_層が表に載っている(module: str):
    """表に無い層は `.get` の既定値で何も禁じられない。層を増やしたら表も増やす。"""
    assert layer_of(module) in FORBIDDEN_LAYERS, f"{module} の層が表に無い"


@pytest.mark.parametrize("module", sorted(MODULES))
def test_層をまたぐimportの向きを守る(module: str):
    forbidden = FORBIDDEN_LAYERS.get(layer_of(module), ())
    for target in sorted(internal_deps(module)):
        assert layer_of(target) not in forbidden, f"{module} が {target} を import した"


def test_第三者パッケージを持ち込まない():
    """`python3` 単体で動くこと。標準ライブラリと `autodevlib` だけを import する。

    入口（`autodev.py`）とフックも見る。層に属さないので他の検査を 1 つも踏まない。
    """
    pairs = [(module, target) for module in sorted(MODULES) for target in imports_of(module)]
    pairs += [
        (script.name, target)
        for script in SCRIPTS
        for target in imports_in(script.stem, script.read_text(encoding="utf-8"))
    ]
    outside = [
        f"{where}: {target}"
        for where, target in pairs
        if target.split(".")[0] not in sys.stdlib_module_names and target.split(".")[0] != ROOT
    ]
    assert outside == []


def test_依存に循環が無い():
    """循環すると import の順で落ちる。落ち方が環境で変わるので、ここで止める。"""
    done: set[str] = set()
    stack: list[str] = []

    def walk(module: str) -> None:
        if module in stack:
            pytest.fail("循環している: " + " → ".join([*stack[stack.index(module) :], module]))
        if module in done:
            return
        stack.append(module)
        for target in sorted(internal_deps(module)):
            walk(target)
        stack.pop()
        done.add(module)

    for module in sorted(MODULES):
        walk(module)


# --- 検出そのものの検査 ------------------------------------------------------
# 検出が甘いと規則を破ったコードが通り、厳しすぎると正しいコードが書けなくなる。


def test_文字列のreplaceを誤って弾かない():
    r"""`core/globs.py` の `path.replace("\\", "/")` は `os.replace` ではない。"""
    assert outside_calls(r'path.replace("\\", "/")') == []
    assert outside_calls("value.strip().replace(a, b)") == []
    assert outside_calls("data.get(key).open()") == []


def test_修飾された操作を弾く():
    assert outside_calls("import os\nos.replace(a, b)") == ["os.replace"]
    assert outside_calls("import os\nos.path.exists(p)") == ["os.path.exists"]
    assert outside_calls("import os\nos.listdir(p)") == ["os.listdir"]
    assert outside_calls("import os\nos.makedirs(p, exist_ok=True)") == ["os.makedirs"]


def test_osからのコマンド起動を弾く():
    """`os.system` と `os.popen` は `subprocess` を import せずにコマンドを流せる。"""
    assert outside_calls("import os\nos.system('git log')") == ["os.system"]
    assert outside_calls("import os\nos.popen('git log').read()") == ["os.popen"]


def test_組み込みのopenを弾く():
    assert outside_calls('open("a")') == ["open"]
    assert outside_calls('with open("a") as fh:\n    pass') == ["open"]


def test_名前でimportした操作も弾く():
    assert outside_calls("from os.path import exists\nexists(p)") == ["os.path.exists"]
    assert outside_calls("from os import makedirs as md\nmd(p)") == ["os.makedirs"]


def test_別名でimportしたosも弾く():
    assert outside_calls("import os as o\no.listdir(p)") == ["os.listdir"]
    assert outside_calls("from os import path\npath.exists(p)") == ["os.path.exists"]


def test_パスの文字列計算は許す():
    assert outside_calls('import os\nos.path.join(a, "b")') == []
    assert outside_calls("import os\nos.path.dirname(a)") == []
    assert outside_calls("import os\nos.environ.get(name)") == []


def test_相対importを絶対名に解く():
    """向きの判定はここに乗る。1 段のずれで規則違反を見逃す。"""
    node = ast.parse("from ..core.verdict import Evidence").body[0]
    assert isinstance(node, ast.ImportFrom)
    got = imported(f"{ROOT}.ports.evidence", node)
    assert f"{ROOT}.core.verdict" in got

    node = ast.parse("from . import globs, review_policy").body[0]
    assert isinstance(node, ast.ImportFrom)
    got = imported(f"{ROOT}.core.verdict", node)
    assert f"{ROOT}.core.globs" in got
    assert f"{ROOT}.core.review_policy" in got


def test_絶対importでも名前を並べる():
    """`from autodevlib import ports` の `ports` を捨てると、層が `autodevlib` になる。"""
    node = ast.parse("from autodevlib import ports").body[0]
    assert isinstance(node, ast.ImportFrom)
    assert f"{ROOT}.ports" in imported(f"{ROOT}.core.globs", node)
