"""ガードのフック（PreToolUse）との橋渡し。止めるか通すかは決めない。

止めるか通すかの規則は、ドメインの `Guard`（`domain/guard.py`）にある。ここがするのは翻訳だけである。

- 実パスを場所（`WriteZone`）に振り分ける。入れ子の場所は一番深いものを選ぶ
- glob でテストのパスかを決める（`is_test`）
- ツールの入力と Bash のコマンド行（`shell.py`）から、書き込みの宛先と操作を取り出す
- 止めた理由（`Refusal`）を、モデルに渡す文面にする

driver は Guard と場所の根のパス（`GuardContext`）を環境変数 `AUTODEV_GUARD` でフックへ渡し、フックは
それを組み立て直して Guard に判定させる。

- フックの設定（`claude --settings` に渡す JSON）はランの頭で 1 回書けばよく、何を止めるかは
  ステージごとに環境変数で渡す（LEDGER HK-01）。フックは claude の環境を受け継ぐ
- フックは終了コード 2 で呼び出しを止め、標準エラーに書いた理由がモデルに渡る（HK-02）。
  `bypassPermissions` でも走る（HK-03）
- フックの入力が読めない・設定が渡っていない・フックの中で例外が出たときは止める（LEDGER N-92 を
  ADDENDUM §11 で採用）。フックが黙って落ちると、ガードが消えたまま走る（HK-19）
- フックでは Bash 越しの書き込みを全部は見つけられない（HK-10）。worktree の中は最後に git の差分で
  確かめられる。gh と git push には、起動するときに資格情報を外す二重の栓がある
  （`agent_runtime.github_withheld_env`）
"""

from __future__ import annotations

import glob as globbing
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.guard import AskVerdict, Operation, Refusal, RefusalReason, WriteTarget, WriteZone
from ..domain.values import DEFAULT_TEST_GLOBS, GlobPattern, Guard, WriteScope
from . import globs
from .shell import Where, bash_facts, expand, is_literal, name_of, simple_commands, strip_heredocs

#: フックへ Guard と GuardContext を渡す環境変数
GUARD_ENV = "AUTODEV_GUARD"
#: フックの `matcher`。MultiEdit は `edits[].file_path`、NotebookEdit は `notebook_path` に宛先がある（HK-05）
WRITE_MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash"
ASK_MATCHER = "Bash"
DENY_WRITES = "deny-writes.py"
PARK_ON_ASK = "park-on-ask.py"
#: ask の入口のファイル名。ask の呼び出しは入口のパスで見分ける（HK-23）
LAUNCHER = "autodev.py"
#: 回答のファイルの置き場（ランディレクトリから。DOMAIN_MODEL §14）
ANSWERS_DIR = "answers"
#: 止まった呼び出しを通すとき、回答のファイルのパスを ask のコマンドに足す引数
ANSWER_FILE_OPTION = "--answer-file"
#: フックが呼び出しを止める終了コード
DENY_EXIT = 2


class GuardUnavailable(ValueError):
    """フックに設定が渡っていない・形が崩れている。"""


# --- 受け渡し ---


@dataclass(frozen=True)
class GuardContext:
    """実行のときにしか分からない、場所ごとの根のパスと、書いてよいファイル。判断は持たない。"""

    #: ステージの worktree
    tree: str
    run_dir: str
    home: str
    #: 対象リポジトリの手元の checkout
    target_repo: str
    #: OS の一時ディレクトリ（`/tmp`・`$TMPDIR`）
    temp_dirs: tuple[str, ...]
    test_globs: tuple[GlobPattern, ...] = DEFAULT_TEST_GLOBS
    #: WriteScope.LISTED のときに書いてよいパス（worktree の根から）
    listed: tuple[str, ...] = ()

    @property
    def answers_dir(self) -> str:
        return os.path.join(self.run_dir, ANSWERS_DIR)

    def roots(self) -> list[tuple[WriteZone, str]]:
        found = [
            (WriteZone.TREE, self.tree),
            (WriteZone.RUN_DIR, self.run_dir),
            (WriteZone.HOME, self.home),
            (WriteZone.TARGET_REPO, self.target_repo),
        ]
        found += [(WriteZone.TEMP, path) for path in self.temp_dirs]
        return found


