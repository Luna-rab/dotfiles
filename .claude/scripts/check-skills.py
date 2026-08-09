#!/usr/bin/env python3
"""`.claude/skills/` 配下のスキルが壊れていないかを検査する。

このリポジトリは Claude Code の skills（`.claude/skills/<スキル名>/SKILL.md` と、そこから
参照される補助ファイル）を dotfiles として持ち歩く。リンク切れや権限の落ちたスクリプトは、
実際にスキルを起動するまで気づけない。それを commit の時点で捕まえるために、次の 4 つを検査する。

1. frontmatter（`SKILL.md` の先頭にある `---` で挟まれた YAML）が YAML として読めること
2. `SKILL.md` が 500 行以下であること（基準は `.claude/rules/editing-skills.md`）
3. スキル内の `*.md` から張られた相対リンクの参照先が実在すること
4. `*.md` が参照する `scripts/` 配下のファイルが実在し、`.sh` に実行権限があること

使い方（リポジトリのルートから、引数なしで実行する）:

    python3 .claude/scripts/check-skills.py

問題が無ければ終了コード 0、1 件でも見つかれば 1 を返す。落ちた箇所は
`パス:行番号: 検査名: 説明` の形で 1 件 1 行ずつ標準出力に出す。

第 1 引数に別のディレクトリを渡すと、そこを `.claude/skills/` の代わりに検査する。
これは「わざと壊した入力で終了コード 1 が返ること」を、実物のスキルに触らずに確かめるために
ある。CI は引数なしで呼ぶので、既定値のままなら振る舞いは変わらない。
"""

import argparse
import re
import stat
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # PyYAML が無い環境で traceback を出さない
    sys.stderr.write(
        "check-skills: PyYAML が無い。`pip install PyYAML` などで入れてから実行する\n"
    )
    sys.exit(1)


# --- 定数 ---

# 既定の検査対象。カレントディレクトリからの相対で解決する（リポジトリのルートで実行する前提）。
# `__file__` からの相対にしない: install.sh の link_claude_config() が `.claude/scripts` を
# `~/.claude/scripts` へ symlink するので、`__file__` 基準だと symlink の実体側を見てしまう。
DEFAULT_SKILLS_ROOT = ".claude/skills"

# `.claude/rules/editing-skills.md` が定める SKILL.md の行数上限。
MAX_SKILL_MD_LINES = 500

# 検査名。CI や grep から使うので ASCII の固定文字列にする（説明文は日本語で変わりうる）。
CHECK_FRONTMATTER = "frontmatter"
CHECK_LINE_LIMIT = "line-limit"
CHECK_MD_LINK = "md-link"
CHECK_SCRIPT_REF = "script-ref"
CHECK_READ = "read"

# `[表示文字](リンク先)`。リンク先は空白と `)` の手前まで。`](x.md "title")` の title は落ちる。
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(?P<target>[^)\s]+)")

# `https:` や `mailto:` のようなスキーム付き。相対リンクではないので検査しない。
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# パス名に使う文字。ここを「使ってよい文字」の側で書くのは、`scripts/x.sh; echo ...` のような
# シェルの例（hooks.md の動作確認）で `;` まで取り込んで参照先を見失わないようにするため。
PATH_CHARS = r"A-Za-z0-9._/\-"

# `scripts/` 配下への参照。2 つの書き方を 1 本の正規表現で拾う。
#   1. skills ルートから書いた形（`~/.claude/skills/team-supervisor/scripts/gh-review.py` など）。
#      `<スキル名>` を捕まえて skills ルートから解決する。別スキルを指す参照も正しく解決できる。
#   2. スキルのディレクトリを起点にした形（`scripts/gh-review.py`）。
# 1 を先に書くのは、1 の中にある `scripts/` を 2 が二重に拾わないようにするため。
SCRIPT_REF_RE = re.compile(
    r"\.claude/skills/(?P<skill>[A-Za-z0-9._\-]+)/scripts/(?P<qualified>[{chars}]+)"
    r"|(?<![\w/~.\-])scripts/(?P<bare>[{chars}]+)".format(chars=PATH_CHARS)
)

# 参照の直後に付く句読点。パス名の一部ではないので落とす。
TRAILING_PUNCT = "。、，．；：！？."


