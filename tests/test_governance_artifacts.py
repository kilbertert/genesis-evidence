from pathlib import Path

from genesis_evidence.core.patient_copy import FORBIDDEN_PATIENT_TERMS

ROOT = Path(__file__).parents[1]

ACCEPTANCE_SCENARIOS = (
    "已发布风险展示已发布推荐",
    "未发布或带风险标的产品不出现",
    "紧急或高危风险项抑制推荐",
    "推荐文案不触发患者禁用词",
    "未匹配到安全已发布产品时显示暂无推荐",
)

QA_CASE_IDS = {
    "QA-PUB-001",
    "QA-EXCL-002",
    "QA-URG-003",
    "QA-FORBID-004",
    "QA-EMPTY-005",
    "QA-E2E-001",
    "QA-E2E-002",
}

QA_SCALAR_FIELDS = (
    "**ID**",
    "**环境**",
    "**前置**",
    "**数据**",
    "**可观察结果**",
    "**清理**",
)

ADR_NAMES = (
    "0001-unfreeze-phase-2-product-recommendations.md",
    "0002-one-time-product-catalog-migration-and-self-governance.md",
    "0003-anchor-recommendations-under-confirmed-findings.md",
    "0004-four-product-seed-pool-publication.md",
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _qa_cases(plan: str) -> dict[str, str]:
    cases: dict[str, str] = {}
    for block in plan.split("### ")[1:]:
        header = block.splitlines()[0].strip()
        case_id = header.split()[0]
        if case_id.startswith("QA-"):
            cases[case_id] = block
    return cases


def _field_value(block: str, field: str) -> str:
    prefix = f"- {field}: "
    for line in block.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def test_acceptance_feature_covers_product_recommendation_contract() -> None:
    feature = _read("acceptance.feature")

    assert feature.startswith("Feature:")
    for scenario in ACCEPTANCE_SCENARIOS:
        assert f"Scenario: {scenario}" in feature


def test_acceptance_forbidden_terms_match_patient_copy_guard() -> None:
    feature = _read("acceptance.feature")

    assert "、".join(FORBIDDEN_PATIENT_TERMS) in feature


def test_qa_plan_cases_have_complete_metadata() -> None:
    plan = _read("qa-plan.md")
    cases = _qa_cases(plan)

    assert set(cases) == QA_CASE_IDS
    for case_id, block in cases.items():
        for field in (*QA_SCALAR_FIELDS, "**动作**"):
            assert f"- {field}:" in block, f"{case_id} missing required field {field}"
        for field in QA_SCALAR_FIELDS:
            assert _field_value(block, field), f"{case_id} has an empty {field}"
        assert "\n  1. " in block, f"{case_id} has no ordered actions"


def test_qa_plan_end_to_end_case_is_executable() -> None:
    plan = _read("qa-plan.md")
    e2e = _qa_cases(plan)["QA-E2E-001"]

    assert "POST /api/evidence/matches" in e2e
    assert "confirmation_status=confirmed" in e2e
    assert "FORBIDDEN_PATIENT_TERMS" in e2e
    assert "GENESIS_EVIDENCE_REVIEWER_ID" in e2e


def test_context_glossary_covers_product_recommendation_terms() -> None:
    context = _read("CONTEXT.md")

    for term in (
        "体检报告解读与健康风险提示",
        "健康风险提示",
        "健康管理建议",
        "产品审核位",
        "blocked",
        "published",
        "high_risk_marketing_claim",
        "recommendations[]",
        "condition_code",
        "product_status",
        "patient_visible_body",
        "action_message",
        "blocked → in_review → published → withdrawn",
    ):
        assert term in context


def test_context_decision_links_match_adr_files() -> None:
    context = _read("CONTEXT.md")

    for name in ADR_NAMES:
        assert f"docs/adr/{name}" in context


def test_product_recommendation_adrs_record_decision_alternatives_and_rationale() -> None:
    for name in ADR_NAMES:
        text = _read(f"docs/adr/{name}")
        for heading in ("## Decision", "## Alternatives Considered", "## Rationale"):
            assert heading in text, f"{name} missing {heading}"
