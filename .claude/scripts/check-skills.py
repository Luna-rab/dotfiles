#!/usr/bin/env python3
"""`.claude/skills/` 配下のスキルが壊れていないかを検査する。

このリポジトリは Claude Code の skills（`.claude/skills/<スキル名>/SKILL.md` と、そこから
参照される補助ファイル）を dotfiles として持ち歩く。リンク切れや権限の落ちたスクリプトは、
実際にスキルを起動するまで気づけない。それを commit の時点で捕まえるために、次の 4 つを検査する。

1. frontmatter（`SKILL.md` の先頭にある `---` で挟まれた YAML）が YAML として読めること
2. `SKILL.md` が 500 行以下であること（基準は `.claude/rules/editing-skills.md`）
3. スキル内の `*.md` から張られた相対リンクの参照先が実在すること
4. `*.md` が参照する `scripts/` 配下のファイルが実在し、直接起動されるファイル
   （`.sh`、および 1 行目が `#!` で始まるファイル）に実行権限があること

使い方（リポジトリのルートから、引数なしで実行する）:

    python3 .claude/scripts/check-skills.py

問題が無ければ終了コード 0、1 件でも見つかれば 1 を返す。落ちた箇所は
`パス:行番号: 検査名: 説明` の形で 1 件 1 行ずつ標準出力に出す。
検査対象のスキルが 1 つも見つからない場合も、CI が緑のまま素通りしないように 1 を返す。

第 1 引数に別のディレクトリを渡すと、そこを `.claude/skills/` の代わりに検査する。
これは「わざと壊した入力で終了コード 1 が返ること」を、実物のスキルに触らずに確かめるために
ある。CI は引数なしで呼ぶので、既定値のままなら振る舞いは変わらない。
"""

import argparse
import errno
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

# `Path.stat()` が「そのパスは無い」を意味して投げる errno。`Path.exists()` が False を返す
# のと同じ集合にそろえてある。これ以外（EACCES = 権限が無い、など）は「確かめられない」として
# 1 件の失敗に記録し、走査は止めない。
MISSING_ERRNOS = (errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP)

# `[表示文字](リンク先)`。リンク先は 2 通りの書き方を受ける。
#   - 山括弧つき `[x](<a b.md>)` — CommonMark がリンク先に空白を許すときの書き方
#   - 素の形 `[x](a.md "title")` — 空白と `)` の手前まで（`"title"` は落ちる）
# 表示文字に `[]` が 1 段だけ入る形（`[see [x] here](a.md)`）も拾う。
# 拾わない書き方: 表示文字を 2 段以上入れ子にした形、リンク先に丸括弧を含む形
# （`[x](a(b).md)`）、参照方式の使用側（`[x][r1]`。定義側の `[r1]: a.md` は REF_DEF_RE で拾う）。
LINK_TEXT = r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]"
LINK_RE = re.compile(LINK_TEXT + r"\(\s*(?:<(?P<angle>[^<>]*)>|(?P<target>[^)\s]+))")

# 参照方式のリンク定義 `[r1]: a.md`。行頭（インデントは CommonMark に合わせて 3 桁まで）。
REF_DEF_RE = re.compile(
    r"^ {0,3}\[(?P<label>[^\]]+)\]:\s*(?:<(?P<angle>[^<>]*)>|(?P<target>\S+))"
)

# HTML で書いたリンク `<a href="a.md">`。markdown の中に直接書ける。
HTML_LINK_RE = re.compile(
    r"<a\s[^>]*?href\s*=\s*"
    r"(?:\"(?P<dquote>[^\"]*)\"|'(?P<squote>[^']*)'|(?P<bare>[^\s>]+))",
    re.IGNORECASE,
)

# `https:` や `mailto:` のようなスキーム付き。相対リンクではないので検査しない。
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# リンクを検査しなかった理由。集計行に内訳として出す。「対象外」を 1 つの数にまとめると、
# 拾い漏らしたリンクが外部 URL に紛れて見えなくなるので分けて数える。
LINK_SKIP_URL = "スキーム付き URL"
LINK_SKIP_ANCHOR = "ページ内リンク"
LINK_SKIP_ABSOLUTE = "ホーム基準や絶対のパス"
LINK_SKIP_NOT_MD = ".md ではない"
LINK_SKIP_REASONS = (
    LINK_SKIP_URL,
    LINK_SKIP_ANCHOR,
    LINK_SKIP_ABSOLUTE,
    LINK_SKIP_NOT_MD,
)

