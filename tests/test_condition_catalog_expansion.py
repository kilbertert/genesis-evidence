"""第二批目录扩表（PRD #239 T2）。

断言的是**外部可观察行为**：目录认领哪些指标、经播种后落到哪、匹配层怎么推导。
不断言元组长度，也不断言内部推导结构——那正是 T1 拆掉的那种写法。
"""

from __future__ import annotations

from genesis_evidence.core.conditions import CONDITION_BY_CODE, CONDITIONS
from genesis_evidence.core.matching import CONDITIONS_BY_METRIC
from genesis_evidence.core.metrics import METRIC_LABELS
from genesis_evidence.core.store import Database

#: 本片新增的健康问题（PRD #239 的 10 个 + 按域补齐的 4 个）。
#: 系统边界用 code 集合定义，不是为测试新增的字段——"第二批"只是历史事实。
SECOND_BATCH_CODES = frozenset(
    {
        "COND_OVERWEIGHT_OBESITY",
        "COND_METABOLIC_SYNDROME",
        "COND_COGNITIVE_DECLINE_RISK",
        "COND_AMD_RISK",
        "COND_DRY_EYE_RISK",
        "COND_INSOMNIA_RISK",
        "COND_OSTEOARTHRITIS_RISK",
        "COND_MALE_OSTEOPOROSIS",
        "COND_BPH_RISK",
        "COND_MENOPAUSE_HEALTH_RISK",
        # 按域补齐（T3/T4/T5/T6 各自的归宿）
        "COND_ELECTROLYTE_DISTURBANCE",
        "COND_INFECTION_INFLAMMATION_PATTERN",
        "COND_URINARY_ABNORMALITY",
        "COND_CARDIAC_ENZYME_PATTERN",
        "COND_LIVER_FUNCTION_PATTERN",
    }
)

#: 首批承接的书面记录（#239 Further Notes 的实证）。本片不得改动。
FIRST_BATCH_METRICS = {
    "COND_HYPERTENSION_RISK": ("systolic_blood_pressure", "diastolic_blood_pressure"),
    "COND_PREDIABETES": ("fasting_glucose", "hba1c"),
    "COND_DYSLIPIDEMIA": ("triglycerides", "hdl_c", "ldl_c", "total_cholesterol", "non_hdl_c"),
    "COND_MASLD_RISK": ("alt", "ast", "ggt", "fasting_glucose", "triglycerides"),
    "COND_HYPERURICEMIA_RISK": ("uric_acid",),
    "COND_CKD_RISK": ("egfr", "creatinine", "uacr"),
    "COND_ANEMIA_PATTERN": ("hemoglobin", "mcv", "ferritin", "tsat"),
    "COND_VITAMIN_D_DEFICIENCY": ("25_oh_vitamin_d",),
    "COND_OSTEOPOROSIS_RISK": ("bone_density_t_score", "calcium", "alp"),
    "COND_SARCOPENIA_FRAILTY": ("grip_strength", "walking_speed", "muscle_mass"),
    "COND_MALNUTRITION_RISK": ("albumin", "bmi", "prealbumin"),
    "COND_CHRONIC_CONSTIPATION": (),
}


def test_second_batch_conditions_are_all_in_the_catalog() -> None:
    assert not (SECOND_BATCH_CODES - set(CONDITION_BY_CODE)), (
        f"missing second-batch conditions: {sorted(SECOND_BATCH_CODES - set(CONDITION_BY_CODE))}"
    )


def test_every_condition_carries_a_department_and_recheck_direction() -> None:
    """患者侧的就医导航靠这两个字段，新老一视同仁。"""

    incomplete = [
        condition.code
        for condition in CONDITIONS
        if not condition.department.strip() or not condition.recheck_direction.strip()
    ]

    assert not incomplete, f"conditions without navigation fields: {incomplete}"


def test_no_condition_references_a_metric_outside_the_canonical_labels() -> None:
    """目录自洽：被引用的每个 metric_code 都必须能在标签表查到。

    否则响应组装时 `METRIC_LABELS[metric_code]` 会 KeyError——这不是理论风险，
    它出现在 `core/store/evidence.py` 与 `core/matching.py` 两处。
    """

    dangling = {
        condition.code: sorted(set(condition.metrics) - set(METRIC_LABELS))
        for condition in CONDITIONS
        if set(condition.metrics) - set(METRIC_LABELS)
    }

    assert not dangling, f"conditions referencing unlabeled metrics: {dangling}"