def guard_context(
    *,
    tree: str | os.PathLike[str],
    run_dir: str | os.PathLike[str],
    target_repo: str | os.PathLike[str],
    test_globs: Sequence[GlobPattern] = DEFAULT_TEST_GLOBS,
    listed: Sequence[str] = (),
    home: str | os.PathLike[str] | None = None,
    temp_dirs: Sequence[str | os.PathLike[str]] | None = None,
) -> GuardContext:
    if temp_dirs is None:
        found = ["/tmp", os.environ.get("TMPDIR") or "", tempfile.gettempdir()]
        temp_dirs = [p for p in found if p]
    return GuardContext(
        tree=os.path.abspath(tree),
        run_dir=os.path.abspath(run_dir),
        home=os.path.abspath(home if home is not None else Path.home()),
        target_repo=os.path.abspath(target_repo),
        temp_dirs=tuple(dict.fromkeys(os.path.abspath(p) for p in temp_dirs)),
        test_globs=tuple(test_globs),
        listed=tuple(globs.normalize(p) for p in listed),
    )


def stage_env(guard: Guard, context: GuardContext) -> dict[str, str]:
    """LLM のステージを起こすときに足す環境変数（`AgentCall.env`）。"""
    payload = {
        "guard": {
            "writes": guard.writes.value,
            "judge": guard.judge,
            "readsDesign": guard.reads_design,
            "canAsk": guard.can_ask,
        },
        "context": {
            "tree": context.tree,
            "runDir": context.run_dir,
            "home": context.home,
            "targetRepo": context.target_repo,
            "tempDirs": list(context.temp_dirs),
            "testGlobs": [str(g) for g in context.test_globs],
            "listed": list(context.listed),
        },
    }
    return {GUARD_ENV: json.dumps(payload, ensure_ascii=False)}


