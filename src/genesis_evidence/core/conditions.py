"""The fixed first-batch health-problem catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    code: str
    name: str
    metrics: tuple[str, ...]
    department: str
    recheck_direction: str


CONDITIONS = (
    ConditionDefinition(
        "COND_HYPERTENSION_RISK",
        "高血压风险",
        ("systolic_blood_pressure", "diastolic_blood_pressure"),
        "心血管内科",
        "规范复测血压并记录家庭血压",
    ),
    ConditionDefinition(
        "COND_PREDIABETES",
        "糖尿病前期 / 糖代谢异常",
        ("fasting_glucose", "hba1c"),
        "内分泌科",
        "复查空腹血糖与糖化血红蛋白",
    ),
    ConditionDefinition(
        "COND_DYSLIPIDEMIA",
        "血脂异常",
        ("triglycerides", "hdl_c", "ldl_c", "total_cholesterol", "non_hdl_c"),
        "心血管内科",
        "空腹复查血脂组合",
    ),
    ConditionDefinition(
        "COND_MASLD_RISK",
        "代谢相关脂肪性肝病风险",
        ("alt", "ast", "ggt", "fasting_glucose", "triglycerides"),
        "消化内科",
        "复查肝功能并结合腹部影像评估",
    ),
    ConditionDefinition(
        "COND_HYPERURICEMIA_RISK",
        "高尿酸血症 / 痛风风险",
        ("uric_acid",),
        "风湿免疫科",
        "复查血尿酸并记录关节症状",
    ),
    ConditionDefinition(
        "COND_CKD_RISK",
        "慢性肾脏病风险",
        ("egfr", "creatinine", "uacr"),
        "肾内科",
        "复查肾功能与尿白蛋白肌酐比",
    ),
    ConditionDefinition(
        "COND_ANEMIA_PATTERN",
        "贫血与缺铁模式",
        ("hemoglobin", "mcv", "ferritin", "tsat"),
        "血液科",
        "复查血常规与铁代谢指标",
    ),
    ConditionDefinition(
        "COND_VITAMIN_D_DEFICIENCY",
        "维生素 D 缺乏风险",
        ("25_oh_vitamin_d",),
        "内分泌科",
        "复查 25-羟维生素 D",
    ),
    ConditionDefinition(
        "COND_OSTEOPOROSIS_RISK",
        "骨量减少 / 骨质疏松风险",
        ("bone_density_t_score", "calcium", "alp"),
        "骨科",
        "结合骨密度与骨代谢指标复查",
    ),
    ConditionDefinition(
        "COND_SARCOPENIA_FRAILTY",
        "肌少症 / 衰弱风险",
        ("grip_strength", "walking_speed", "muscle_mass"),
        "老年医学科",
        "复测握力、步速和肌肉量",
    ),
    ConditionDefinition(
        "COND_MALNUTRITION_RISK",
        "营养不良风险",
        ("albumin", "bmi", "prealbumin"),
        "临床营养科",
        "复核体重变化并复查营养相关指标",
    ),
    ConditionDefinition(
        "COND_CHRONIC_CONSTIPATION",
        "慢性便秘",
        (),
        "消化内科",
        "记录排便症状并完成生活方式评估",
    ),
)


CONDITION_BY_CODE = {condition.code: condition for condition in CONDITIONS}
