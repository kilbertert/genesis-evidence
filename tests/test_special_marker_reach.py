"""T8：专项标志物可解读，且**每一条带措辞边界**（PRD #239）。

本片风险最高：三个标志物都容易被读成诊断。路由设计是**按化验结果命名、
不按疾病命名**——名称本身就是边界的一部分。

每条边界都要有**反例断言**（断言诊断性措辞会被拒），不只正例通过。
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE, CONDITIONS
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_ALIASES, normalize_metric_name
from genesis_evidence.core.patient_copy import FORBIDDEN_PATIENT_TERMS, validate_patient_copy
from genesis_evidence.integrations.health_flow import build_evidence_request

#: condition code -> (它认领的指标, 该从名字里去掉的诊断性字眼)
REVIEW_PROMPTS = {
    "COND_PROSTATE_REVIEW_PROMPT": ("psa", ("前列腺增生", "肿瘤", "癌")),
    "COND_RHEUMATOID_REVIEW_PROMPT": ("rheumatoid_factor", ("类风湿关节炎", "关节炎")),
    "COND_EYE_REVIEW_PROMPT": ("uncorrected_vision", ("黄斑", "变性", "干眼")),
}


def _diagnostic_terms_present(text: str, forbidden: tuple[str, ...]) -> set[str]:
    """该文案里出现了哪些诊断性字眼。

    与 `validate_patient_copy` 互补：词表覆盖通用禁词，这里覆盖**本片专属**的
    病名（增生、肿瘤、黄斑、关节炎……）。两者都要能对反例亮灯，正例才可信。
    """

    hits = {term for term in forbidden if term in text}
    hits |= {term for term in FORBIDDEN_PATIENT_TERMS if term in text}
    return hits


REPORT_NAMES = {
    "PSA": "psa",
    "Rheumatoid Factor": "rheumatoid_factor",
    "Uncorrected Vision, Right Eye": "uncorrected_vision",
    "Uncorrected Vision - Left Eye": "uncorrected_vision",
    "Intraocular Pressure, Right Eye": "intraocular_pressure",
    "Intraocular Pressure - Left Eye": "intraocular_pressure",
}


@pytest.mark.parametrize(("report_name", "expected"), sorted(REPORT_NAMES.items()))
def test_special_marker_report_names_resolve(report_name: str, expected: str) -> None:
    assert METRIC_ALIASES.get(normalize_metric_name(report_name)) == expected


def test_eye_names_collapse_left_and_right_onto_one_metric() -> None:
    """左右眼同指标：病种与复查方向相同，分眼只会让 scope 翻倍而无分别。"""

    for side in ("Left", "Right"):
        for prefix, code in (
            ("Uncorrected Vision", "uncorrected_vision"),
            ("Intraocular Pressure", "intraocular_pressure"),
        ):
            for sep in (", ", " - "):
                assert METRIC_ALIASES[normalize_metric_name(f"{prefix}{sep}{side} Eye")] == code


@pytest.mark.parametrize("condition_code", sorted(REVIEW_PROMPTS))
def test_review_prompt_condition_reaches_through_its_marker(condition_code: str) -> None:
    metric_code = REVIEW_PROMPTS[condition_code][0]
    claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric_code]}
    assert condition_code in claims


@pytest.mark.parametrize("condition_code", sorted(REVIEW_PROMPTS))
def test_review_prompt_names_carry_no_diagnostic_wording(condition_code: str) -> None:
    """正例：名称与复查方向过既有患者文案闸门，且不含任何诊断性字眼。"""

    condition = CONDITION_BY_CODE[condition_code]
    _, forbidden = REVIEW_PROMPTS[condition_code]
    text = f"{condition.name}{condition.recheck_direction}"

    validate_patient_copy(condition.name)
    validate_patient_copy(condition.recheck_direction)
    for term in FORBIDDEN_PATIENT_TERMS + forbidden:
        assert term not in text, f"{condition_code} carries diagnostic wording: {term}"


@pytest.mark.parametrize("condition_code", sorted(REVIEW_PROMPTS))
def test_rejected_counterexamples_are_actually_rejected(condition_code: str) -> None:
    """**反例断言**：诊断性的说法必须被判为越界，不能只是正例通过。

    票据要求每条边界各有一条反例。判据是同一个函数，先对真名断言通过，再对
    反例断言不通过——否则「通过」只说明判据太松，不说明文案干净。
    """

    condition = CONDITION_BY_CODE[condition_code]
    _, forbidden = REVIEW_PROMPTS[condition_code]

    assert not _diagnostic_terms_present(condition.name, forbidden)
    assert not _diagnostic_terms_present(condition.recheck_direction, forbidden)

    for term in forbidden:
        bad_name = f"{condition.name}{term}"
        bad_direction = f"{condition.recheck_direction}（{term}）"
        assert _diagnostic_terms_present(bad_name, forbidden), (
            f"predicate failed to flag {term!r} in a diagnosis-shaped name"
        )
        assert _diagnostic_terms_present(bad_direction, forbidden)


def test_disease_named_conditions_do_not_claim_a_lab_metric() -> None:
    """疾病风险 condition 不得由化验数值判定。

    PSA 异常只支持「前列腺相关复查提示」；AMD/干眼要靠眼科检查，骨关节炎要靠
    症状与查体。把它们挂在化验上，患者读到的会是疾病名，而证据只支持复查。
    """

    for code, metric in (
        ("COND_BPH_RISK", "psa"),
        ("COND_AMD_RISK", "uncorrected_vision"),
        ("COND_DRY_EYE_RISK", "intraocular_pressure"),
    ):
        claims = {condition.code for condition in CONDITIONS_BY_METRIC[metric]}
        assert code not in claims, f"{code} claims {metric}, which it cannot support"
        assert CONDITION_BY_CODE[code].metrics == ()


def test_disease_named_conditions_stay_in_the_catalog() -> None:
    codes = {condition.code for condition in CONDITIONS}
    assert {"COND_BPH_RISK", "COND_AMD_RISK", "COND_DRY_EYE_RISK"} <= codes


def test_adapter_resolves_a_special_marker_row() -> None:
    result = build_evidence_request(
        [
            {
                "metric_name": "Rheumatoid Factor",
                "metric_value": "80",
                "unit": "IU/mL",
                "reference_range": "0-14",
                "evidence_text": "Rheumatoid Factor 80 IU/mL 参考范围 0-14 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert [item.metric_code for item in result.request.observations] == ["rheumatoid_factor"]
