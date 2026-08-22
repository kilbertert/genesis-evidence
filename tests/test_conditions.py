from genesis_evidence.core.conditions import CONDITION_BY_CODE, CONDITIONS


def test_first_batch_contains_exactly_twelve_unique_conditions() -> None:
    assert len(CONDITIONS) == 12
    assert len(CONDITION_BY_CODE) == 12
    assert {item.code for item in CONDITIONS} == {
        "COND_HYPERTENSION_RISK",
        "COND_PREDIABETES",
        "COND_DYSLIPIDEMIA",
        "COND_MASLD_RISK",
        "COND_HYPERURICEMIA_RISK",
        "COND_CKD_RISK",
        "COND_ANEMIA_PATTERN",
        "COND_VITAMIN_D_DEFICIENCY",
        "COND_OSTEOPOROSIS_RISK",
        "COND_SARCOPENIA_FRAILTY",
        "COND_MALNUTRITION_RISK",
        "COND_CHRONIC_CONSTIPATION",
    }


def test_constipation_does_not_claim_report_metric_matching() -> None:
    assert CONDITION_BY_CODE["COND_CHRONIC_CONSTIPATION"].metrics == ()


def test_dyslipidemia_includes_non_hdl_canonical_metric() -> None:
    assert "non_hdl_c" in CONDITION_BY_CODE["COND_DYSLIPIDEMIA"].metrics
