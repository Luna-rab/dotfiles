"""ステージを 1 回走らせる。**driver とランナーの境目はこのファイル 1 本である。**

`claude -p` を子プロセスとして起動し、`--output-format stream-json` の JSONL を
**1 行ずつ読みながら**進める。別のランナーに替えるときに書き換えるのはここだけで、
`Call` と `Result` の形を保てば driver には手が入らない。

ステージの起動条件で効くもの（`claude -p` で実測した）。

    --allowedTools / --disallowedTools   ツールをセッションから消す
    --append-system-prompt               必須ルールを system 側に置く
    --max-turns                          ターンの上限。超えると終了コード 1 と
                                         `subtype: error_max_turns` が返る
    --model / --effort                   ステージごとにモデルと思考量を選ぶ
    --session-id / --resume              セッション id を呼ぶ側が決め、続きを回す
    --settings                           フックを外から渡す。**worktree に何も置かない**
    --json-schema                        結果の形を固定する。`StructuredOutput` ツールが
                                         増え、外れた出力はツールのエラーとして差し戻され、
                                         claude が自分で言い直す
    --permission-mode bypassPermissions  確認を挟まない。cwd の外も読み書きできる
                                         （ステージは `<ランディレクトリ>/` の下を読むので要る）

**`--json-schema` は生成時の制約ではなく事後の検証である。** 言い直しは `--max-turns` の
予算を食うので、結果を返すステージの上限は多めに置く。そして**検証に失敗しても終了コードは 0、
`subtype` は `success` のまま `structured_output` が空になる経路がある**（実測）。だから
`result` が在ることを別に確かめる。

**`--input-format stream-json` で起動し、標準入力を開いたまま進める。** こうすると
走行中に制御要求を送れる。

    {"type":"control_request","request_id":"…","request":{"subtype":"interrupt"}}

`interrupt` を送ると走行中のターンが打ち切られ、`result` イベントの `subtype` は
`error_during_execution` になる。**プロセスを kill するのと違って `result` が返るので、
usage も停止理由も残る。** `cancel_queued` は capability
`interrupt_cancel_queued_v1` があるときだけ付ける（無い CLI は黙って無視して
キューを走らせる）。**版の文字列で比べず `system/init` の `capabilities` を見る。**

`result` イベントを見たら標準入力を閉じる。閉じないと claude は次のメッセージを待って
終わらない。

打ち切るかどうかを決めるのは `Call.watch`——**driver が渡すコードで、モデルは入らない。**

**`--disallowedTools` で消せるのは名前のあるツールだけである。** Bash のリダイレクトで
書く道は残るので、ソースが動いていないことは完了チェック②（コミット数）と完了チェック⑤（テストの差分）で
実物から見る。
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core import events
from . import files, proc

CLAUDE = [
    "claude",
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    "bypassPermissions",
]
#: 打ち切りを送ってから `result` を待つ余裕。過ぎたら terminate する
GRACE = 60.0


@dataclass(frozen=True)
class Call:
    """ステージ 1 回の起動条件。**driver が決めるのはここに入るものだけである。**"""

    stage: str
    prompt: str
    cwd: str
    log_path: str
    #: 結果の形（JSON Schema draft-07 の本文）。渡すと `StructuredOutput` ツールが
    #: セッションに増え、**最後の応答がこの形の JSON になる**
    json_schema: str | None = None
    model: str | None = None
    effort: str | None = None
    #: 空なら絞らない。入れると**それ以外のツールが消える**
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    max_turns: int | None = None
    #: フックを書いた settings の json。worktree には置かない
    settings: str | None = None
    system_append: str | None = None
    #: 呼ぶ側が決めるセッション id。`resume` が真なら続きを回す
    session_id: str | None = None
    resume: bool = False
    env: dict[str, str | None] = field(default_factory=dict)
    timeout: int = 3600
    #: イベント 1 つごとに呼ばれる。**文字列を返すとその理由でステージを打ち切る。**
    #: 例外を投げてもステージは止めない（監視の誤りで作業を落とさない）
    watch: Callable[[dict[str, Any]], str | None] | None = None


@dataclass
class Result:
    """ステージ 1 回の結果。"""

    stage: str
    code: int
    session_id: str | None
    text: str
    usage: dict[str, Any]
    #: `result` イベントの `subtype`。正常終了は `success`
    reason: str | None
    log: str
    #: `structured_output`。`json_schema` を渡していないか、ステージが返さなかったら None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: int = 0
    turns: int = 0
    cost: float = 0.0
    #: driver が打ち切った理由。打ち切っていなければ None
    aborted: str | None = None
    #: フックが `defer` を返してステージが止まったときの `deferred_tool_use`。
    #: **`subtype` は `success` のままなので、止まったかどうかはここで見る。**
    deferred: dict[str, Any] | None = None
    capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """ステージが正常に終わったか。**結果の中身が妥当かはここでは見ない。**"""
        return (
            self.code == 0
            and self.reason == "success"
            and self.error is None
            and self.deferred is None
        )


def argv_for(call: Call) -> list[str]:
    """起動する引数を組む。

    ツールの一覧は**カンマでつないで 1 引数にする。** `--allowedTools <tools...>` は
    可変長で、次のオプションが来るまで後ろの引数を全部取る。
    """
    argv = list(CLAUDE)
    if call.json_schema:
        argv += ["--json-schema", call.json_schema]
    if call.model:
        argv += ["--model", call.model]
    if call.effort:
        argv += ["--effort", call.effort]
    if call.max_turns:
        argv += ["--max-turns", str(call.max_turns)]
    if call.settings:
        argv += ["--settings", call.settings]
    if call.system_append:
        argv += ["--append-system-prompt", call.system_append]
    if call.allowed_tools:
        argv += ["--allowedTools", ",".join(call.allowed_tools)]
    if call.disallowed_tools:
        argv += ["--disallowedTools", ",".join(call.disallowed_tools)]
    if call.session_id:
        argv += ["--resume", call.session_id] if call.resume else ["--session-id", call.session_id]
    return argv


def user_message(prompt: str) -> str:
    """`--input-format stream-json` に流す 1 行。

    `uuid` は**こちらで振る。** 振らないと `interrupt` の応答の `still_queued` /
    `cancelled` が空で返り、何が残ったのか分からない。
    """
    return (
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": prompt},
                "parent_tool_use_id": None,
                "uuid": str(uuid.uuid4()),
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def run(call: Call) -> Result:
    """ステージを 1 回走らせて結果を返す。**例外を投げない**（呼び出し側が `ok` を見る）。"""
    out = _drive(call)
    out.error = _fault(out)
    # 回答を待って止まったステージは、結果を返していなくても責めない
    if out.error or out.deferred is not None or not call.json_schema:
        return out
    if out.result is None:
        # **`subtype` が `success` でも結果が空のことがある**（実測）。Claude が
        # 「このスキーマは満たせない」と自由記述で説明して正常終了する経路があるので、
        # 終了コードと `subtype` だけでは失敗を見つけられない
        out.error = f"ステージが結果を返さなかった（最後の応答: {out.text[:200]!r}）"
    return out


def _fault(out: Result) -> str | None:
    """進めてはいけない理由。無ければ None。"""
    if out.reason is None:
        # 停止理由が取れないときは標準エラーを載せる。引数の誤りはここにだけ出る
        # （claude は JSONL を 1 行も出さずに終わる）
        return out.error or f"claude が結果を返さなかった（終了コード {out.code}）"
    if out.aborted:
        return f"driver が打ち切った: {out.aborted}"
    if out.reason != "success":
        return out.error or f"途中で止まった: {out.reason}"
    if out.deferred is None and out.code != 0:
        return f"終了コード {out.code}"
    return None


class _Session:
    """子プロセス 1 つ分。標準入力を開いたまま、標準出力を 1 行ずつ読む。"""

    def __init__(self, call: Call) -> None:
        self.call = call
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.err_path = f"{call.log_path}.err"
        files.write_text(self.err_path, "")
        self.log = open(files.write_text(call.log_path, ""), "w", encoding="utf-8")  # noqa: SIM115
        self.errfile = open(self.err_path, "w", encoding="utf-8")  # noqa: SIM115
        self.proc = subprocess.Popen(
            argv_for(call),
            cwd=call.cwd,
            env=proc.merged_env(call.env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errfile,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send_prompt(self) -> None:
        self._write(user_message(self.call.prompt))

    def interrupt(self, cancel_queued: bool) -> None:
        request: dict[str, Any] = {"subtype": "interrupt"}
        if cancel_queued:
            request["cancel_queued"] = True
        self._write(
            json.dumps(
                {"type": "control_request", "request_id": str(uuid.uuid4()), "request": request},
                ensure_ascii=False,
            )
            + "\n"
        )

    def _write(self, payload: str) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            return
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def close_stdin(self) -> None:
        """**`result` を見たら閉じる。** 閉じないと claude は次の入力を待って終わらない。"""
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            with contextlib.suppress(BrokenPipeError, OSError):
                self.proc.stdin.close()

    def record(self, line: str) -> None:
        """**読んだ行をその場でログへ落とす。** 打ち切っても記録が残る。"""
        self.log.write(line)
        self.log.flush()

    def finish(self) -> tuple[int, str]:
        self.close_stdin()
        try:
            code = self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        self.log.close()
        self.errfile.close()
        return code, files.read_text(self.err_path, "").strip()


def _drive(call: Call) -> Result:
    session = _Session(call)
    # **止まったステージを再開するときはプロンプトを渡さない。** 渡すと新しいターンが始まって
    # しまう。止まったツール呼び出しはそのまま再開される（実測）
    if call.prompt:
        session.send_prompt()
    else:
        session.close_stdin()
    state = _Collector(call.stage, call.log_path, call.session_id)
    deadline = time.monotonic() + call.timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            state.abort(f"制限時間を超えた（{call.timeout} 秒）")
            session.interrupt(state.can_cancel_queued)
            deadline = time.monotonic() + GRACE
            continue
        try:
            line = session.lines.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if line is None:
            break
        session.record(line)
        event = _event(line)
        if event is None:
            continue
        state.take(event)
        if event.get("type") == "result":
            session.close_stdin()
            continue
        if state.aborted:
            continue
        reason = _ask_watch(call, event)
        if reason:
            state.abort(reason)
            session.interrupt(state.can_cancel_queued)
            deadline = time.monotonic() + GRACE

    code, err = session.finish()
    return state.build(code, err)


def _event(line: str) -> dict[str, Any] | None:
    body = line.strip()
    if not body.startswith("{"):
        return None
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _ask_watch(call: Call, event: dict[str, Any]) -> str | None:
    """監視を呼ぶ。**監視の誤りでステージを落とさない**ので、例外は飲む。"""
    if call.watch is None:
        return None
    try:
        return call.watch(event)
    except Exception:
        return None


class _Collector:
    """流れてきたイベントから、ステージ 1 回の結果を組み立てる。"""

    def __init__(self, stage: str, log_path: str, session_id: str | None) -> None:
        self.stage = stage
        self.log_path = log_path
        self.session_id = session_id
        self.final: dict[str, Any] = {}
        self.capabilities: list[str] = []
        self.events = 0
        self.blocked = 0
        self.aborted: str | None = None

    @property
    def can_cancel_queued(self) -> bool:
        return "interrupt_cancel_queued_v1" in self.capabilities

    def abort(self, reason: str) -> None:
        self.aborted = self.aborted or reason

    def take(self, event: dict[str, Any]) -> None:
        self.events += 1
        self.session_id = event.get("session_id") or self.session_id
        kind = event.get("type")
        if kind == "result":
            self.final = event
        elif event.get("subtype") == "init":
            self.capabilities = list(event.get("capabilities") or [])
        else:
            self.blocked += events.guard_denials(event)

    def build(self, code: int, err: str) -> Result:
        warnings: list[str] = []
        if self.blocked:
            warnings.append(f"フックが {self.blocked} 回止めた")
        for denial in self.final.get("permission_denials") or []:
            warnings.append(f"ツールを拒んだ: {json.dumps(denial, ensure_ascii=False)[:200]}")

        detail = ""
        if self.final.get("is_error"):
            detail = str(
                self.final.get("result") or self.final.get("api_error_status") or ""
            ).strip()[:500]

        structured = self.final.get("structured_output")
        return Result(
            stage=self.stage,
            code=code,
            session_id=self.session_id,
            result=structured if isinstance(structured, dict) else None,
            text=str(self.final.get("result") or "").strip(),
            usage=self.final.get("usage") or {},
            reason=self.final.get("subtype") if self.final else None,
            log=self.log_path,
            error=detail or (err[:2000] if not self.final else None),
            events=self.events,
            turns=int(self.final.get("num_turns") or 0),
            cost=float(self.final.get("total_cost_usd") or 0.0),
            aborted=self.aborted,
            deferred=(
                self.final.get("deferred_tool_use")
                if self.final.get("stop_reason") == "tool_deferred"
                else None
            ),
            capabilities=self.capabilities,
            warnings=warnings,
        )


def usage_line(got: Result) -> str:
    """進行の表示に出す 1 行。**ステージごとの固定費を目で追えるようにする。**"""
    usage = got.usage
    if not usage:
        return "usage なし"
    cached = usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    return (
        f"in {usage.get('input_tokens', 0):,} / cache {cached:,}"
        f" / out {usage.get('output_tokens', 0):,} / {got.turns} ターン / ${got.cost:.3f}"
    )
