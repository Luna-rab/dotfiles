"""検査で `claude -p` の代わりに起動する偽物。本物は叩かない。

`FAKE_CLAUDE_SCENARIO` で振る舞いを選び、受けた引数・環境変数・標準入力を `FAKE_CLAUDE_RECORD` に
JSON で書く。流すイベントの形は LEDGER の AR- と HK-20・AR-26 の行に合わせてある。
"""

from __future__ import annotations

import json
import os
import sys
import time


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def init() -> None:
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "x",
            "capabilities": ["interrupt_cancel_queued_v1"],
        }
    )


def result(**fields: object) -> None:
    body = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "result": "done",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 7,
        },
        "total_cost_usd": 0.25,
        "permission_denials": [],
    }
    body.update(fields)
    emit(body)


received: list[str] = []


def record() -> None:
    path = os.environ.get("FAKE_CLAUDE_RECORD")
    if not path:
        return
    keep = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "AUTODEV_GUARD",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_CONFIG_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
    )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "argv": sys.argv[1:],
                "cwd": os.getcwd(),
                "env": {k: os.environ[k] for k in keep if k in os.environ},
                "oauth": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"),
                "stdin": received,
            },
            fh,
            ensure_ascii=False,
        )


def read_line() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    received.append(line)
    record()
    return json.loads(line)


def drain() -> None:
    while read_line() is not None:
        pass


def success() -> None:
    read_line()
    init()
    emit(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "tool_use", "id": "t1", "name": "Edit", "input": {}}],
            },
        }
    )
    emit(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "is_error": True,
                        "content": "PreToolUse:Edit hook error: [python3 x]: 止めた",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "t0",
                        "is_error": True,
                        "content": [{"type": "text", "text": "PreToolUse:Bash hook error: 止めた"}],
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "t2",
                        "is_error": True,
                        "content": "ふつうの失敗",
                    },
                ]
            },
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "id": "m2",
                "content": [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {}}],
            },
        }
    )
    emit({"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": "…"}]}})
    init()
    result(structured_output={"ok": True}, stop_reason="end_turn")
    drain()


def empty_structured() -> None:
    read_line()
    result(result="スキーマを満たせない")
    drain()


def deferred() -> None:
    # 再開ではプロンプトが来ない（標準入力はすぐ閉じる）
    drain()
    result(
        stop_reason="tool_deferred",
        deferred_tool_use={
            "id": "toolu_01",
            "name": "Bash",
            "input": {"command": "python3 /s/autodev.py ask --question 'どちら?'"},
        },
    )


def no_result() -> None:
    sys.stderr.write("error: unknown option '--bogus'\n")
    sys.exit(1)


def init_then_crash() -> None:
    """セッションを開いてから、result を返さずに落ちた。"""
    init()
    sys.stderr.write("panic: 落ちた\n")
    sys.exit(1)


def session_not_found() -> None:
    """`--resume` に見つからないセッションを渡した。claude 2.1.288 で確かめた形（プロンプトの有無で
    変わらない）。init を出さず、`result` の欄の無い result を返して終了コード 1 で終わる。"""
    session = sys.argv[sys.argv.index("--resume") + 1]
    message = f"No conversation found with session ID: {session}"
    emit(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "duration_ms": 0,
            "is_error": True,
            "num_turns": 0,
            "stop_reason": None,
            "session_id": session,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "permission_denials": [],
            "errors": [message],
        }
    )
    sys.stderr.write(message + "\n")
    sys.exit(1)


def max_turns() -> None:
    read_line()
    result(subtype="error_max_turns", is_error=True, terminal_reason="max_turns")
    drain()
    sys.exit(1)


def rate_limited() -> None:
    read_line()
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
    result(is_error=True, api_error_status=429, result="You've hit your limit")
    drain()
    sys.exit(1)


def rate_event_then_success() -> None:
    """上限の知らせが届いても、成功で終わったなら上限には当たっていない。"""
    read_line()
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
    result(result="rate limit の話をした")
    drain()


def interruptible() -> None:
    read_line()
    init()
    while True:
        message = read_line()
        if message is None:
            return
        if message.get("type") == "control_request":
            result(subtype="error_during_execution", is_error=True)
            drain()
            return


def stubborn() -> None:
    """interrupt を受けても result を返さない。"""
    read_line()
    init()
    while True:
        time.sleep(0.05)
        read_line()


def lingering() -> None:
    """result の後、標準入力を閉じても終わらない。"""
    read_line()
    result()
    time.sleep(60)


SCENARIOS = {
    "success": success,
    "empty-structured": empty_structured,
    "deferred": deferred,
    "no-result": no_result,
    "init-then-crash": init_then_crash,
    "session-not-found": session_not_found,
    "max-turns": max_turns,
    "rate-limited": rate_limited,
    "rate-event-then-success": rate_event_then_success,
    "interruptible": interruptible,
    "stubborn": stubborn,
    "lingering": lingering,
}


if __name__ == "__main__":
    record()
    SCENARIOS[os.environ.get("FAKE_CLAUDE_SCENARIO", "success")]()
