from pathlib import Path

ROOT = Path(__file__).parents[1]
ADR_DIR = ROOT / "docs" / "adr"


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.is_file(), f"missing governance artifact: {relative_path}"
    return path.read_text(encoding="utf-8")


def test_acceptance_feature_covers_product_recommendation_contract() -> None:
    feature = _read("acceptance.feature")

    assert feature.startswith("Feature:")
    for scenario in (
        "已发布风险展示已发布推荐",
        "未发布或带风险标的产品不出现",
        "紧急或高危风险项抑制推荐",
        "推荐文案不触发患者禁用词",
    ):
        assert scenario in feature


def test_qa_plan_cases_have_required_fields_and_an_end_to_end_case() -> None:
    plan = _read("qa-plan.md")
    case_blocks = [block for block in plan.split("### ") if block.startswith("QA-")]
    required_fields = (
        "**ID**",
        "**环境**",
        "**前置**",
        "**数据**",
        "**动作**",
        "**可观察结果**",
        "**清理**",
    )

    assert case_blocks, "qa-plan.md must define at least one QA case"
    for block in case_blocks:
        for field in required_fields:
            assert field in block, f"case missing required field {field}: {block.splitlines()[0]}"
    assert any(block.lstrip().startswith("QA-E2E-") for block in case_blocks)


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
        "recommendations",
    ):
        assert term in context


def test_product_recommendation_adrs_record_decision_alternatives_and_rationale() -> None:
    adr_names = (
        "0001-unfreeze-phase-2-product-recommendations.md",
        "0002-one-time-product-catalog-migration-and-self-governance.md",
        "0003-anchor-recommendations-under-confirmed-findings.md",
        "0004-four-product-seed-pool-publication.md",
    )

    for name in adr_names:
        text = _read(f"docs/adr/{name}")
        for heading in ("## Decision", "## Alternatives Considered", "## Rationale"):
            assert heading in text, f"{name} missing {heading}"
