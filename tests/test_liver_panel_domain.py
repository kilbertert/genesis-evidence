"""T6：肝功能扩展域可解读（PRD #239）。

外部可观察行为：报告项名 → canonical 指标 → 健康问题；以及**既有归属不变**。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
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
