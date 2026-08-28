"""Deterministic PICOTS matching and Evidence Profile scope resolution.

This module is the single home for the Evidence Profile "stable outcome scope"
decision: first-batch report metrics resolve to ``metric:<metric_code>``,
metric-less conditions to ``condition:<condition_code>``, and other outcomes to
a deterministic ``outcome:<slug>`` derived from the locked PICOTS outcome text.

It is deliberately stateless and free of any storage dependency: the resolver
takes only locked-topic PICOTS, condition definitions, metric labels, and
normalized decision inputs. Review stores and the autonomous review service are
thin callers of this interface. NOTE: this module must not import
``genesis_evidence.review`` (its ``__init__`` eagerly imports the service, which
imports ``core.store`` and would create an import cycle).
"""

from __future__ import annotations

import re
import unicodedata

from ..core.conditions import CONDITION_BY_CODE
from ..core.metrics import METRIC_LABELS


def _picots_text_matches(
    topic_text: str, extracted_text: str, *, require_qualifiers: bool = True
) -> bool:
    topic_text = _normalize_picots_text(topic_text)
    extracted_text = _normalize_picots_text(extracted_text)
    # Comparator phrases can contain "nutrition intervention" while the
    # actual match is the no-treatment/placebo arm; resolve that explicit
    # overlap before the nutrition-exposure guard below.
    if "no intervention" in topic_text and "no intervention" in extracted_text:
        return True
    if "placebo" in topic_text and "placebo" in extracted_text:
        return True
    aliases = {
        "bp": ("blood", "pressure"),
        "sbp": ("blood", "pressure"),
        "dbp": ("blood", "pressure"),
        "salt": ("sodium",),
        "fat": ("fatty",),
        "men": ("adult",),
        "women": ("adult",),
        "adults": ("adult",),
        "control": ("control", "usual", "alternative"),
        "placebo": ("placebo", "usual", "control"),
        "alternative": ("alternative", "intervention"),
        "supplement": ("supplement", "intervention"),
        "collagen": ("collagen", "protein"),
        "25ohd": ("25", "vitamin"),
        "hydroxyvitamin": ("vitamin",),
    }
    stopwords = {
        "and",
        "or",
        "the",
        "of",
        "with",
        "versus",
        "usual",
        "alternative",
        "aged",
        "older",
        "serum",
        "concentration",
        "dietary",
    }

    def tokens(value: str) -> set[str]:
        raw = re.findall(r"[a-z0-9]+", value.casefold())
        result = set()
        if {"25", "oh", "d"} <= set(raw):
            result.update(("25", "vitamin"))
        for token in raw:
            if len(token) <= 1 or token in stopwords:
                continue
            for canonical in aliases.get(token, (token.rstrip("s"),)):
                canonical = next(
                    (
                        stem
                        for prefix, stem in (
                            ("supplement", "supplement"),
                            ("reduc", "reduce"),
                            ("modif", "modify"),
                        )
                        if canonical.startswith(prefix)
                    ),
                    canonical,
                )
                result.add(canonical)
        return result

    topic_age = re.search(r"\baged\s*(?:≥|>=|>)?\s*(\d{1,3})", topic_text.casefold())
    if topic_age:
        extracted_age = re.search(r"\baged\s*(?:≥|>=|>)?\s*(\d{1,3})", extracted_text.casefold())
        extracted_folded = extracted_text.casefold()
        adult_scope = int(topic_age.group(1)) <= 18 and re.search(r"\badults?\b", extracted_folded)
        # A locked adult-40+ topic may receive an explicit postmenopausal
        # population label; this is a bounded adult proxy, not a generic
        # inference for women or older-sounding text.
        postmenopausal_scope = int(topic_age.group(1)) >= 40 and re.search(
            r"\bpostmenopausal\b", extracted_folded
        )
        alternate_population_scope = _matches_alternate_population_scope(topic_text, extracted_text)
        if (
            not adult_scope
            and not postmenopausal_scope
            and not alternate_population_scope
            and (not extracted_age or int(extracted_age.group(1)) < int(topic_age.group(1)))
        ):
            return False
    topic_duration = re.search(
        r"\b(?:(?:at least|minimum(?: of)?)\s*)?(\d+(?:\.\d+)?)\s*"
        r"(day|week|month|year)s?(?:\s*(?:or|and)\s*(?:longer|more))?",
        topic_text.casefold(),
    )
    if topic_duration:
        extracted_durations = re.findall(
            r"(\d+(?:\.\d+)?)\s*(?:\w+\s+)?(day|week|month|year)s?",
            extracted_text.casefold(),
        )
        if not extracted_durations:
            return False
        weeks = {"day": 1 / 7, "week": 1, "month": 4.345, "year": 52}
        topic_weeks = float(topic_duration.group(1)) * weeks[topic_duration.group(2)]
        if max(float(amount) * weeks[unit] for amount, unit in extracted_durations) < topic_weeks:
            return False
    # A generic nutrition PICOTS describes a class of exposures, not one
    # literal ingredient. Keep an explicit marker requirement so unrelated
    # exercise, medication, or missing-exposure text does not pass.
    nutrition_topic = any(
        marker in topic_text
        for marker in (
            "dietary pattern",
            "defined food",
            "nutrient intervention",
            "nutrition intervention",
            "nutrition component",
        )
    )
    if nutrition_topic:
        nutrition_markers = {
            "diet",
            "dietary",
            "food",
            "ferric",
            "ferrous",
            "iron",
            "nutrient",
            "protein",
            "vitamin",
            "mineral",
            "supplement",
            "collagen",
            "fiber",
            "fibre",
            "fat",
            "oil",
            "salt",
            "sodium",
            "potassium",
            "calcium",
            "soy",
            "isoflavone",
            "barley",
            "grain",
            "fruit",
            "vegetable",
            "milk",
            "tea",
            "coffee",
            "beverage",
            "drink",
            "water",
            "alkaline",
            "electrolyte",
            "omega",
            "probiotic",
            "prebiotic",
        }
        extracted_words = set(re.findall(r"[a-z0-9]+", extracted_text.casefold()))
        return bool(nutrition_markers & extracted_words)
    if re.search(r"\b(?:usual|alternative|placebo)\b", topic_text) and re.search(
        r"\b(?:control|usual|alternative|placebo)\b", extracted_text
    ):
        return True
    topic_tokens = tokens(topic_text)
    extracted_tokens = tokens(extracted_text)
    qualifiers = topic_tokens & {"supplement", "reduce", "modify"}
    if {"usual", "alternative"} & extracted_tokens and {
        "usual",
        "alternative",
    } & topic_tokens:
        return True
    return bool(topic_duration) or (
        bool(topic_tokens & extracted_tokens)
        and (not require_qualifiers or qualifiers <= extracted_tokens)
    )


