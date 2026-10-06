from pathlib import Path

from genesis_evidence.core.patient_copy import FORBIDDEN_PATIENT_TERMS

ROOT = Path(__file__).parents[1]

ACCEPTANCE_SCENARIOS = (
    "已确认风险返回已发布证据且不含商品",
    "未发布知识卡不进入患者侧",
    "边界观测如实返回",
    "患者文案不触发禁用词",
)

QA_CASE_IDS = {
    "QA-EVID-001",
    "QA-EVID-002",
    "QA-EVID-003",
    "QA-EVID-004",
    "QA-DISEASE-001",
    "QA-WORKBENCH-001",
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
    "0005-review-workbench-independent-reading-regions.md",
    "0006-retire-product-capability-and-move-goods-authority-to-the-mall.md",
    "0007-component-centric-evidence.md",
)

COMPONENT_AXIS_TERMS = (
    "成分 / component",
    "`component_key`",
    "成分形式 / component form",
    "确定性判定 / determinacy",
    "非可合并 / not poolable",
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


def test_acceptance_feature_covers_the_evidence_contract() -> None:
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


def test_context_glossary_covers_the_evidence_terms() -> None:
    context = _read("GLOSSARY.md")

    for term in (
        "体检报告解读与健康风险提示",
        "健康风险提示",
        "condition_code",
        "evidence_items",
        "published",
        "patient_visible_body",
        "action_message",
        "content_layer",
        "action_status",
        "已移到商城",
    ):
        assert term in context


def test_context_pins_the_component_axis_vocabulary() -> None:
    context = _read("GLOSSARY.md")

    for term in COMPONENT_AXIS_TERMS:
        assert term in context, f"component-axis term not pinned: {term}"


def test_context_is_glossary_only_and_adrs_are_separate() -> None:
    context = _read("GLOSSARY.md")

    assert "## Decision" not in context
    assert "## 数据与迁移" not in context
    for name in ADR_NAMES:
        assert (ROOT / "docs/adr" / name).is_file()


def test_adrs_record_decision_alternatives_and_rationale() -> None:
    for name in ADR_NAMES:
        text = _read(f"docs/adr/{name}")
        for heading in ("## Decision", "## Alternatives Considered", "## Rationale"):
            assert heading in text, f"{name} missing {heading}"
