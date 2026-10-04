"""GateEvaluator（`domain/services/gate.py`）と VerifySelector（`domain/services/verify.py`）。"""

from __future__ import annotations

import dataclasses

import pytest
from autodevlib.domain.services.gate import ChangedFile, GateEvaluator, GateEvidence
from autodevlib.domain.services.verify import VerifyScope, VerifySelector
from autodevlib.domain.value_objects.escalation_kind import EscalationKind
from autodevlib.domain.value_objects.finding_id import FindingId
from autodevlib.domain.value_objects.finding_summary import FindingSummary
from autodevlib.domain.value_objects.gate_item import GateItem
from autodevlib.domain.value_objects.rating import Rating
from autodevlib.domain.value_objects.stage_kind import StageKind
from autodevlib.domain.value_objects.verify_command import VerifyCommand
from autodevlib.domain.value_objects.verify_result import VerifyResult

PYTEST = VerifyCommand("uv run pytest -q")
RUFF = VerifyCommand("uv run ruff check .")

PASSING = GateEvidence(
    commits=2,
    open_findings=(),
    reviewers_expected=(StageKind.REVIEW, StageKind.ADVERSARIAL_REVIEW),
    reviewers_completed=(StageKind.ADVERSARIAL_REVIEW, StageKind.REVIEW),
    has_test_gen=True,
    verify=(VerifyResult(PYTEST, 0),),
)


#: TestGen の後でテストのファイルを変えた
TESTS_CHANGED = {"changed_since_tests": (ChangedFile("tests/test_a.py", is_test=True),)}
#: テストが要らないパスの外を変えた
OUTSIDE_UNTESTED = {"changed": (ChangedFile("src/a.py"),)}


def evaluate(**changes):
    return GateEvaluator.evaluate(dataclasses.replace(PASSING, **changes))


def failed_items(**changes) -> list[GateItem]:
    return [result.item for result in evaluate(**changes).report.failed]


# --- 項目 ---


def test_証拠が揃えば通りTestGenの有無で見る項目が変わる():
    outcome = GateEvaluator.evaluate(PASSING)
    assert outcome.passed
    assert [r.item for r in outcome.report.items] == [
        GateItem.COMMITS,
        GateItem.NO_OPEN_FINDINGS,
        GateItem.REVIEWERS_RAN,
        GateItem.TESTS_UNCHANGED,
        GateItem.VERIFY,
    ]
    without = evaluate(has_test_gen=False)
    assert GateItem.UNTESTED_PATHS in [r.item for r in without.report.items]
    assert GateItem.TESTS_UNCHANGED not in [r.item for r in without.report.items]


@pytest.mark.parametrize(
    ("changes", "item"),
    [
        ({"commits": 0}, GateItem.COMMITS),
        ({"open_findings": (FindingId("R2"),)}, GateItem.NO_OPEN_FINDINGS),
        ({"reviewers_completed": (StageKind.REVIEW,)}, GateItem.REVIEWERS_RAN),
        (TESTS_CHANGED, GateItem.TESTS_UNCHANGED),
        ({"has_test_gen": False, **OUTSIDE_UNTESTED}, GateItem.UNTESTED_PATHS),
        ({"verify": (VerifyResult(PYTEST, 0), VerifyResult(RUFF, 1))}, GateItem.VERIFY),
    ],
    ids=lambda v: v.value if isinstance(v, GateItem) else "",
)
def test_項目ごとに落ちる(changes: dict, item: GateItem):
    assert failed_items(**changes) == [item]


def test_TestGenがあればテストが要らないパスの外を変えてもよい():
    assert failed_items(**OUTSIDE_UNTESTED) == []


def test_TestGenが無ければテストを変えてもよい():
    assert failed_items(has_test_gen=False, **TESTS_CHANGED) == []


def test_照合の答えで見るファイルを絞る():
    # TestGen の後で変わってもテストでなければ、テストが要らないパスに収まれば、落ちない
    assert failed_items(changed_since_tests=(ChangedFile("src/a.py"),)) == []
    docs = (ChangedFile("docs/a.md", untested_ok=True),)
    assert failed_items(has_test_gen=False, changed=docs) == []