class Failure:
    """検査に落ちた 1 件。パス・行番号・検査名・説明を持つ。"""

    def __init__(self, path, line, check, message):
        self.path = str(path)
        self.line = line
        self.check = check
        self.message = message

    def sort_key(self):
        return (self.path, self.line, self.check)

    def format(self):
        return "{}:{}: {}: {}".format(self.path, self.line, self.check, self.message)


class Counts:
    """検査した件数。0 件しか見ていないのに「問題なし」と出る状態を見分けるために数える。"""

    def __init__(self):
        self.skills = 0
        self.files = 0
        self.links_checked = 0
        self.links_skipped = 0
        self.script_refs = 0


# --- ファイルの読み込み ---


def read_text(path):
    """テキストを読む。読めなければ (None, Failure) を返す。

    UTF-8 以外や読み取り権限の無いファイルで例外を投げて止まると、残りのスキルが検査されない。
    1 件の失敗として記録して先へ進む。
    """
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except (OSError, UnicodeDecodeError) as err:
        return None, Failure(path, 1, CHECK_READ, "読めない: {}".format(err))


# --- 検査 1: frontmatter ---


def split_frontmatter(text):
    """先頭の frontmatter を取り出す。(YAML 本文, エラー説明) を返す。

    frontmatter は 1 行目の `---` で始まり、次に現れる `---` または `...` で終わる。
    1 行目が `---` でなければ frontmatter が無い。閉じが無ければ、本文全体を YAML として
    読ませない（400 行の markdown が YAML として何かに解釈されて通ってしまうのを避ける）。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, "frontmatter が無い（1 行目が `---` ではない）"
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            return "\n".join(lines[1:i]), None
    return None, "frontmatter が閉じていない（2 行目以降に `---` が無い）"


def check_frontmatter(skill_md, text):
    """SKILL.md の frontmatter が YAML の mapping として読めることを確かめる。"""
    source, error = split_frontmatter(text)
    if error is not None:
        return [Failure(skill_md, 1, CHECK_FRONTMATTER, error)]
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as err:
        detail = getattr(err, "problem", None) or str(err).replace("\n", " ")
        mark = getattr(err, "problem_mark", None)
        line = mark.line + 2 if mark is not None else 1  # +2: frontmatter は 2 行目から
        return [
            Failure(
                skill_md,
                line,
                CHECK_FRONTMATTER,
                "YAML として読めない: {}".format(detail),
            )
        ]
    if not isinstance(data, dict):
        return [
            Failure(
                skill_md,
                1,
                CHECK_FRONTMATTER,
                "frontmatter が mapping ではない（{}）".format(type(data).__name__),
            )
        ]
    return []


# --- 検査 2: 行数 ---


def check_line_limit(skill_md, text):
    """SKILL.md が 500 行以下であることを確かめる。"""
    count = len(text.splitlines())
    if count > MAX_SKILL_MD_LINES:
        return [
            Failure(
                skill_md,
                MAX_SKILL_MD_LINES + 1,
                CHECK_LINE_LIMIT,
                "{} 行（上限 {} 行）".format(count, MAX_SKILL_MD_LINES),
            )
        ]
    return []


# --- 検査 3: 相対リンク ---


def is_checkable_link(target):
    """そのリンクの実在を確かめるかどうかを返す。

    確かめないのは次の 4 つ。いずれもスキル内のファイルを指していない。
    - `https://...` のようなスキーム付き
    - `#見出し` だけのページ内リンク
    - `/` で始まる絶対パス（相対リンクではない）
    - `.md` で終わらない参照先（DoD が検査対象を `*.md` に限っている）
    """
    if SCHEME_RE.match(target) or target.startswith("#"):
        return False
    path_part = target.split("#", 1)[0]
    if not path_part or path_part.startswith("/"):
        return False
    return path_part.lower().endswith(".md")


def check_md_links(md_path, text, counts):
    """md から張られた相対リンクの参照先が実在することを確かめる。"""
    failures = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for match in LINK_RE.finditer(line):
            target = match.group("target")
            if not is_checkable_link(target):
                counts.links_skipped += 1
                continue
            rel = target.split("#", 1)[0]
            counts.links_checked += 1
            if not (md_path.parent / rel).exists():
                failures.append(
                    Failure(
                        md_path,
                        lineno,
                        CHECK_MD_LINK,
                        "リンク先が無い: {}".format(rel),
                    )
                )
    return failures


# --- 検査 4: scripts/ 配下への参照 ---


def iter_script_refs(text):
    """`scripts/` 配下への参照を (行番号, スキル名 or None, 相対パス) で返す。

    スキル名が付く形（`~/.claude/skills/<スキル名>/scripts/x`）は skills ルートから、
    付かない形（`scripts/x`）はそのファイルが属するスキルのディレクトリから解決する。
    """
    for lineno, line in enumerate(text.split("\n"), start=1):
        for match in SCRIPT_REF_RE.finditer(line):
            skill = match.group("skill")
            rel = match.group("qualified") if skill else match.group("bare")
            rel = rel.rstrip(TRAILING_PUNCT)
            if not rel:
                continue
            yield lineno, skill, rel


def check_script_refs(md_path, skill_dir, skills_root, text, counts):
    """参照された `scripts/` 配下のファイルが実在し、`.sh` に実行権限があることを確かめる。"""
    failures = []
    for lineno, skill, rel in iter_script_refs(text):
        counts.script_refs += 1
        base = (skills_root / skill) if skill else skill_dir
        target = base / "scripts" / rel
        if not target.exists():
            failures.append(
                Failure(
                    md_path,
                    lineno,
                    CHECK_SCRIPT_REF,
                    "参照先が無い: {}".format(target),
                )
            )
            continue
        if target.suffix == ".sh":
            mode = stat.S_IMODE(target.stat().st_mode)
            # os.access(X_OK) は root だと常に真になるので、モードのビットを直接見る。
            if not mode & 0o111:
                failures.append(
                    Failure(
                        md_path,
                        lineno,
                        CHECK_SCRIPT_REF,
                        "実行権限が無い（mode {:03o}）: {}".format(mode, target),
                    )
                )
    return failures


# --- 走査 ---


def iter_skill_dirs(skills_root):
    """skills ルート直下のスキルディレクトリを返す。

    `.gitkeep` のようなファイルと、`.` で始まるディレクトリは対象外にする。
    """
    return sorted(
        p
        for p in skills_root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def check_skill(skill_dir, skills_root, counts):
    """1 スキルを検査して、落ちた件を返す。"""
    failures = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        # SKILL.md を消せば検査が静かになる、という抜け道を作らない。
        failures.append(
            Failure(skill_md, 1, CHECK_FRONTMATTER, "SKILL.md が無い")
        )

    for md_path in sorted(skill_dir.rglob("*.md")):
        text, read_failure = read_text(md_path)
        if read_failure is not None:
            failures.append(read_failure)
            continue
        counts.files += 1
        if md_path == skill_md:
            failures.extend(check_frontmatter(md_path, text))
            failures.extend(check_line_limit(md_path, text))
        failures.extend(check_md_links(md_path, text, counts))
        failures.extend(
            check_script_refs(md_path, skill_dir, skills_root, text, counts)
        )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="`.claude/skills/` 配下のスキルを検査する。"
        " 問題があれば終了コード 1 と落ちた箇所を返す。",
    )
    parser.add_argument(
        "skills_root",
        nargs="?",
        default=DEFAULT_SKILLS_ROOT,
        help="検査するディレクトリ。既定は {}（リポジトリのルートからの相対）。"
        " わざと壊した入力で 1 が返ることを確かめるときだけ指定する。".format(
            DEFAULT_SKILLS_ROOT
        ),
    )
    args = parser.parse_args(argv)

    skills_root = Path(args.skills_root)
    if not skills_root.is_dir():
        sys.stderr.write(
            "check-skills: 検査対象が無い: {}"
            "（リポジトリのルートから実行する）\n".format(skills_root)
        )
        return 1

    counts = Counts()
    failures = []
    for skill_dir in iter_skill_dirs(skills_root):
        counts.skills += 1
        failures.extend(check_skill(skill_dir, skills_root, counts))

    for failure in sorted(failures, key=Failure.sort_key):
        print(failure.format())

    summary = (
        "check-skills: {} スキル / {} ファイルを検査した"
        "（リンク {} 件・scripts 参照 {} 件を確認、リンク {} 件は対象外）。".format(
            counts.skills,
            counts.files,
            counts.links_checked,
            counts.script_refs,
            counts.links_skipped,
        )
    )
    if failures:
        print(summary + "{} 件が落ちた".format(len(failures)))
        return 1
    print(summary + "問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