def _normalize_picots_text(value: str) -> str:
    """Normalize common bilingual extraction terms before PICOTS matching."""

    replacements = {
        "绝经后": "postmenopausal",
        "绝经": "postmenopausal",
        "年龄": "aged ",
        "成年人": "adults",
        "成人": "adults",
        "女性": "women",
        "男性": "men",
        "儿童": "children",
        "老年人": "older adults",
        "肌少症": "sarcopenia",
        "衰弱": "frailty",
        "骨质疏松": "osteoporosis",
        "骨量减少": "osteopenia",
        "骨密度": "bone mineral density",
        "bmd": "bone mineral density",
        "慢性肾脏病": "chronic kidney disease",
        "肾脏病": "kidney disease",
        "肾功能": "kidney function",
        "ckd": "chronic kidney disease",
        "患者": "patients",
        "病人": "patients",
        "缺铁性贫血": "iron deficiency anemia",
        "缺铁": "iron deficiency",
        "贫血": "anemia",
        "ida": "iron deficiency anemia",
        "大麦嫩叶": "barley green",
        "大麦": "barley",
        "电解碱性水": "electrolyzed alkaline water",
        "碱性水": "alkaline water",
        "大豆": "soy",
        "异黄酮": "isoflavone",
        "蛋白质": "protein",
        "营养素": "nutrient",
        "肌醇": "inositol supplement",
        "食物": "food",
        "口服": "oral",
        "铁": "iron",
        "饮用": "drink",
        "服用": "consume",
        "摄入": "intake",
        "平衡膳食": "usual diet",
        "纯净中性水": "control water",
        "中性水": "control water",
        "碳酸氢钠": "sodium bicarbonate",
        "氯化钠": "sodium chloride",
        "低钠高钾盐替代品": "low sodium high potassium salt substitute",
        "盐替代品": "salt substitute",
        "低钠": "low sodium",
        "高钾": "high potassium",
        "钠摄入": "sodium intake",
        "钾摄入": "potassium intake",
        "中链甘油三酯油": "medium chain triglyceride oil",
        "膳食油脂": "dietary oils and solid fats",
        "固体脂肪": "solid fat",
        "油脂": "oil fat",
        "黄油": "butter",
        "椰子油": "coconut oil",
        "橄榄油": "olive oil",
        "油": "oil",
        "脂肪": "fat",
        "普通盐": "control salt",
        "收缩压": "systolic blood pressure",
        "舒张压": "diastolic blood pressure",
        "减少": "reduction",
        "降低": "reduction",
        "胆钙化醇": "cholecalciferol vitamin d",
        "胶原蛋白肽": "collagen protein peptide",
        "胶原蛋白": "collagen protein",
        "蛋白质补充剂": "protein supplementation",
        "膳食": "dietary",
        "饮食": "dietary",
        "对照组": "control",
        "普通鲜奶": "control milk",
        "普通牛奶": "control milk",
        "常规牛奶": "control milk",
        "对照牛奶": "control milk",
        "no treatment": "no intervention",
        "no-treatment": "no intervention",
        "regular milk": "control milk",
        "plain milk": "control milk",
        "安慰剂": "placebo",
        "常规护理": "usual care",
        "标准治疗": "standard care",
        "相互比较": "alternative",
        "补充": "supplement",
        "肌酐清除率": "creatinine clearance",
        "血清肌酐": "serum creatinine",
        "尿白蛋白肌酐比": "urine albumin creatinine ratio",
        "营养不良": "malnutrition",
        "便秘": "constipation",
        "血压": "blood pressure",
        "握力": "grip strength",
        "步速": "gait speed",
        "肌肉量": "muscle mass",
        "肌肉质量": "muscle mass",
        "骨骼肌质量": "skeletal muscle mass",
        "软瘦肉组织": "soft lean mass",
        "运动": "exercise",
        "钙": "calcium",
        "单独": "alternative",
        "血红蛋白": "hemoglobin",
        "铁蛋白": "ferritin",
        "尿酸": "uric acid",
        "甘油三酯": "triglycerides",
        "高密度脂蛋白胆固醇": "hdl cholesterol",
        "低密度脂蛋白胆固醇": "ldl cholesterol",
        "总胆固醇": "total cholesterol",
        "空腹血糖": "fasting glucose",
        "糖化血红蛋白": "hba1c",
        "维生素\u00a0d": "vitamin d",
        "维生素d": "vitamin d",
        "masld": "metabolic risk",
        "nafld": "metabolic risk",
        "周": " weeks ",
        "月": " months ",
        "年": " years ",
        "天": " days ",
        "岁": " years ",
    }
    normalized = value.casefold()
    normalized = re.sub(
        r"(\d{1,3})\s*岁\s*(?:或|及)?以上",
        lambda match: f" aged {match.group(1)} years and older ",
        normalized,
    )
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    duration_units = {"天": "days", "周": "weeks", "月": "months", "年": "years"}

    def replace_duration(match: re.Match[str]) -> str:
        raw, unit = match.groups()
        if "十" in raw:
            left, _, right = raw.partition("十")
            number = digits.get(left, 1) * 10 + digits.get(right, 0)
        else:
            number = digits[raw]
        return f" {number} {duration_units[unit]} "

    normalized = re.sub(
        r"([一二三四五六七八九十]+)\s*个?\s*(天|周|月|年)", replace_duration, normalized
    )
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(source, f" {target.strip()} ")
    return normalized


