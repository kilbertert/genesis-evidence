"""Canonical report metric labels shared by adapters and the portal."""

from __future__ import annotations

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
}


def normalize_metric_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


METRIC_ALIASES = {
    **{normalize_metric_name(code): code for code in METRIC_LABELS},
    **{normalize_metric_name(label): code for code, label in METRIC_LABELS.items()},
}

