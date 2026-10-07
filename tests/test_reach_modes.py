"""T7：体成分触达 + 人群属性触达的边界（PRD #239，ADR 0008）。

体成分（指标触达）：报告项名 → canonical 指标 → 超重与肥胖。
人群属性触达：**本仓做不了**——需要患者性别/年龄，而服务没有这些输入。
ADR 0008 定下 fail-closed，这里把「做不到」钉成断言，而不是让它悄悄变成
「做成了但会误报」。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE, CONDITIONS
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.integrations.health_flow import build_evidence_request

OVERWEIGHT = "COND_OVERWEIGHT_OBESITY"

#: 报告实测出现过的体成分项名。
BODY_COMPOSITION_NAMES = {
    "Body Fat Rate": "body_fat_rate",
    "Waist Circumference": "waist_circumference",
    "BMI": "bmi",
}

#: 依赖「人群属性」触达的 condition：性别或生命阶段。见 ADR 0008。
POPULATION_RESTRICTED = (
    "COND_MALE_OSTEOPOROSIS",
    "COND_BPH_RISK",
    "COND_MENOPAUSE_HEALTH_RISK",
)


@pytest.mark.parametrize(
    ("report_name", "expected"), sorted(BODY_COMPOSITION_NAMES.items())
)
def test_body_composition_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_body_composition_metrics_reach_overweight() -> None:
    for metric_code in ("bmi", "waist_circumference", "body_fat_rate"):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
        assert OVERWEIGHT in claims, metric_code


def test_adapter_resolves_a_body_composition_row() -> None:
    result = build_evidence_request(
        [
            {
                "metric_name": "Waist Circumference",
                "metric_value": "98",
                "unit": "cm",
                "reference_range": "70-90",
                "evidence_text": "Waist Circumference 98 cm 参考范围 70-90 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["waist_circumference"]


def test_population_restricted_conditions_claim_no_metric() -> None:
    """ADR 0008：人群属性触达缺输入时 fail-closed。

    `EvidenceMatcher` 只按 `metric_code` 路由，拿不到性别与年龄。给这些
    condition 挂指标，一位男性高 LDL 会拿到「更年期健康风险」、一位女性低骨密度
    会拿到「男性骨质疏松风险」。所以它们指标集为空、患者侧不可达。
    """

    offenders = {
        code: CONDITION_BY_CODE[code].metrics
        for code in POPULATION_RESTRICTED
        if CONDITION_BY_CODE[code].metrics
    }

    assert not offenders, f"population-restricted conditions must claim no metric: {offenders}"


def test_population_restricted_conditions_stay_in_the_catalog() -> None:
    """不可达 ≠ 不在目录里：它们是真实的健康问题，覆盖矩阵必须显示它们。"""

    codes = {condition.code for condition in CONDITIONS}
    assert not (set(POPULATION_RESTRICTED) - codes)


def test_the_boundary_carries_no_patient_demographics() -> None:
    """ADR 0008 的前提：证据请求里没有性别/年龄字段。

    若将来加了，这条会红——那时应当连同 ADR 一起重新判定，而不是顺手让它绿。
    """

    from genesis_evidence.core.contracts import EvidenceMatchObservation

    fields = set(EvidenceMatchObservation.model_fields)

    assert not (fields & {"patient_sex", "patient_age", "sex", "age", "birth_date"}), (
        "the evidence boundary gained patient demographics; revisit ADR 0008"
    )
