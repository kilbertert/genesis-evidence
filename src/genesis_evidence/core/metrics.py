"""Canonical report metric labels shared by adapters and the portal."""

from __future__ import annotations

import math
import re
import unicodedata

METRIC_LABELS = {
    "systolic_blood_pressure": "收缩压",
    "diastolic_blood_pressure": "舒张压",
    "fasting_glucose": "空腹血糖",
    "hba1c": "糖化血红蛋白",
    "triglycerides": "甘油三酯",
    "hdl_c": "高密度脂蛋白胆固醇",
    "ldl_c": "低密度脂蛋白胆固醇",
    "total_cholesterol": "总胆固醇",
    "non_hdl_c": "非高密度脂蛋白胆固醇",
    "alt": "丙氨酸氨基转移酶",
    "ast": "天门冬氨酸氨基转移酶",
    "ggt": "γ-谷氨酰转移酶",
    "uric_acid": "尿酸",
    "egfr": "估算肾小球滤过率",
    "creatinine": "肌酐",
    "uacr": "尿白蛋白肌酐比",
    "hemoglobin": "血红蛋白",
    "mcv": "平均红细胞体积",
    "ferritin": "铁蛋白",
    "tsat": "转铁蛋白饱和度",
    "25_oh_vitamin_d": "25-羟维生素 D",
    "bone_density_t_score": "骨密度 T 值",
    "calcium": "钙",
    "alp": "碱性磷酸酶",
    "grip_strength": "握力",
    "walking_speed": "步速",
    "muscle_mass": "肌肉量",
    "albumin": "白蛋白",
    "bmi": "体重指数",
    "prealbumin": "前白蛋白",
    # 第二批（PRD #239）。目录里被引用的每个 metric_code 都必须在这里有条目，
    # 否则响应组装时 `METRIC_LABELS[...]` 会 KeyError。
    # 注意：这里只放 canonical 指标，不放报告项目名——报告名经别名表解析，
    # 别名表由「标识符 + 标签」两者派生，因此英文报告名（Waist Circumference
    # 等）在 T3–T8 各自补，本片不补。
    "waist_circumference": "腰围",
    "body_fat_rate": "体脂率",
    "uncorrected_vision": "裸眼视力",
    "intraocular_pressure": "眼压",
    "psa": "前列腺特异性抗原",
    "sodium": "钠",
    "potassium": "钾",
    "chloride": "氯",
    "phosphate": "磷酸盐",
    "corrected_calcium": "校正钙",
    "wbc": "白细胞计数",
    "neutrophils_percent": "中性粒细胞百分比",
    "lymphocytes_percent": "淋巴细胞百分比",
    "monocytes_percent": "单核细胞百分比",
    "eosinophils_percent": "嗜酸性粒细胞百分比",
    "basophils_percent": "嗜碱性粒细胞百分比",
    "neutrophils_absolute": "中性粒细胞绝对值",
    "lymphocytes_absolute": "淋巴细胞绝对值",
    "monocytes_absolute": "单核细胞绝对值",
    "eosinophils_absolute": "嗜酸性粒细胞绝对值",
    "basophils_absolute": "嗜碱性粒细胞绝对值",
    "urine_protein": "尿蛋白",
    "urine_leucocytes": "尿白细胞",
    "urine_erythrocytes": "尿红细胞",
    "urine_specific_gravity": "尿比重",
    "urine_ph": "尿 pH",
    "ck": "肌酸激酶",
    "ck_mb": "肌酸激酶同工酶",
    "ldh": "乳酸脱氢酶",
}


def normalize_metric_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def evidence_contains_value(evidence: str, value: float) -> bool:
    """Return whether a finite numeric value appears verbatim in source text."""

    normalized = unicodedata.normalize("NFKC", evidence)
    return any(
        math.isclose(float(match.group()), value, rel_tol=1e-9, abs_tol=1e-12)
        for match in re.finditer(r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])", normalized)
    )


#: 报告项目名 → metric_code。canonical 码与中文标签由 `METRIC_LABELS` 派生；
#: 这里只补**两者派生不出来**的报告写法：缩写。
#: 两种来源最后合进 `METRIC_ALIASES` 一个映射，解析点仍只有一处（见术语表）。
#: 英文全称（`Sodium`/`Phosphate`/`Corrected Calcium`）无需登记——它们与
#: canonical 码同名，派生已经覆盖。
REPORT_METRIC_ALIASES = {
    # 电解质（T3）。报告上印的是短代号。
    # `Ca` 是**总钙**，不是白蛋白校正钙，所以归 `calcium` 而非 `corrected_calcium`。
    "Na": "sodium",
    "K": "potassium",
    "Cl": "chloride",
    "Ca": "calcium",
}


METRIC_ALIASES = {
    **{normalize_metric_name(code): code for code in METRIC_LABELS},
    **{normalize_metric_name(label): code for code, label in METRIC_LABELS.items()},
    **{normalize_metric_name(name): code for name, code in REPORT_METRIC_ALIASES.items()},
}
