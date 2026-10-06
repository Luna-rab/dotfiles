"""ステージの結果の読み替え（`domain/stages/results.py`）。組で書く欄の片方だけの値を、形の誤りとして拒む。"""

from __future__ import annotations

from typing import Any

import pytest
from autodev_samples import stage_result
from autodevlib.domain.stages.catalog import STAGE_SPECS
from autodevlib.domain.stages.results import has_report, parse_result, read_report
from autodevlib.domain.value_objects.artifact_kind import ArtifactKind
from autodevlib.domain.value_objects.artifact_ref import ArtifactRef
from autodevlib.domain.value_objects.base import InvalidValue
from autodevlib.domain.value_objects.design_cause import DesignCause
from autodevlib.domain.value_objects.design_judgement import DesignJudgement
from autodevlib.domain.value_objects.design_version import DesignVersion
from autodevlib.domain.value_objects.escalation_kind import EscalationKind
from autodevlib.domain.value_objects.evidence import Evidence
from autodevlib.domain.value_objects.stage_exit import StageExit
from autodevlib.domain.value_objects.stage_kind import StageKind
from autodevlib.domain.value_objects.stall_cause import StallCause
from autodevlib.domain.value_objects.verify_command import VerifyCommand

S = StageKind
EVIDENCE = Evidence(exit=StageExit.OK, result_valid=True)


def parse(stage: StageKind, **fields: Any):
    return parse_result(STAGE_SPECS[stage], stage_result(stage, **fields), EVIDENCE)


def test_報告とその中身は組で書く():
    spec = STAGE_SPECS[S.IMPL]
    reported = stage_result(S.IMPL, report="design-gap", reportReason="受入条件と設計が違う")
    assert has_report(spec, reported)
    assert read_report(spec, reported) == (EscalationKind.DESIGN_GAP, "受入条件と設計が違う")
    for reason in (None, "", " "):
        with pytest.raises(InvalidValue, match="reportReason に中身を書く"):
            read_report(spec, stage_result(S.IMPL, report="design-gap", reportReason=reason))
    with pytest.raises(InvalidValue, match="report が無いのに reportReason がある"):
        parse(S.IMPL, reportReason="理由だけ")


def test_停滞の分類とその理由は組で書く():
    result = parse(S.JUDGE, stallCause="tests", stallReason="テストが受入条件と合わない")
    assert (result.stall_cause, result.stall_reason) == (
        StallCause.TESTS,
        "テストが受入条件と合わない",
    )
    assert parse(S.JUDGE).stall_reason is None
    for fields in ({"stallCause": "tests"}, {"stallReason": "理由だけ"}):
        with pytest.raises(InvalidValue, match="両方書くか両方 null"):
            parse(S.JUDGE, **fields)


def cause(kind: str, reverted: int | None = None, question: str | None = None) -> dict:
    return {"kind": kind, "revertedTo": reverted, "reason": "見た", "question": question}


def test_設計の分類は理由を持ち曖昧なときだけ問いを持つ():
    ambiguous = parse(S.DESIGN_JUDGE, designCause=cause("ambiguous", question="A か B か"))
    assert ambiguous.design_cause == DesignJudgement(
        DesignCause.AMBIGUOUS, reason="見た", question="A か B か"
    )
    reverted = parse(S.DESIGN_JUDGE, designCause=cause("reverted", reverted=1))
    assert reverted.design_cause == DesignJudgement(
        DesignCause.REVERTED, DesignVersion(1), reason="見た"
    )
    with pytest.raises(InvalidValue, match="question を書くのは"):
        parse(S.DESIGN_JUDGE, designCause=cause("ambiguous"))
    with pytest.raises(InvalidValue, match="question を書くのは"):
        parse(S.DESIGN_JUDGE, designCause=cause("reverted", reverted=1, question="?"))


# --- 計画の提案の検証コマンド ---

PROPOSED = Evidence(
    exit=StageExit.OK,
    result_valid=True,
    products=(ArtifactRef(ArtifactKind.PROPOSAL, "1"),),
)


def planned_task(**fields: Any) -> dict:
    return {
        "id": "task1",
        "title": "キャッシュ",
        "dod": "",
        "acceptance": [],
        "scope": [],
        "entryPoints": [],
        "boundary": "",
        "taskTests": [],
        "blockedBy": [],
        **fields,
    }


def plan_result(stage: StageKind, **fields: Any) -> dict:
    return {
        "design": "# 設計\n",
        "tasks": [planned_task(taskTests=["pytest a"])],
        "quickChecks": ["ruff"],
        "regressionTests": ["pytest"],
        "decisions": [],
        "deferrals": [],
        **({"codemap": "地図"} if stage is S.PLAN else {"stop": [], "discard": [], "carry": []}),
        **({"comments": []} if stage is S.REVISE else {}),
        **fields,
    }


@pytest.mark.parametrize("stage", [S.PLAN, S.REPLAN, S.REVISE])
def test_提案の軽い検査と回帰テストとタスクのテストを読む(stage: StageKind):
    result = parse_result(STAGE_SPECS[stage], plan_result(stage), PROPOSED)
    proposal = result.proposal
    assert proposal is not None
    assert proposal.quick_checks == (VerifyCommand("ruff"),)
    assert proposal.regression_tests == (VerifyCommand("pytest"),)
    assert proposal.tasks[0].spec.task_tests == (VerifyCommand("pytest a"),)