def test_verifyが空なら通る():
    assert failed_items(verify=()) == []


def test_落ちた理由にコマンドと終了コードとidを書く():
    outcome = evaluate(
        open_findings=(FindingId("R2"), FindingId("G-verify")),
        verify=(VerifyResult(RUFF, 1),),
    )
    reasons = {result.item: result.reason for result in outcome.report.failed}
    assert reasons[GateItem.NO_OPEN_FINDINGS] == "open の指摘: R2"
    assert reasons[GateItem.VERIFY] == "uv run ruff check . が終了コード 1 で落ちた"


def test_Gateが自分で判定するG_の指摘は残っている指摘に数えない():
    # 前の Gate が開いた G- の指摘で、次の Gate が必ず落ちることが無い
    assert (
        failed_items(open_findings=(FindingId("G-verify"), FindingId("G-no-open-findings"))) == []
    )


# --- 落ちた項目の扱い ---


def test_直せる項目だけが落ちたらG_の指摘を開き上げない():
    outcome = evaluate(open_findings=(FindingId("R2"),), verify=(VerifyResult(RUFF, 2),))
    assert outcome.escalation is None
    assert [(f.finding, f.rating) for f in outcome.findings] == [
        (FindingId("G-no-open-findings"), Rating.MUST_FIX),
        (FindingId("G-verify"), Rating.MUST_FIX),
    ]


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"commits": 0}, EscalationKind.GATE_UNFIXABLE),
        ({"reviewers_completed": ()}, EscalationKind.GATE_UNFIXABLE),
        (TESTS_CHANGED, EscalationKind.GATE_UNFIXABLE),
        ({"has_test_gen": False, **OUTSIDE_UNTESTED}, EscalationKind.UNTESTED_CHANGE),
    ],
)
def test_直せない項目が落ちたら上げる(changes: dict, kind: EscalationKind):
    assert evaluate(**changes).escalation is kind


def test_直せない項目と直せる項目が両方落ちたら上げる方を取る():
    outcome = evaluate(commits=0, verify=(VerifyResult(RUFF, 1),))
    assert outcome.escalation is EscalationKind.GATE_UNFIXABLE
    # 上げるので GateFailed にならず、G- の指摘も開かない
    assert outcome.findings == ()
    # 項目ごとの合否から直せる項目を引くことはできる（台帳の RecordGateResult が使う）
    assert [f.finding for f in GateEvaluator.findings_for(outcome.report.failed)] == [
        FindingId("G-verify")
    ]


def test_通った項目からは何も開かない():
    assert GateEvaluator.findings_for(GateEvaluator.evaluate(PASSING).report.items) == ()
    assert GateEvaluator.escalation_for(GateEvaluator.evaluate(PASSING).report.items) is None


def test_GateFailedの項目から開く指摘の中身():
    (result,) = evaluate(verify=(VerifyResult(RUFF, 1),)).report.failed
    assert GateEvaluator.findings_for((result,)) == (
        FindingSummary(FindingId("G-verify"), Rating.MUST_FIX, result.reason),
    )


# --- VerifySelector ---


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (StageKind.CONFIRM_RED, (PYTEST,)),
        (StageKind.GATE, (PYTEST,)),
        (StageKind.VERIFY, (RUFF,)),
    ],
)
def test_実装タスクはそのタスクのverifyをgit管理タスクはラン共通のverifyを流す(
    stage: StageKind, expected: tuple[VerifyCommand, ...]
):
    assert VerifySelector.select(stage, (PYTEST,), (RUFF,)) == expected


def test_他のタスクのverifyを積み上げず空なら空のまま():
    assert VerifySelector.select(StageKind.GATE, (), (RUFF, PYTEST)) == ()
    assert VerifySelector.select(StageKind.VERIFY, (PYTEST,), ()) == ()


def test_検証コマンドを流さないステージには選ばない():
    assert VerifySelector.scope_of(StageKind.IMPL) is None
    assert VerifySelector.scope_of(StageKind.VERIFY) is VerifyScope.RUN
    with pytest.raises(ValueError, match="Impl は検証コマンドを流さない"):
        VerifySelector.select(StageKind.IMPL, (PYTEST,), (RUFF,))
