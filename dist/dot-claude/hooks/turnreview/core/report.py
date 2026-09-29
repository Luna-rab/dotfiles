"""何を報告済みとし、Claude に何の形で返すか。

**報告済みの鍵には本文を丸ごと hash して入れる。** 中身が変われば再び上がる。行番号は入れない。
上に行を足してずれただけで再び上がらないようにするため。

`decision: "block"` ではなく `additionalContext` で返す。ループ防止（`stop_hook_active` と
8 連続打ち切り）は同じで、transcript の表示が hook エラーではなく feedback になる。
SubagentStop でもサブエージェントが続きを書く（実測）。

**サブエージェントには、見直しのあとに元の報告を書き直させる。** 親に渡るのは
サブエージェントの最後のメッセージだけなので、見直しの報告で終えると本来の報告が消える。
"""

from __future__ import annotations

import hashlib

from turnreview.core.turn import is_subagent

SUBAGENT_RULE = (
    "見直しを書いたあと、元の最終報告をもう一度そのまま書いてください。"
    "サブエージェントなので、呼び出し元には最後のメッセージしか届きません。"
)


def report_key(kind: str, name: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{kind}\0{name}\0{digest}"


def session_digest(session_id: str) -> str:
    """報告済みの記録を置くファイル名。セッション id をそのままパスに入れない。"""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def notes_for(payload: dict) -> tuple[str, ...]:
    """呼び出しの場面で足す注意。SubagentStop なら、元の報告を書き直させる一文。"""
    return (SUBAGENT_RULE,) if is_subagent(payload) else ()


def hook_output(payload: dict, message: str) -> dict:
    """stdout に出す JSON。"""
    return {
        "hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name", "Stop"),
            "additionalContext": message,
        }
    }
