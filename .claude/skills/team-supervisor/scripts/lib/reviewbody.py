"""レビューコメント本文の組み立てと解析、および役割・重大度・状態語の検査。

GitHub を呼ばない。「どんな文字列を投稿するか」と「投稿された文字列をどう読むか」だけを
持つ。取得と投稿は reviewthreads.py と gh-review.py が行う。

投稿する本文の 1 行目には、GitHub 上で表示されない HTML コメントの形で機械可読な
メタデータ（`<!-- team-supervisor {...} -->`）を埋める。役割・重大度をここから読むので、
表示用の `**[役割]**` タグの書式を変えてもパースが壊れない。
"""

import json
import re

from .shell import die

SEVERITIES = ("must-fix", "should-fix", "nit")
VERDICTS = ("approved", "changes-requested")

# 本文の 1 行目に埋める隠しメタデータ。GitHub は HTML コメントを表示しない
META_PREFIX = "<!-- team-supervisor "
META_SUFFIX = " -->"
META_RE = re.compile(r"<!--\s*team-supervisor\s+(\{.*?\})\s*-->", re.DOTALL)

# 役割の接頭辞ごとに使ってよい状態語
STATUS_BY_ROLE = {
    "impl": ("fixed", "partial", "wont-fix", "disputed", "deferred"),
    "subleader": ("upheld", "overruled"),
    "review": ("still-open", "resolved"),
    "lead": ("note",),
}


def check_role(role):
    """役割タグの接頭辞を検査して返す。使えない接頭辞なら止める。"""
    prefix = role.split(":", 1)[0]
    if prefix not in STATUS_BY_ROLE:
        die(
            f"役割 '{role}' は使えません。接頭辞は "
            + " / ".join(sorted(STATUS_BY_ROLE))
            + " のいずれかにしてください（例: review:normal, impl:a, subleader:task4）"
        )
    return prefix


def meta_line(**fields):
    """本文の 1 行目に置く隠しメタデータ。

    表示用の `**[役割]**` タグを文字列一致でパースしていた形から移した。タグは人間が
    PR 画面で誰の指摘かを読むために残すが、機械が読むのはこの行である。
    """
    payload = {k: v for k, v in fields.items() if v is not None}
    return META_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + META_SUFFIX


def parse_meta(body):
    """本文から隠しメタデータを取り出す。無ければ空の dict。"""
    if not body:
        return {}
    match = META_RE.search(body)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def finding_body(role, finding):
    """指摘スレッドの先頭コメント。"""
    severity = finding.get("severity")
    if severity not in SEVERITIES:
        die(f"severity は {' / '.join(SEVERITIES)} のいずれかにしてください: {severity!r}")
    category = finding.get("category") or "general"
    lines = [
        meta_line(kind="finding", role=role, severity=severity, category=category),
        f"**[{role}]** `{severity}` / {category}",
        "",
        finding.get("body", "").rstrip(),
    ]
    if finding.get("evidence"):
        lines += ["", f"**根拠**: {finding['evidence']}"]
    if finding.get("suggestion"):
        lines += ["", f"**提案**: {finding['suggestion']}"]
    if finding.get("target"):
        lines += ["", f"**本来の対象**: {finding['target']}"]
    return "\n".join(lines).rstrip() + "\n"


def summary_body(role, verdict, counts, extra):
    """レビューのサマリ本文。gate はここに埋めた role を数えて提出の有無を判定する。"""
    if verdict not in VERDICTS:
        die(f"verdict は {' / '.join(VERDICTS)} のいずれかにしてください: {verdict!r}")
    lines = [
        meta_line(kind="review", role=role, verdict=verdict, counts=counts),
        f"**[{role}]** verdict: `{verdict}`",
        "",
        "- must-fix: {must-fix} / should-fix: {should-fix} / nit: {nit}".format(**counts),
    ]
    if extra:
        lines += ["", extra.rstrip()]
    return "\n".join(lines).rstrip() + "\n"


def reply_body(role, status, message, commit):
    """スレッドへの返信本文。"""
    lines = [
        meta_line(kind="reply", role=role, status=status, commit=commit),
        f"**[{role}]** `{status}` — {message}",
    ]
    if commit:
        lines += ["", f"commit: {commit}"]
    return "\n".join(lines).rstrip() + "\n"


def thread_meta(comment_nodes):
    """スレッドの先頭コメントから severity と role を取り出す。

    隠しメタデータを優先し、無ければ表示用タグの 1 行目を文字列一致で読む
    （メタデータを入れる前に投稿されたスレッドと、手書きのコメントのため）。
    どちらでも読めなければ None。
    """
    if not comment_nodes:
        return None, None
    body = comment_nodes[0].get("body") or ""

    meta = parse_meta(body)
    if meta.get("severity") in SEVERITIES or meta.get("role"):
        severity = meta.get("severity")
        return (severity if severity in SEVERITIES else None), meta.get("role")

    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("<!--")]
    first_line = lines[0] if lines else ""
    severity = next((s for s in SEVERITIES if f"`{s}`" in first_line), None)
    role_match = re.search(r"\*\*\[([^\]]+)\]\*\*", first_line)
    return severity, (role_match.group(1) if role_match else None)
