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
}


def test_bare_ph_is_not_mapped_it_is_specimen_ambiguous() -> None:
    """裸 `pH` 不登记：血气分析也报 pH，报告上都不带单位，名字不决定标本来源。

    没有标本字段可查时就只能 fail-closed——否则一份血气 pH 会产出泌尿系统
    finding。带标本前缀的 `Urine pH` 仍正常解析。
    """

    assert METRIC_ALIASES.get(normalize_metric_name("pH")) is None
    assert METRIC_ALIASES.get(normalize_metric_name("Urine pH")) == "urine_ph"


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


def test_unitless_urinalysis_rows_now_reach_the_observation() -> None:
    """`SG`/`pH` 无量纲，报告上不印单位；T6 起它们过闸门而不再被丢弃。

    先前这里钉的是「被丢弃」——那是适配器把「没有单位」与「缺数据」混为一谈。
    T6 给无量纲指标补了规范单位，本片随之修正。
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

    observations = list(result.request.observations)
    assert [item.metric_code for item in observations] == ["urine_specific_gravity"]
    assert observations[0].unit == "1"
    assert result.skipped == ()