def _matches_alternate_population_scope(topic_text: str, extracted_text: str) -> bool:
    """Allow explicit non-age alternatives in versioned population PICOTS."""

    if re.search(r"\bchildren?\b", extracted_text):
        return False
    alternatives = {
        "kidney disease risk": ("kidney disease", "chronic kidney disease", "ckd"),
        "metabolic risk": (
            "metabolic risk",
            "metabolic dysfunction",
            "masld",
            "fatty liver",
            "obesity",
            "diabetes",
        ),
        "nutritional risk": ("nutritional risk", "malnutrition", "undernutrition"),
        "sarcopenia/frailty": ("sarcopenia", "frailty"),
    }
    if "nutritional risk" in topic_text and any(
        marker in extracted_text
        for marker in ("mna-sf", "mini nutritional assessment", "mna score")
    ):
        return True
    return any(
        marker in topic_text and any(term in extracted_text for term in terms)
        for marker, terms in alternatives.items()
    )


def _profile_scope_matches(picots: object, dimensions: dict[str, str]) -> bool:
    if not isinstance(picots, dict):
        return False
    for topic_field, result_field in (
        ("population", "population"),
        ("intervention_or_exposure", "ingredient_name"),
        ("outcomes", "outcome"),
        ("timing", "timepoint"),
    ):
        topic_value = str(picots.get(topic_field) or "").strip()
        result_value = dimensions[result_field].strip()
        if topic_field == "intervention_or_exposure":
            result_value = " ".join(
                value
                for key in ("ingredient_name", "ingredient_form", "dose")
                if (value := dimensions.get(key, "")).strip()
            )
        if topic_value and (
            not result_value
            or not _picots_text_matches(topic_value, result_value, require_qualifiers=False)
        ):
            return False
    return True


