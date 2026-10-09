"""T6：肝功能扩展域可解读（PRD #239）。

外部可观察行为：报告项名 → canonical 指标 → 健康问题；以及**既有归属不变**。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.disposition import UNKNOWN_METRIC
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.integrations.health_flow import build_evidence_request

LIVER_CONDITION = "COND_LIVER_FUNCTION_PATTERN"

REPORT_NAMES = {
    "Total Bilirubin": "total_bilirubin",
    "TBIL": "total_bilirubin",
    "Direct Bilirubin": "direct_bilirubin",
    "DBIL": "direct_bilirubin",
    "Indirect Bilirubin": "indirect_bilirubin",
    "IBIL": "indirect_bilirubin",
    "Total Protein": "total_protein",
    "TP": "total_protein",
    "Globulin": "globulin",
    "GLB": "globulin",
    "Albumin/Globulin ratio": "albumin_globulin_ratio",
    "A/G": "albumin_globulin_ratio",
    "Alkaline Phosphatase": "alp",
}


@pytest.mark.parametrize(("report_name", "expected"), sorted(REPORT_NAMES.items()))
def test_liver_panel_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_new_liver_metrics_reach_the_liver_condition() -> None:
    for metric_code in (
        "total_bilirubin",
        "direct_bilirubin",
        "indirect_bilirubin",
        "total_protein",
        "globulin",
        "albumin_globulin_ratio",
    ):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
        assert LIVER_CONDITION in claims, metric_code


def test_alkaline_phosphatase_keeps_its_existing_owner() -> None:
    """AC：既有归属逐条未变。`alp` 仍归首批骨质疏松，本片只补别名。"""

    claims = {condition.code for condition in CONDITIONS_BY_METRIC["alp"]}
    assert claims == {"COND_OSTEOPOROSIS_RISK"}
    assert METRIC_ALIASES[normalize_metric_name("Alkaline Phosphatase")] == "alp"


def test_masld_keeps_its_first_batch_metrics() -> None:
    """本片不把扩展项塞进 MASLD：它是首批条目，且是具体疾病不是泛肝功能。"""

    assert CONDITION_BY_CODE["COND_MASLD_RISK"].metrics == (
        "alt",
        "ast",
        "ggt",
        "fasting_glucose",
        "triglycerides",
    )


def test_adapter_resolves_a_liver_panel_row() -> None:
    result = build_evidence_request(
        [
            {
                "metric_name": "TBIL",
                "metric_value": "30",
                "unit": "μmol/L",
                "reference_range": "3.4-28.0",
                "evidence_text": "TBIL 30 μmol/L 参考范围 3.4-28.0 H",
                "page_number": 2,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["total_bilirubin"]


def test_dimensionless_ratio_survives_the_unit_gate() -> None:
    """`A/G` 无量纲，报告上不印单位；它必须过闸门而不是被丢弃。

    实测该比值在报告里恒为空单位。丢弃等于「有值有参考区间却当作没有数据」。
    """

    result = build_evidence_request(
        [
            {
                "metric_name": "A/G",
                "metric_value": "0.9",
                "unit": "",
                "reference_range": "1.2-2.4",
                "evidence_text": "A/G 0.9 参考范围 1.2-2.4 L",
                "page_number": 2,
            }
        ],
        confirmed=True,
    )

    observations = list(result.request.observations)
    assert [item.metric_code for item in observations] == ["albumin_globulin_ratio"]
    assert observations[0].unit == "1"
    assert result.skipped == ()


def test_unitless_non_dimensionless_rows_are_still_gated() -> None:
    """放宽只针对无量纲指标；别的指标缺单位仍应显式丢弃。"""

    result = build_evidence_request(
        [
            {
                "metric_name": "Urea",
                "metric_value": "9",
                "unit": "",
                "reference_range": "2.5-8.0",
                "evidence_text": "Urea 9 参考范围 2.5-8.0 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert list(result.request.observations) == []
    assert result.skipped[0]["reason"] == UNKNOWN_METRIC


def test_immunoglobulin_is_not_total_globulin() -> None:
    """`globulin` 不得在 `Immunoglobulin G` 里命中——总球蛋白与免疫球蛋白是两回事。"""

    from genesis_evidence.review.scope import EvidenceProfileScopeResolver

    resolver = EvidenceProfileScopeResolver()

    assert not resolver.metric_outcome_matches_text("globulin", "Immunoglobulin G")
    assert not resolver.metric_outcome_matches_text("globulin", "Immunoglobulin A")
    assert resolver.metric_outcome_matches_text("globulin", "Globulin")
    assert resolver.metric_outcome_matches_text("globulin", "serum globulin")
