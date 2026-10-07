"""T4：血细胞分类域可解读（PRD #239）。

报告里观测最多的域（82 条）。外部可观察行为：报告项名 → canonical 指标 →
健康问题；以及患者可见文案的措辞边界。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.core.patient_copy import FORBIDDEN_PATIENT_TERMS, validate_patient_copy
from genesis_evidence.integrations.health_flow import build_evidence_request

INFECTION_CONDITION = "COND_INFECTION_INFLAMMATION_PATTERN"

#: 真实报告里出现过的项名 -> 预期 canonical 指标。
#: **裸名是绝对值**（实测单位 x10⁹/L、参考区间 2.0-7.0），百分比另有其名。
REPORT_NAMES = {
    "Neutrophils": "neutrophils_absolute",
    "Neutrophils percentage": "neutrophils_percent",
    "NEUT%": "neutrophils_percent",
    "Neutrophils absolute count": "neutrophils_absolute",
    "Lymphocytes": "lymphocytes_absolute",
    "Lymphocytes percentage": "lymphocytes_percent",
    "LYMPH%": "lymphocytes_percent",
    "Monocytes": "monocytes_absolute",
    "Monocytes absolute count": "monocytes_absolute",
    "Eosinophils": "eosinophils_absolute",
    "EO%": "eosinophils_percent",
    "Basophils": "basophils_absolute",
    "BASO%": "basophils_percent",
    "WBC": "wbc",
    "White Cell Count": "wbc",
}


@pytest.mark.parametrize(("report_name", "expected"), sorted(REPORT_NAMES.items()))
def test_differential_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_bare_name_is_absolute_not_percent() -> None:
    """裸名 `Neutrophils` 是绝对值；百分比是另一项。混了会把值挂错指标。"""

    assert METRIC_ALIASES[normalize_metric_name("Neutrophils")] == "neutrophils_absolute"
    assert (
        METRIC_ALIASES[normalize_metric_name("Neutrophils percentage")]
        == "neutrophils_percent"
    )


def test_urine_leucocytes_is_not_blood_wbc() -> None:
    """尿沉渣的 `Leucocytes`（x10⁶/L）不是血白细胞，不得归 `wbc`。

    按字面归 WBC 会把尿里的计数挂到血象上——正是本批要避免的错误归属。
    """

    assert METRIC_ALIASES.get(normalize_metric_name("Leucocytes")) is None


def test_every_differential_metric_reaches_the_infection_condition() -> None:
    for metric_code in (
        "wbc",
        "neutrophils_absolute",
        "neutrophils_percent",
        "lymphocytes_absolute",
        "lymphocytes_percent",
        "monocytes_absolute",
        "monocytes_percent",
        "eosinophils_absolute",
        "eosinophils_percent",
        "basophils_absolute",
        "basophils_percent",
    ):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
        assert INFECTION_CONDITION in claims, metric_code


def test_adapter_resolves_a_differential_row() -> None:
    result = build_evidence_request(
        [
            {
                "metric_name": "NEUT%",
                "metric_value": "82",
                "unit": "%",
                "reference_range": "50-70",
                "evidence_text": "NEUT% 82 % 参考范围 50-70 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["neutrophils_percent"]


def test_the_condition_navigates_to_a_review_not_a_diagnosis() -> None:
    """措辞边界：分类异常只能导向**复查**，文案里不得出现诊断性表述。

    `validate_patient_copy` 是既有的患者文案闸门，这里用它证明该 condition 的
    科室/复查方向能过闸门，且不含任何禁用词。
    """

    condition = CONDITION_BY_CODE[INFECTION_CONDITION]

    validate_patient_copy(condition.name)
    validate_patient_copy(condition.recheck_direction)
    assert not any(term in condition.recheck_direction for term in FORBIDDEN_PATIENT_TERMS)
    # 导向具体科室的复查，而不是给出结论。
    assert "复查" in condition.recheck_direction
