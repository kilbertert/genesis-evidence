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
    # --- 第二批：报告异常驱动的健康问题（PRD #239）--------------------------------
    # 这批的名字来自对真实报告的逐域复核，不是补充剂功能分类（健康方向）。批次是
    # 历史事实，不进模型：不加前缀、不改形状，与首批走同一道证据闸门。
    ConditionDefinition(
        "COND_OVERWEIGHT_OBESITY",
        "超重与肥胖",
        ("bmi", "waist_circumference", "body_fat_rate"),
        "内分泌科",
        "记录体重与腰围并评估体重管理",
    ),
    ConditionDefinition(
        "COND_METABOLIC_SYNDROME",
        "代谢综合征",
        (
            "bmi",
            "waist_circumference",
            "fasting_glucose",
            "triglycerides",
            "hdl_c",
            "systolic_blood_pressure",
            "diastolic_blood_pressure",
        ),
        "内分泌科",
        "复核腰围、血糖、血压与血脂的组合风险",
    ),
    ConditionDefinition(
        "COND_COGNITIVE_DECLINE_RISK",
        "认知功能下降风险",
        (),
        "神经内科",
        "记录认知相关困扰并到神经内科评估",
    ),
    ConditionDefinition(
        "COND_AMD_RISK",
        "年龄相关性黄斑变性风险",
        ("uncorrected_vision", "intraocular_pressure"),
        "眼科",
        "到眼科复查视力与眼底",
    ),
    ConditionDefinition(
        "COND_DRY_EYE_RISK",
        "干眼风险",
        (),
        "眼科",
        "记录眼部干涩症状并到眼科评估",
    ),
    ConditionDefinition(
        "COND_INSOMNIA_RISK",
        "失眠风险",
        (),
        "睡眠医学科",
        "记录睡眠情况并评估持续时间与继发因素",
    ),
    ConditionDefinition(
        "COND_OSTEOARTHRITIS_RISK",
        "骨关节炎风险",
        (),
        "骨科",
        "结合关节症状与查体到骨科评估",
    ),
    # 这三条按人群属性限定（男/女），但匹配链路上**没有性别闸门**——
    # `EvidenceMatcher` 拿不到患者性别，`CONDITIONS_BY_METRIC` 只按指标索引。
    # 若现在就给它们挂上通用指标，一位男性高 LDL 会拿到「更年期健康风险」，
    # 一位女性低骨密度会拿到「男性骨质疏松风险」。所以先 fail-closed：指标集
    # 留空，患者侧不可达，直到 T7 建好人群属性触达再填回。
    # 这是已知且接受的中间态，不是缺陷——与 `COND_CHRONIC_CONSTIPATION` 同形。
    ConditionDefinition(
        "COND_MALE_OSTEOPOROSIS",
        "男性骨质疏松风险",
        (),
        "内分泌科",
        "复查骨密度并排查继发因素",
    ),
    ConditionDefinition(
        "COND_BPH_RISK",
        "良性前列腺增生风险",
        (),
        "泌尿外科",
        "到泌尿外科复查评估下尿路症状",
    ),
    ConditionDefinition(
        "COND_MENOPAUSE_HEALTH_RISK",
        "更年期健康风险",
        (),
        "妇科",
        "结合围绝经期症状到妇科或内分泌科评估",
    ),
    # 以下四条按域补齐。它们是 T3–T6 各自的归宿：电解质（44 条观测）、血细胞分类
    # （82 条，报告里最多的域）、尿常规（8/9 份报告）此前落不到任何健康问题，
    # 而 PRD 的问题陈述正是「不要静默丢弃」。不补则 T5 无家可归、无法完成。
    ConditionDefinition(
        "COND_ELECTROLYTE_DISTURBANCE",
        "电解质紊乱风险",
        ("sodium", "potassium", "chloride", "phosphate", "corrected_calcium"),
        "肾内科",
        "复查电解质并结合临床评估",
    ),
    ConditionDefinition(
        "COND_INFECTION_INFLAMMATION_PATTERN",
        "感染与炎症模式",
        (
            "wbc",
            "neutrophils_percent",
            "lymphocytes_percent",
            "monocytes_percent",
            "eosinophils_percent",
            "basophils_percent",
            "neutrophils_absolute",
            "lymphocytes_absolute",
            "monocytes_absolute",
            "eosinophils_absolute",
            "basophils_absolute",
        ),
        "全科",
        "复查血常规并结合症状到全科评估",
    ),
    ConditionDefinition(
        "COND_URINARY_ABNORMALITY",
        "泌尿系统异常提示",
        (
            "urine_protein",
            "urine_leucocytes",
            "urine_erythrocytes",
            "urine_specific_gravity",
            "urine_ph",
        ),
        "肾内科",
        "复查尿常规并到肾内科或泌尿外科评估",
    ),
    ConditionDefinition(
        "COND_CARDIAC_ENZYME_PATTERN",
        "心肌酶异常提示",
        ("ck", "ck_mb", "ldh"),
        "心血管内科",
        "复查心肌酶并到心血管内科评估",
    ),
)


CONDITION_BY_CODE = {condition.code: condition for condition in CONDITIONS}