# パス名に使う文字。ここを「使ってよい文字」の側で書くのは、`scripts/x.sh; echo ...` のような
# シェルの例（hooks.md の動作確認）で `;` まで取り込んで参照先を見失わないようにするため。
PATH_CHARS = r"A-Za-z0-9._/\-"

# スキルのディレクトリ名に使う文字。
SKILL_NAME_CHARS = r"A-Za-z0-9._\-"

# `scripts/` の直前 1 文字がこれなら参照として拾わない。目的は 2 つだけである。
#   - 別語の一部を弾く（`myscripts/x.sh` の `scripts`）
#   - スキルの外の `scripts/` を弾く（`.claude/scripts/statusline.sh`、`docs/scripts/x.sh`）
# ASCII に限るのが要点。`\w` は Python 3 では Unicode に当たるため、このリポジトリのスキルが
# 全文日本語であることと相まって `詳細はscripts/x.sh` を取りこぼしていた。
NOT_BEFORE = r"A-Za-z0-9_/~.\-"

# `scripts/` 配下への参照。4 つの書き方を 1 本の正規表現で拾う。並び順に意味がある
# （前の候補から順に試されるので、より長い書き方を先に置く）。
#   1. skills ルートから書いた形（`~/.claude/skills/team-supervisor/scripts/gh-review.py`）
#      → skills ルートから解決する
#   2. 隣のスキルを相対で指す形（`../team-supervisor/scripts/gh-review.py`）
#      → そのスキルのディレクトリの 1 つ上から解決する
#   3. 同じスキルの中を指す形（`scripts/x.sh`、`./scripts/x.sh`）
#      → そのファイルが属するスキルのディレクトリから解決する
#   4. スキル名から書いた形（`team-supervisor/scripts/gh-review.py`）
#      → 2 と同じ場所から解決する。ただし名前が実在するスキルのときだけ参照として扱う
#        （`docs/scripts/x.sh` や `.claude/scripts/x.sh` を誤って拾わないため）
# 拾わない書き方: `../scripts/x.sh`（`..` はスキル名ではないので 4 の検証で落ちる）、
# 実在しないスキル名から書いた形（同上。名前を間違えた参照は検査されない）。
SCRIPT_REF_RE = re.compile(
    r"\.claude/skills/(?P<root_skill>[{name}]+)/scripts/(?P<root_path>[{chars}]+)"
    r"|(?<![{lead}])\.\./(?P<up_skill>[{name}]+)/scripts/(?P<up_path>[{chars}]+)"
    r"|(?<![{lead}])(?:\./)?scripts/(?P<same_path>[{chars}]+)"
    r"|(?<![{lead}])(?P<named_skill>[{name}]+)/scripts/(?P<named_path>[{chars}]+)".format(
        name=SKILL_NAME_CHARS, chars=PATH_CHARS, lead=NOT_BEFORE
    )
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
        self.links_skipped = dict.fromkeys(LINK_SKIP_REASONS, 0)
        self.script_refs = 0

    def links_skipped_total(self):
        return sum(self.links_skipped.values())

    def links_skipped_detail(self):
        return "・".join(
            "{} {}".format(reason, self.links_skipped[reason])
            for reason in LINK_SKIP_REASONS
        )


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


def stat_path(path):
    """`path` の stat を取る。戻り値は (stat 結果, 確かめられない理由)。

    - (stat 結果, None): 在る
    - (None, None): 無い
    - (None, 理由): 権限が無いなどで確かめられない（理由には対象のパスも含まれる）

    `Path.exists()` を直に呼ばないのは、EACCES（権限が無い）で例外が外へ出るからである。
    `scripts/` を `chmod 000` にしたスキルが 1 つあるだけで走査全体が traceback で止まり、
    後続のスキルが 1 つも検査されなくなる。read_text() と同じく、1 件の失敗として記録して
    先へ進めるようにここで受ける。
    """
    try:
        return path.stat(), None
    except OSError as err:
        if err.errno in MISSING_ERRNOS:
            return None, None
        return None, str(err)


def starts_with_shebang(path):
    """1 行目が `#!` で始まるかを返す。戻り値は (真偽, 読めない理由)。

    読めない理由には対象のパスも含まれる。read_text() と同じく、例外で走査を止めない。
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError as err:
        return None, str(err)
    # UTF-8 の BOM を付けて保存されたスクリプトでも 1 行目を見分けられるようにする。
    return head.lstrip(b"\xef\xbb\xbf").startswith(b"#!"), None


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


def count_lines(text):
    """エディタや `wc -l` と同じ数え方で行数を返す。

    `str.splitlines()` を使わないのは、改行のほかに `\\v` `\\f` `\\x1c` `\\x1d` `\\x1e`
    `\\x85` `U+2028` `U+2029` まで行の区切りに数えるからである。`.claude/rules/editing-skills.md`
    の「500 行以下」はエディタが数える行のことなので、基準がずれる（`\\f` を各行に入れた
    403 行の SKILL.md が 803 行と報告されていた）。

    末尾の改行は行を増やさない。`"a\\n" * 500` と `"a\\n" * 499 + "a"` はどちらも 500 行。
    """
    if not text:
        return 0
    count = text.count("\n")
    if not text.endswith("\n"):
        count += 1
    return count


def check_line_limit(skill_md, text):
    """SKILL.md が 500 行以下であることを確かめる。"""
    count = count_lines(text)
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


def iter_link_targets(line):
    """1 行に書かれたリンク先を順に返す。

    3 通りの書き方を拾う。`[x](a.md)`（山括弧つきの `[x](<a.md>)` を含む）、
    参照方式の定義 `[r1]: a.md`、HTML の `<a href="a.md">`。
    """
    for match in LINK_RE.finditer(line):
        angle = match.group("angle")
        yield angle.strip() if angle is not None else match.group("target")
    ref_def = REF_DEF_RE.match(line)
    if ref_def is not None:
        angle = ref_def.group("angle")
        yield angle.strip() if angle is not None else ref_def.group("target")
    for match in HTML_LINK_RE.finditer(line):
        for name in ("dquote", "squote", "bare"):
            value = match.group(name)
            if value is not None:
                yield value.strip()
                break


def link_skip_reason(target):
    """リンクの実在を確かめないなら理由を、確かめるなら None を返す。

    確かめないのは次の 4 つ。いずれもスキル内のファイルを指していない。
    - `https://...` のようなスキーム付き
    - `#見出し` だけのページ内リンク
    - `/` で始まる絶対パスと `~/` で始まるホーム基準のパス（どちらもそのファイルからの
      相対ではない。`~` はシェルが展開する記号であってパスの一部ではないので、
      `<スキルのディレクトリ>/~/...` を探しにいくと実在するファイルを誤って落とす）
    - `.md` で終わらない参照先（DoD が検査対象を `*.md` に限っている）
    """
    if SCHEME_RE.match(target):
        return LINK_SKIP_URL
    if target.startswith("#"):
        return LINK_SKIP_ANCHOR
    path_part = target.split("#", 1)[0]
    if not path_part:
        return LINK_SKIP_ANCHOR
    if path_part.startswith(("/", "~")):
        return LINK_SKIP_ABSOLUTE
    if not path_part.lower().endswith(".md"):
        return LINK_SKIP_NOT_MD
    return None


def check_md_links(md_path, text, counts):
    """md から張られた相対リンクの参照先が実在することを確かめる。"""
    failures = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for target in iter_link_targets(line):
            reason = link_skip_reason(target)
            if reason is not None:
                counts.links_skipped[reason] += 1
                continue
            rel = target.split("#", 1)[0]
            counts.links_checked += 1
            result, error = stat_path(md_path.parent / rel)
            if error is not None:
                failures.append(
                    Failure(
                        md_path,
                        lineno,
                        CHECK_READ,
                        "リンク先を確かめられない: {}".format(error),
                    )
                )
            elif result is None:
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
    """`scripts/` 配下への参照を (行番号, スキル名 or None, 相対パス, 実在検証の要否) で返す。

    スキル名が None なら、そのファイルが属するスキルのディレクトリから解決する
    （`scripts/x.sh`、`./scripts/x.sh`）。スキル名が付くなら、スキルのディレクトリの
    1 つ上から解決する。

    4 番目の値が True の書き方（`team-supervisor/scripts/x.sh`）は、スキル名の位置に
    何が書かれていても形が同じなので、実在するスキル名のときだけ参照として扱う。
    そうしないと `docs/scripts/x.sh` を「スキル docs の参照」と誤って拾ってしまう。
    """
    for lineno, line in enumerate(text.split("\n"), start=1):
        for match in SCRIPT_REF_RE.finditer(line):
            if match.group("root_skill"):
                skill, rel, verify_name = (
                    match.group("root_skill"),
                    match.group("root_path"),
                    False,
                )
            elif match.group("up_skill"):
                skill, rel, verify_name = (
                    match.group("up_skill"),
                    match.group("up_path"),
                    False,
                )
            elif match.group("same_path"):
                skill, rel, verify_name = None, match.group("same_path"), False
            else:
                skill, rel, verify_name = (
                    match.group("named_skill"),
                    match.group("named_path"),
                    True,
                )
            rel = rel.rstrip(TRAILING_PUNCT)
            if not rel:
                continue
            yield lineno, skill, rel, verify_name


def check_script_refs(md_path, skill_dir, known_skills, text, counts):
    """参照された `scripts/` 配下のファイルが実在し、実行権限があることを確かめる。

    実行権限を求めるのは、インタプリタを前に置かずそのまま起動されるファイルである。
    拡張子 `.sh` に加えて、1 行目が `#!` で始まるファイル（`gh-review.py` など）も見る。
    拡張子を並べる代わりに shebang を見るのは、`.py` `.pl` と増えるたびに漏れるのを避け、
    「直接起動される」という事実に近い指標で判定するためである。
    """
    failures = []
    for lineno, skill, rel, verify_name in iter_script_refs(text):
        if verify_name and skill not in known_skills:
            # スキル名ではない（`docs/scripts/...` など）。スキルの参照ではないので数えない。
            continue
        counts.script_refs += 1
        base = (skill_dir.parent / skill) if skill else skill_dir
        target = base / "scripts" / rel
        result, error = stat_path(target)
        if error is not None:
            failures.append(
                Failure(
                    md_path,
                    lineno,
                    CHECK_READ,
                    "参照先を確かめられない: {}".format(error),
                )
            )
            continue
        if result is None:
            failures.append(
                Failure(
                    md_path,
                    lineno,
                    CHECK_SCRIPT_REF,
                    "参照先が無い: {}".format(target),
                )
            )
            continue
        if not stat.S_ISREG(result.st_mode):
            continue  # ディレクトリなどは実行権限を見ない
        needs_exec = target.suffix == ".sh"
        if not needs_exec:
            shebang, read_error = starts_with_shebang(target)
            if read_error is not None:
                failures.append(
                    Failure(
                        md_path,
                        lineno,
                        CHECK_READ,
                        "参照先の 1 行目を読めない: {}".format(read_error),
                    )
                )
                continue
            needs_exec = shebang
        if not needs_exec:
            continue
        mode = stat.S_IMODE(result.st_mode)
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


def check_skill(skill_dir, known_skills, counts):
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
            check_script_refs(md_path, skill_dir, known_skills, text, counts)
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

    skill_dirs = iter_skill_dirs(skills_root)
    if not skill_dirs:
        # 0 件を「問題なし」で通すと、スキルが全部消えても CI が緑になる。
        # `.claude/skills/.gitkeep` があるためディレクトリだけは残る ＝ 実際に起こりうる。
        sys.stderr.write(
            "check-skills: 検査対象のスキルが 1 つも無い: {}"
            "（直下に <スキル名>/ のディレクトリが要る）\n".format(skills_root)
        )
        return 1
    known_skills = frozenset(p.name for p in skill_dirs)

    counts = Counts()
    failures = []
    for skill_dir in skill_dirs:
        counts.skills += 1
        failures.extend(check_skill(skill_dir, known_skills, counts))

    for failure in sorted(failures, key=Failure.sort_key):
        print(failure.format())

    summary = (
        "check-skills: {} スキル / {} ファイルを検査した"
        "（リンク {} 件・scripts 参照 {} 件を確認、リンク {} 件は対象外: {}）。".format(
            counts.skills,
            counts.files,
            counts.links_checked,
            counts.script_refs,
            counts.links_skipped_total(),
            counts.links_skipped_detail(),
        )
    )
    if failures:
        print(summary + "{} 件が落ちた".format(len(failures)))
        return 1
    print(summary + "問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
