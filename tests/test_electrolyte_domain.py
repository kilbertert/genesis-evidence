"""T3：电解质域可解读（PRD #239）。

可观察行为：把报告上的电解质项目名喂进适配器，它落到的 canonical 指标与
对应的健康问题；以及该域之外的项目名**没有**被顺带纳入。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.integrations.health_flow import build_evidence_request

ELECTROLYTE_CONDITION = "COND_ELECTROLYTE_DISTURBANCE"

#: 真实报告里出现过的电解质项目名 -> 预期 canonical 指标。
#: 短代号来自报告（`Na`/`K`/`Cl`/`Ca`），全称由 canonical 码派生。
REPORT_NAMES = {
    "Na": "sodium",
    "K": "potassium",
    "Cl": "chloride",
    "Ca": "calcium",
    "Phosphate": "phosphate",
    "Corrected Calcium": "corrected_calcium",
    "Sodium": "sodium",
    "Potassium": "potassium",
    "Chloride": "chloride",
}


@pytest.mark.parametrize(("report_name", "expected"), sorted(REPORT_NAMES.items()))
def test_electrolyte_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_every_electrolyte_metric_reaches_the_electrolyte_condition() -> None:
    """每个电解质指标都要挂在电解质健康问题下，否则报告认得它也没话可说。"""

    for metric_code in ("sodium", "potassium", "chloride", "phosphate", "calcium"):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
        assert ELECTROLYTE_CONDITION in claims, metric_code


def test_calcium_is_total_not_albumin_corrected() -> None:
    """报告上的 `Ca` 是总钙；校正钙是另一项，两者不能混。"""

    assert METRIC_ALIASES[normalize_metric_name("Ca")] == "calcium"
    assert METRIC_ALIASES[normalize_metric_name("Corrected Calcium")] == "corrected_calcium"
    assert "calcium" in CONDITION_BY_CODE[ELECTROLYTE_CONDITION].metrics
    assert "corrected_calcium" in CONDITION_BY_CODE[ELECTROLYTE_CONDITION].metrics


def test_adapter_resolves_an_electrolyte_row() -> None:
    """端到端：报告的英文项名经适配器变成 canonical 指标，不再落 unknown_metric。"""

    result = build_evidence_request(
        [
            {
                "metric_name": "Na",
                "metric_value": "150.0",
                "unit": "mmol/L",
                "reference_range": "137-147",
                "evidence_text": "Na 150.0 mmol/L 参考范围 137-147 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["sodium"]
    assert result.skipped == ()


def test_adapter_still_rejects_a_name_outside_this_domain() -> None:
    """本片只碰电解质：别的域的项目名必须仍然显式落 unknown_metric，不得被误认。"""

    result = build_evidence_request(
        [
            {
                "metric_name": "Neutrophils",
                "metric_value": "80",
                "unit": "%",
                "reference_range": "40-75",
                "evidence_text": "Neutrophils 80 % 参考范围 40-75 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert list(result.request.observations) == []
    assert result.skipped[0]["reason"] == "unknown_metric"
