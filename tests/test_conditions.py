from genesis_evidence.core.conditions import CONDITION_BY_CODE, CONDITIONS

#: 首批 12 个。这是**冻结集合**：它们必须一直在册，删掉任何一个都让本文件变红。
FIRST_BATCH_CODES = frozenset(
    {
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
)

#: 第二批及其后。与首批**分开断言**：这里增删是预期内的扩表，不是回归。
#: 声明在此、断言在下一处；维护者新增健康问题时把 code 加进来。
LATER_BATCH_CODES: frozenset[str] = frozenset()


def test_first_batch_conditions_all_remain() -> None:
    """首批只增不减——扩表不得悄悄改动或移除既有条目。

    This replaces the earlier `len(CONDITIONS) == 12`, which pinned the *size* of
    the catalog rather than the *presence* of its first batch. Those two coincide
    only until the catalog grows, which is exactly what the expansion does, so
    every later slice would have landed on a red test for no reason.
    """

    codes = [item.code for item in CONDITIONS]

    assert not (FIRST_BATCH_CODES - set(codes)), (
        f"dropped first-batch conditions: {sorted(FIRST_BATCH_CODES - set(codes))}"
    )
    assert not (FIRST_BATCH_CODES - set(CONDITION_BY_CODE)), (
        "first batch missing from the by-code index"
    )
    # Both assertions above are set-valued, so a duplicate code is invisible to
    # them: two entries collapse to one and every set comparison still holds. Count
    # over the raw sequence instead — `list` sees the duplicate, `set` cannot.
    #
    # Deliberately not also asserting `set(CONDITION_BY_CODE) == set(codes)`: the
    # index is built by comprehension over `CONDITIONS`, so that equality holds by
    # construction and the assertion can never fail. A duplicate is the one way the
    # index can lose an entry, and it is caught here.
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    assert not duplicates, f"duplicate condition code in the catalog: {duplicates}"


def test_later_batch_conditions_are_the_declared_ones() -> None:
    """扩表集合，独立于首批冻结——两者判据不同，理由不同，失败信息也不同。"""

    codes = {item.code for item in CONDITIONS}

    assert not (LATER_BATCH_CODES - codes), (
        f"declared conditions missing from the catalog: {sorted(LATER_BATCH_CODES - codes)}"
    )
    assert not (LATER_BATCH_CODES & FIRST_BATCH_CODES), "a condition cannot be both batches"


def test_constipation_does_not_claim_report_metric_matching() -> None:
    assert CONDITION_BY_CODE["COND_CHRONIC_CONSTIPATION"].metrics == ()


def test_dyslipidemia_includes_non_hdl_canonical_metric() -> None:
    assert "non_hdl_c" in CONDITION_BY_CODE["COND_DYSLIPIDEMIA"].metrics
