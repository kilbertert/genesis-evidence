"""T5：尿常规域可解读（PRD #239）。

外部可观察行为：报告项名 → canonical 指标 → 健康问题；以及**不**把血象名字
误当尿检，也不把血清肾功名字拉进来。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.integrations.health_flow import build_evidence_request

URINARY_CONDITION = "COND_URINARY_ABNORMALITY"

REPORT_NAMES = {
    "Urine protein": "urine_protein",
    "Urine leucocytes": "urine_leucocytes",
    "Urine leucocytes microscopy": "urine_leucocytes",
    "Leucocytes": "urine_leucocytes",
    "Leucocytes (microscopy)": "urine_leucocytes",
    "Urine erythrocytes": "urine_erythrocytes",
    "Erythrocytes": "urine_erythrocytes",
    "Erythrocytes (microscopy)": "urine_erythrocytes",
    "Specific Gravity": "urine_specific_gravity",
    "SG": "urine_specific_gravity",
    "Urine pH": "urine_ph",
    "pH": "urine_ph",
}


@pytest.mark.parametrize(("report_name", "expected"), sorted(REPORT_NAMES.items()))
def test_urinalysis_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_blood_red_cell_count_is_not_urine_erythrocytes() -> None:
    """`RBC` 是 x10¹²/L 的血红细胞计数，不是尿红细胞。

    报告里 `RBC` 与 `Erythrocytes` 并存且单位不同（10¹²/L 血 vs 10⁶/L 尿），
    把前者当尿检会把血象挂到泌尿问题上。
    """

    assert METRIC_ALIASES.get(normalize_metric_name("RBC")) is None


def test_serum_renal_markers_are_not_pulled_into_urinalysis() -> None:
    """`Urea`/`BUN`/`CREA`/`UA` 是血清肾功，不是尿常规，本片不碰。"""

    for name in ("Urea", "BUN", "CREA", "UA"):
        assert METRIC_ALIASES.get(normalize_metric_name(name)) is None, name


def test_every_urinalysis_metric_reaches_the_urinary_condition() -> None:
    for metric_code in (
        "urine_protein",
        "urine_leucocytes",
        "urine_erythrocytes",
        "urine_specific_gravity",
        "urine_ph",
    ):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
        assert URINARY_CONDITION in claims, metric_code


def test_adapter_resolves_a_urinalysis_row() -> None:
    result = build_evidence_request(
        [
            {
                "metric_name": "Urine leucocytes",
                "metric_value": "25",
                "unit": "x 10^6/L",
                "reference_range": "0-10",
                "evidence_text": "Urine leucocytes 25 x 10^6/L 参考范围 0-10 H",
                "page_number": 2,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["urine_leucocytes"]


def test_unitless_urinalysis_rows_are_dropped_upstream_not_mismapped() -> None:
    """`SG`/`pH` 在报告里**没有单位**，适配器要求单位，于是它们被丢弃。

    这不是本片能修的：它是适配器的单位闸门，不是名称解析问题。如实钉住这个
    现状，免得有人以为这两个指标已经端到端可用。丢弃是显式的（落
    `missing_unit`），不是静默错配。
    """

    result = build_evidence_request(
        [
            {
                "metric_name": "SG",
                "metric_value": "1.030",
                "unit": "",
                "reference_range": "1.004-1.030",
                "evidence_text": "SG 1.030 参考范围 1.004-1.030 H",
                "page_number": 2,
            }
        ],
        confirmed=True,
    )

    assert list(result.request.observations) == []
    assert result.skipped[0]["reason"] == "missing_unit"