def test_conditions_by_metric_links_new_metrics_to_their_condition() -> None:
    """零代码改动生效的实证：`CONDITIONS_BY_METRIC` 是推导出来的，扩目录即自动关联。"""

    assert CONDITION_BY_CODE["COND_ELECTROLYTE_DISTURBANCE"] in CONDITIONS_BY_METRIC["sodium"]
    assert CONDITION_BY_CODE["COND_URINARY_ABNORMALITY"] in CONDITIONS_BY_METRIC["urine_protein"]
    # 共享指标要关联到**每一个**认领它的健康问题，不是一个。
    assert {
        condition.code for condition in CONDITIONS_BY_METRIC["fasting_glucose"]
    } >= {"COND_PREDIABETES", "COND_MASLD_RISK", "COND_METABOLIC_SYNDROME"}


def test_first_batch_metric_assignments_are_unchanged() -> None:
    """扩表是追加，不得改写首批既有归属。"""

    drifted = {
        code: (CONDITION_BY_CODE[code].metrics, expected)
        for code, expected in FIRST_BATCH_METRICS.items()
        if CONDITION_BY_CODE[code].metrics != expected
    }

    assert not drifted, f"first-batch metric assignments changed: {drifted}"


def test_initialization_seeds_the_new_conditions(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    seeded = {condition.code for condition in database.list_conditions()}

    assert not (SECOND_BATCH_CODES - seeded), (
        f"conditions missing after seeding: {sorted(SECOND_BATCH_CODES - seeded)}"
    )


def test_new_scopes_appear_in_the_coverage_matrix_as_unstarted(tmp_path) -> None:
    """新范围进覆盖矩阵，起步状态是 `planned`（未闭合），不是 `screening`。

    `screening` 表示「已有一轮检索在筛」，需要一个进行中的 collection run；
    新目录一条主题都还没建，所以起步是 `planned`，`next_action` 是建立并锁定
    主题/PICOTS。票据 AC 写的是「状态为 screening（未闭合）」——它要表达的
    「尚未闭合」是对的，但词选错了；这里按真实词表断言。
    """

    from genesis_evidence.core.store import ReviewStore

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    matrix = ReviewStore(database).list_coverage_matrix()
    new_rows = [row for row in matrix if row["condition_code"] in SECOND_BATCH_CODES]

    assert new_rows, "second-batch conditions produced no coverage rows"
    assert {row["coverage_status"] for row in new_rows} == {"planned"}
    # 未闭合：没有任何一行被标成已覆盖。
    assert not any(row["coverage_status"].startswith("published") for row in new_rows)


def test_new_conditions_produce_no_finding_until_a_card_is_published(tmp_path) -> None:
    """本片的预期中间态：新 condition 在册，患者侧却还看不到内容。

    注意这里**不是**用「别名解析不到」当判据——那是错的：canonical 码本身
    就会进别名表（`METRIC_ALIASES` 由码与标签两者派生），所以补一个 `sodium`
    码，报告上的 `Sodium` 当场就解析得了。真正把内容挡在门外的是**已发布卡**：
    匹配层只在 `adapter.lookup(...)` 返回卡片时才产出 finding。

    这正是 PRD 说的「放开的是覆盖范围，不是确定性等级」——扩目录不等于上线。
    """

    from genesis_evidence.core.contracts import EvidenceMatchObservation
    from genesis_evidence.core.store import EvidenceStore

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    observation = EvidenceMatchObservation(
        observation_id="obs-sodium",
        metric_code="sodium",
        value=150.0,
        unit="mmol/L",
        reference_low=137.0,
        reference_high=147.0,
        evidence_text="Sodium 150.0 mmol/L (137-147)",
        source_file_index=1,
        source_page=1,
        confirmation_status="confirmed",
    )

    payload = EvidenceStore(database).match_published_cards(
        [observation], correlation_id="corr-1"
    )

    assert payload["findings"] == []
    # 而它确实被认领了——只是没卡可发，所以落进 unmatched 而不是被静默丢弃。
    assert [item["metric_code"] for item in payload["unmatched"]] == ["sodium"]