_PROFILE_OUTCOME_ALIASES = {
    "systolic_blood_pressure": (
        "systolicbloodpressure",
        "sbp",
        "收缩压",
    ),
    "diastolic_blood_pressure": (
        "diastolicbloodpressure",
        "dbp",
        "舒张压",
    ),
    "triglycerides": ("triglyceride", "triglycerides", "甘油三酯"),
    "hdl_c": (
        "hdlc",
        "hdlcholesterol",
        "highdensitylipoproteincholesterol",
        "高密度脂蛋白胆固醇",
    ),
    "ldl_c": (
        "ldlc",
        "ldlcholesterol",
        "lowdensitylipoproteincholesterol",
        "低密度脂蛋白胆固醇",
    ),
    "total_cholesterol": ("totalcholesterol", "总胆固醇"),
    "non_hdl_c": (
        "nonhdlc",
        "nonhdlcholesterol",
        "nonhighdensitylipoproteincholesterol",
        "非高密度脂蛋白胆固醇",
    ),
    "fasting_glucose": ("fastingglucose", "fastingbloodglucose", "fbg", "空腹血糖"),
    "hba1c": ("hba1c", "glycatedhemoglobin", "glycosylatedhemoglobin", "糖化血红蛋白"),
    "alt": ("alanineaminotransferase", "alaninetransaminase", "alt", "丙氨酸氨基转移酶"),
    "ast": ("aspartateaminotransferase", "aspartatetransaminase", "ast", "天门冬氨酸氨基转移酶"),
    "ggt": ("gammaglutamyltransferase", "gammaglutamyltranspeptidase", "ggt", "谷氨酰转移酶"),
    "uric_acid": ("uricacid", "serumuricacid", "urate", "尿酸"),
    "egfr": ("egfr", "estimatedglomerularfiltrationrate", "估算肾小球滤过率"),
    "creatinine": ("creatinine", "serumcreatinine", "肌酐"),
    "uacr": (
        "uacr",
        "urinealbumincreatinineratio",
        "urinealbumintocreatinineratio",
        "urinaryalbumincreatinineratio",
        "urinaryalbumintocreatinineratio",
        "尿白蛋白肌酐比",
    ),
    "hemoglobin": ("hemoglobin", "haemoglobin", "血红蛋白"),
    "mcv": ("mcv", "meancorpuscularvolume", "平均红细胞体积"),
    "ferritin": ("ferritin", "serumferritin", "铁蛋白"),
    "tsat": ("tsat", "transferrinsaturation", "转铁蛋白饱和度"),
    "25_oh_vitamin_d": (
        "25ohd",
        "25hydroxyvitamind",
        "25羟维生素d",
    ),
    "bone_density_t_score": (
        "tscore",
        "bonedensitytscore",
        "bmdtscore",
        "骨密度t值",
        "骨密度t评分",
        "t评分",
        "t值",
    ),
    "calcium": ("calcium", "serumcalcium", "bloodcalcium", "血钙", "钙"),
    "alp": ("alkalinephosphatase", "alp", "碱性磷酸酶"),
    "grip_strength": ("gripstrength", "handgripstrength", "握力"),
    "walking_speed": (
        "walkingspeed",
        "6mwalkingspeed",
        "gaitspeed",
        "walkingperformance",
        "步行速度",
        "步速",
    ),
    "muscle_mass": (
        "musclemass",
        "skeletalmusclemass",
        "appendicularskeletalmusclemass",
        "skeletalmusclemassindex",
        "appendicularskeletalmusclemassindex",
        "asmm",
        "smm",
        "smi",
        "leanmass",
        "fatfreemass",
        "softleanmass",
        "肌肉量",
        "肌肉质量",
        "骨骼肌质量",
        "软瘦肉组织",
    ),
    "albumin": ("albumin", "serumalbumin", "白蛋白"),
    "bmi": ("bodymassindex", "bmi", "体重指数"),
    "prealbumin": ("prealbumin", "transthyretin", "前白蛋白"),
}