def load(environ: Mapping[str, str]) -> tuple[Guard, GuardContext]:
    """フックの側で、Guard と GuardContext を組み立て直す。"""
    raw = environ.get(GUARD_ENV)
    if not raw:
        raise GuardUnavailable(f"{GUARD_ENV} が渡っていない")
    try:
        data = json.loads(raw)
        rules, where = data["guard"], data["context"]
        guard = Guard(
            WriteScope(rules["writes"]),
            judge=rules["judge"] is True,
            reads_design=rules["readsDesign"] is True,
            can_ask=rules["canAsk"] is True,
        )
        # テストのパスが渡らなかったら既定に戻し、テストを無防備にしない（HK-18）
        test_globs = tuple(GlobPattern(g) for g in where.get("testGlobs") or ())
        context = GuardContext(
            tree=_absolute(where["tree"]),
            run_dir=_absolute(where["runDir"]),
            home=_absolute(where["home"]),
            target_repo=_absolute(where["targetRepo"]),
            temp_dirs=tuple(_absolute(p) for p in where["tempDirs"]),
            test_globs=test_globs or DEFAULT_TEST_GLOBS,
            listed=tuple(globs.normalize(str(p)) for p in where.get("listed") or ()),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise GuardUnavailable(f"{GUARD_ENV} の形が崩れている: {error!r}") from error
    return guard, context


def _absolute(path: Any) -> str:
    text = str(path)
    if not os.path.isabs(text):
        raise ValueError(f"絶対パスでない: {text!r}")
    return text


# --- claude --settings に渡す設定 ---


def hooks_dir() -> Path:
    """スキルの根の `hooks/`。階層を数えて上らず、`SKILL.md` を探して決める（LEDGER FP-06）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "SKILL.md").is_file():
            return parent / "hooks"
    raise FileNotFoundError(f"SKILL.md が見つからない（{here} から上）")


def hook_settings(python: str | None = None, hooks: Path | None = None) -> dict[str, Any]:
    """`claude --settings` に渡す設定。worktree には置かない（commit に混ざる。HK-01）。

    判断の優先順位は deny > defer なので、書き込みを止めるフックと ask を止めるフックを並べても
    順番を気にしなくてよい（HK-22）。
    """
    interpreter = python or sys.executable
    where = hooks or hooks_dir()

    def command(name: str) -> dict[str, str]:
        return {"type": "command", "command": shlex.join([interpreter, str(where / name)])}

    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": WRITE_MATCHER, "hooks": [command(DENY_WRITES)]},
                {"matcher": ASK_MATCHER, "hooks": [command(PARK_ON_ASK)]},
            ]
        }
    }


def write_hook_settings(path: str | os.PathLike[str], python: str | None = None) -> Path:
    """`guard.json` を書く。一時ファイルに書いてから置き換える（LEDGER FP-04）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(hook_settings(python), ensure_ascii=False, indent=2) + "\n"
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    return target


# --- 翻訳: パス → WriteTarget ---


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def zone_of(context: GuardContext, real_path: str) -> tuple[WriteZone, str]:
    """一番深い場所と、その根の実パス。どれにも入らなければ ELSEWHERE。"""
    best: tuple[WriteZone, str] = (WriteZone.ELSEWHERE, "")
    depth = -1
    for zone, root in context.roots():
        real_root = os.path.realpath(root)
        if _inside(real_path, real_root) and len(real_root) > depth:
            best, depth = (zone, real_root), len(real_root)
    return best


def _is_test_path(context: GuardContext, rel: str, directory: bool) -> bool:
    if globs.matches_any(rel, context.test_globs):
        return True
    # ディレクトリは、中にテストのパスが入りうるか（`tests` は `**/tests/**` に当たる）
    return directory and globs.matches_any(f"{rel}/x", context.test_globs)


def _tree_target(context: GuardContext, rel: str, *, directory: bool = False) -> WriteTarget:
    path = globs.normalize(rel)
    return WriteTarget(
        WriteZone.TREE,
        path,
        is_test=_is_test_path(context, path, directory),
        listed=path in context.listed,
    )


def _tracked_under(context: GuardContext, full: str) -> list[str]:
    """ディレクトリの中の、git が追跡しているファイル（worktree の根から）。無視しているもの・追跡して
    いないディレクトリはたどらない。git が使えなければ、`.git` を除いて全部たどる。"""
    tree = os.path.realpath(context.tree)
    try:
        listed = subprocess.run(
            ["git", "-C", tree, "ls-files", "-z", "--", os.path.relpath(full, tree)],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return [p for p in listed.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        found: list[str] = []
        for root, dirs, names in os.walk(full):
            dirs[:] = [d for d in dirs if d != ".git"]
            found += [os.path.relpath(os.path.join(root, n), tree) for n in names]
        return found


def _targets_at(context: GuardContext, full: str, shown: str) -> list[WriteTarget]:
    zone, root = zone_of(context, full)
    if zone is not WriteZone.TREE:
        return [WriteTarget(zone, shown)]
    rel = os.path.relpath(full, root)
    if not os.path.isdir(full):
        return [_tree_target(context, rel)]
    return [
        _tree_target(context, rel, directory=True),
        *(_tree_target(context, inner) for inner in _tracked_under(context, full)),
    ]


def tool_targets(context: GuardContext, raw: str, cwd: str) -> list[WriteTarget]:
    """Write・Edit などのツールのパス。そのまま使う（`[`・`*`・`?`・`$` を glob や変数とみなさない）。"""
    return _targets_at(context, os.path.realpath(os.path.join(cwd, raw)), raw)


_UNEXPANDED = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)?|\$[A-Za-z_0-9@*#?$!-]*|`[^`]*`?")


def bash_targets(context: GuardContext, raw: str, where: Where) -> list[WriteTarget]:
    """Bash のコマンド行のパス。`~` と環境変数を展開し、glob は実物に当てる。

    先頭が展開できない変数や置換なら、場所が決まらない（UNKNOWN）。途中から先が決まらないものは、
    決まらない所を glob の `*` とみなし、決まっている前置きで場所を決める（`tests/$X.py`）。
    cd の引数が決まらなかった後の相対パスも、場所が決まらない。
    """
    expanded = expand(raw)
    pattern = _UNEXPANDED.sub("*", expanded)
    if expanded.startswith(("$", "`")) or (not os.path.isabs(pattern) and not where.known):
        return [WriteTarget(WriteZone.UNKNOWN, raw)]
    targets: list[WriteTarget] = []
    for cwd in where.cwds:
        full = os.path.join(cwd, pattern)
        if is_literal(pattern):
            targets += _targets_at(context, os.path.realpath(full), raw)
            continue
        # 決まっている前置きのディレクトリで場所を決める
        fixed = re.split(r"[*?\[]", full, maxsplit=1)[0]
        directory = fixed.rstrip(os.sep) if fixed.endswith(os.sep) else os.path.dirname(fixed)
        real_directory = os.path.realpath(directory)
        zone, root = zone_of(context, real_directory)
        if zone is not WriteZone.TREE:
            targets.append(WriteTarget(zone, raw))
            continue
        # glob そのものを 1 つの宛先にし（listed には入らない）、当たる実物も宛先にする
        glob_path = real_directory + full[len(directory) :]
        targets.append(_tree_target(context, os.path.relpath(glob_path, root)))
        for match in globbing.glob(full):
            targets += _targets_at(context, os.path.realpath(match), match)
    return targets


# --- 当てる ---

_MESSAGES: Mapping[RefusalReason, str] = {
    RefusalReason.READ_ONLY_TREE: (
        "このステージは worktree の中を書き換えられません: {path}\n"
        "読んで判断するステージです。結果は StructuredOutput ツールで返してください（ファイルに書く必要はありません）。"
    ),
    RefusalReason.TESTS_PROTECTED: (
        "このステージはテストのファイルを書き換えられません: {path}\n"
        "テストが仕様と食い違うと判断したら、直さずに結果の JSON でそのことを報告してください。"
    ),
    RefusalReason.NON_TESTS_PROTECTED: (
        "このステージはテスト以外のファイルを書き換えられません: {path}\n"
        "実装の出力が受入条件と食い違うなら、期待値を書かずに結果の JSON でそのことを報告してください。"
    ),
    RefusalReason.NOT_LISTED: (
        "このステージが書き換えてよいのは、渡されたファイルだけです: {path}\n"
        "書いてよいファイル: {listed}（glob ではなくパスを 1 つずつ書いてください）"
    ),
    RefusalReason.OUTSIDE_TREE: (
        "worktree の外のこの場所には書き込めません: {path}\n"
        "結果は StructuredOutput ツールで返してください。一時ファイルは /tmp に置けます。"
    ),
    RefusalReason.UNKNOWN_PLACE: (
        "書き込む先の場所が決まらないので止めました: {path}\n"
        "変数や置換を使わず、パスを書いてください。cd した後なら、絶対パスで書いてください。"
    ),
    RefusalReason.GITHUB: (
        "GitHub の操作（gh）は、このステージからはできません。PR を作る・見る・push するのは driver です。"
    ),
    RefusalReason.PUSH: "git push は、このステージからはできません。push するのは driver です。",
    RefusalReason.UNKNOWN_COMMAND: (
        "コマンドの名前が決まらないので止めました（変数や置換をコマンドの位置に置かないでください）。"
    ),
    RefusalReason.ASK_NOT_ALLOWED: (
        "このステージは ask で聞けません。決められないことは結果の JSON で報告してください。"
    ),
}
DENY_UNREADABLE = "ガードのフックが入力を読めなかったので、呼び出しを止めました: {reason}"
DENY_UNAVAILABLE = (
    "ガードの設定が渡っていないので、呼び出しを止めました（driver の不具合）: {reason}"
)


def describe(refusal: Refusal, context: GuardContext) -> str:
    return _MESSAGES[refusal.reason].format(
        path=refusal.subject, listed=", ".join(context.listed) or "（なし）"
    )


def check_tool(
    guard: Guard,
    context: GuardContext,
    tool: str,
    tool_input: Mapping[str, Any],
    cwd: str | None = None,
) -> Refusal | None:
    """止めるなら、ドメインが返した理由。"""
    base = cwd or context.tree
    targets: list[WriteTarget] = []
    operations: list[Operation] = []
    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        for path in _tool_paths(tool_input):
            targets += tool_targets(context, path, base)
    elif tool == "Bash":
        facts = bash_facts(str(tool_input.get("command") or ""), base, context.home)
        operations = facts.operations
        for path, where in facts.writes:
            targets += bash_targets(context, path, where)
    for operation in operations:
        refusal = guard.judge_operation(operation)
        if refusal:
            return refusal
    for target in targets:
        refusal = guard.judge_write(target)
        if refusal:
            return refusal
    return None


def _tool_paths(tool_input: Mapping[str, Any]) -> list[str]:
    found = [tool_input.get("file_path"), tool_input.get("notebook_path")]
    for edit in tool_input.get("edits") or ():
        if isinstance(edit, Mapping):
            found.append(edit.get("file_path"))
    return [str(p) for p in found if p]


# --- ask ---


@dataclass(frozen=True)
class AskCall:
    #: ask の単純コマンドの語（リダイレクトを除く）
    words: tuple[str, ...]
    question: str


def parse_ask(command: str) -> AskCall | None:
    """`<入口> ask --question <質問>` の呼び出しなら、その中身。`python3 <入口> ask` の形も見る。"""
    for simple in simple_commands(strip_heredocs(command)[0]):
        words = simple.words
        for index, word in enumerate(words[:-1]):
            if name_of(word) == LAUNCHER and words[index + 1] == "ask":
                question = _option(words[index + 2 :], "--question") or ""
                return AskCall(words, question)
    return None


def ask_question(tool_input: Mapping[str, Any]) -> str | None:
    """defer で止まった呼び出し（`deferred_tool_use` の input）から質問を読む。ask でなければ None。"""
    found = parse_ask(str(tool_input.get("command") or ""))
    return found.question if found else None


def _option(words: Sequence[str], name: str) -> str | None:
    for index, word in enumerate(words):
        if word == name and index + 1 < len(words):
            return words[index + 1]
        if word.startswith(f"{name}="):
            return word[len(name) + 1 :]
    return None


_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def answer_path(answers_dir: str | os.PathLike[str], tool_use_id: str) -> Path:
    """回答のファイル `answers/<tool_use_id>.json`。id はファイル名に入るので形を確かめる。"""
    if not _TOOL_USE_ID.fullmatch(tool_use_id):
        raise ValueError(f"tool_use_id の形が違う: {tool_use_id!r}")
    return Path(answers_dir) / f"{tool_use_id}.json"


@dataclass(frozen=True)
class HookReply:
    """フックの返事。`stdout` は JSON の判断、`stderr` は止めた理由（モデルに渡る）。"""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def deny(reason: str) -> HookReply:
    return HookReply(DENY_EXIT, "", reason)


def _decision(kind: str, **extra: Any) -> str:
    body = {"hookEventName": "PreToolUse", "permissionDecision": kind, **extra}
    return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)


def park(guard: Guard, context: GuardContext, payload: Mapping[str, Any]) -> HookReply:
    """ask の呼び出しを、Guard の判断（回答のファイルが無ければ defer・在れば通す）に従って返す。

    `--resume` すると同じ呼び出しで PreToolUse がもう一度走り、tool_use_id は変わらない（HK-20）。
    通すときは、ask の単純コマンドの語に回答のファイルのパスを足した 1 つのコマンドに置き換える
    （コマンドは tool_use_id を知らない。元の行の後ろに足すと、パイプや `;` の先へ渡ってしまう）。
    """
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") != "Bash" or not isinstance(tool_input, Mapping):
        tool_input = {}
    ask = parse_ask(str(tool_input.get("command") or ""))
    if ask is None:
        return HookReply()
    tool_use_id = payload.get("tool_use_id")
    try:
        if not isinstance(tool_use_id, str):
            raise ValueError("tool_use_id が無い")
        answer = answer_path(context.answers_dir, tool_use_id)
    except ValueError as error:
        return deny(DENY_UNREADABLE.format(reason=error))
    verdict = guard.judge_ask(answered=answer.is_file())
    if verdict is AskVerdict.REFUSE:
        return deny(_MESSAGES[RefusalReason.ASK_NOT_ALLOWED])
    if verdict is AskVerdict.DEFER:
        return HookReply(0, _decision("defer"))
    updated = dict(tool_input)
    updated["command"] = shlex.join([*ask.words, ANSWER_FILE_OPTION, str(answer)])
    return HookReply(0, _decision("allow", updatedInput=updated))


def guard_writes(guard: Guard, context: GuardContext, payload: Mapping[str, Any]) -> HookReply:
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, Mapping):
        return deny(DENY_UNREADABLE.format(reason="tool_name か tool_input が無い"))
    cwd = payload.get("cwd")
    refusal = check_tool(guard, context, tool, tool_input, cwd if isinstance(cwd, str) else None)
    return deny(describe(refusal, context)) if refusal else HookReply()


def run_hook(handler: str, stdin: str, environ: Mapping[str, str]) -> HookReply:
    """フックの入口が呼ぶ。読めない入力・渡っていない設定・例外は、どれも止める側に倒す（N-92）。"""
    try:
        payload = json.loads(stdin)
        if not isinstance(payload, dict):
            raise ValueError("JSON の object でない")
    except ValueError as error:
        return deny(DENY_UNREADABLE.format(reason=error))
    try:
        guard, context = load(environ)
    except GuardUnavailable as error:
        return deny(DENY_UNAVAILABLE.format(reason=error))
    try:
        if handler == DENY_WRITES:
            return guard_writes(guard, context, payload)
        if handler == PARK_ON_ASK:
            return park(guard, context, payload)
        return deny(DENY_UNAVAILABLE.format(reason=f"知らないフック: {handler}"))
    except Exception as error:
        return deny(DENY_UNREADABLE.format(reason=f"{type(error).__name__}: {error}"))