def _compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", value).casefold())


def _metric_outcome_matches(metric_code: str, value: str) -> bool:
    compact = _compact_text(value)
    if metric_code in {"hdl_c", "ldl_c", "total_cholesterol"} and "ratio" in value.casefold():
        return False
    if metric_code == "hdl_c" and "nonhdl" in compact:
        return False
    if metric_code == "creatinine" and any(
        term in compact
        for term in (
            "clearance",
            "ratio",
            "uacr",
            "urine",
            "urinary",
            "excretion",
            "清除率",
            "比值",
            "尿",
        )
    ):
        return False
    if metric_code == "bone_density_t_score" and not any(
        token in compact for token in ("tscore", "t评分", "t值")
    ):
        return False
    aliases = _PROFILE_OUTCOME_ALIASES.get(metric_code, ())
    risky_abbreviations = {"alt", "ast", "alp"}
    if any(alias in compact for alias in aliases if alias not in risky_abbreviations):
        return True
    tokens = set(re.findall(r"[0-9a-z]+", unicodedata.normalize("NFKC", value).casefold()))
    if any(alias in tokens for alias in aliases if alias in risky_abbreviations):
        return True
    if metric_code == "systolic_blood_pressure":
        return "systolic" in compact and "bloodpressure" in compact
    if metric_code == "diastolic_blood_pressure":
        return "diastolic" in compact and "bloodpressure" in compact
    return False


def _topic_outcome_components(value: str) -> tuple[str, ...]:
    components = tuple(
        item.strip(" .")
        for item in re.split(r"\s*(?:[,;，；]|\b(?:and|or)\b|和|或)\s*", value, flags=re.I)
        if item.strip(" .")
    )
    return components or (value.strip(),)


def _metric_outcome_matches_text(metric_code: str, value: str) -> bool:
    """Match a metric in either a single or a compound reported outcome."""

    return _metric_outcome_matches(metric_code, value) or any(
        _metric_outcome_matches(metric_code, component)
        for component in _topic_outcome_components(value)
    )





def _generic_scope_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    slug = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", normalized).strip("-")
    return f"outcome:{slug[:140]}"


def _profile_scopes(
    picots: object, condition_code: str, dimensions: dict[str, object]
) -> dict[str, str]:
    if not isinstance(picots, dict):
        return {}
    values = {key: str(value or "") for key, value in dimensions.items()}
    if not _profile_scope_matches(picots, values):
        return {}
    topic_outcome = str(picots.get("outcomes") or "").strip()
    result_outcome = values.get("outcome", "")
    compact_result = _compact_text(result_outcome)
    condition = CONDITION_BY_CODE.get(condition_code)
    if condition and not condition.metrics:
        if _picots_text_matches(topic_outcome, result_outcome, require_qualifiers=False):
            return {f"condition:{condition_code}": condition.name}
        return {}
    lipid_metrics = {"hdl_c", "ldl_c", "total_cholesterol"}
    if (
        condition
        and lipid_metrics.intersection(condition.metrics)
        and re.search(r"\bratio\b", result_outcome.casefold())
        and "nonhdl" not in compact_result
    ):
        return {}
    scopes = {
        f"metric:{metric_code}": METRIC_LABELS[metric_code]
        for metric_code in (condition.metrics if condition else ())
        if metric_code in _PROFILE_OUTCOME_ALIASES
        and _metric_outcome_matches_text(metric_code, topic_outcome)
        and _metric_outcome_matches_text(metric_code, result_outcome)
    }
    for component in _topic_outcome_components(topic_outcome):
        if (
            _metric_outcome_matches("bone_density_t_score", result_outcome)
            and _compact_text(component) in {"bonemineraldensity", "bmd"}
        ):
            continue
        if condition and any(
            _metric_outcome_matches_text(metric_code, component)
            for metric_code in condition.metrics
        ):
            continue
        if _picots_text_matches(component, result_outcome, require_qualifiers=False):
            scopes[_generic_scope_key(component)] = component
    return scopes


def _synthesis_dimensions(picots: object, outcome: str) -> dict[str, str]:
    if not isinstance(picots, dict):
        raise ValueError("locked topic PICOTS is required")
    return {
        "population": str(picots.get("population") or "Not restricted"),
        "baseline_nutrient_status": "Mixed or not restricted by the locked topic",
        "ingredient_name": str(picots.get("intervention_or_exposure") or "Not specified"),
        "ingredient_form": "As reported across included studies",
        "dose": "As reported across included studies",
        "comparator": str(picots.get("comparator") or "Not specified"),
        "outcome": outcome,
        "timepoint": str(picots.get("timing") or "As reported across included studies"),
    }


def _resolve_profile_scope(
    picots: object,
    condition_code: str,
    rows: list[dict[str, object]],
    *,
    requested: str,
    estimate_target: str,
) -> tuple[str, str]:
    scopes = [_profile_scopes(picots, condition_code, row) for row in rows]
    common = set(scopes[0]) if scopes else set()
    for item in scopes[1:]:
        common &= set(item)
    if requested:
        if requested not in common:
            raise ValueError("selected claims do not share the requested outcome scope")
        return requested, scopes[0][requested]
    if len(common) == 1:
        key = common.pop()
        return key, scopes[0][key]
    target_matches = [
        key
        for key in common
        if _picots_text_matches(scopes[0][key], estimate_target, require_qualifiers=False)
    ]
    if len(target_matches) == 1:
        key = target_matches[0]
        return key, scopes[0][key]
    raise ValueError("evidence profile requires one explicit shared outcome scope")



class EvidenceProfileScopeResolver:
    """Stateless facade over the deterministic PICOTS-to-scope decision chain.

    Every method is a pure function of its inputs; the resolver holds no state
    and imports no storage or store classes. Store methods and the autonomous
    review service call this surface instead of re-implementing bilingual
    normalization, metric aliasing, or scope-key derivation locally.
    """

    def picots_matches(
        self, topic_text: str, extracted_text: str, *, require_qualifiers: bool = True
    ) -> bool:
        """Decide whether extracted text matches a locked-topic PICOTS field."""

        return _picots_text_matches(
            topic_text, extracted_text, require_qualifiers=require_qualifiers
        )

    def profile_scope_matches(self, picots: object, dimensions: dict[str, str]) -> bool:
        """Decide whether a result's dimensions share the locked topic's scope."""

        return _profile_scope_matches(picots, dimensions)

    def metric_outcome_matches_text(self, metric_code: str, value: str) -> bool:
        """Decide whether a (possibly compound) reported outcome matches a metric."""

        return _metric_outcome_matches_text(metric_code, value)

    def profile_scopes(
        self, picots: object, condition_code: str, dimensions: dict[str, object]
    ) -> dict[str, str]:
        """Derive the candidate scope keys/labels for one eligible dimension set."""

        return _profile_scopes(picots, condition_code, dimensions)

    def resolve_profile_scope(
        self,
        picots: object,
        condition_code: str,
        rows: list[dict[str, object]],
        *,
        requested: str,
        estimate_target: str,
    ) -> tuple[str, str]:
        """Resolve a set of eligible rows into exactly one shared scope."""

        return _resolve_profile_scope(
            picots,
            condition_code,
            rows,
            requested=requested,
            estimate_target=estimate_target,
        )

    def population_text(self, extraction_json: object) -> str:
        """Join the paper-level population list from extraction JSON (pure)."""

        if not isinstance(extraction_json, dict):
            return ""
        population = extraction_json.get("population")
        if not isinstance(population, list):
            return ""
        return " ".join(str(value).strip() for value in population if str(value).strip())
