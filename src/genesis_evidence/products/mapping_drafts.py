"""Persist the offline AI draft of the 12-condition product mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from ..core.store.database import Database
from .catalog import _has_high_risk_marketing_claim, _now

_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True, slots=True)
class MappingSeed:
    condition_code: str
    direction: str
    category: str
    product_name: str
    high_risk: bool = False


MAPPING_SEEDS = (
    MappingSeed("COND_HYPERTENSION_RISK", "心脑血管养护", "血压管理", "护心素胶囊"),
    MappingSeed(
        "COND_PREDIABETES",
        "心脑血管养护",
        "血糖管理",
        "百糖伏®益生菌胶囊",
        high_risk=True,
    ),
    MappingSeed(
        "COND_DYSLIPIDEMIA", "心脑血管养护", "血脂管理", "郅臻堂®植物甾醇咀嚼片"
    ),
    MappingSeed("COND_MASLD_RISK", "肝脏解毒养护", "脂肪肝", "奶蓟硫辛酸胶囊"),
    MappingSeed(
        "COND_HYPERURICEMIA_RISK", "心脑血管养护", "尿酸管理", "复合槲皮素胶囊"
    ),
    MappingSeed("COND_CKD_RISK", "基础营养补充", "多维多矿", "超级维BC片"),
    MappingSeed("COND_ANEMIA_PATTERN", "基础营养补充", "多维多矿", "活性叶酸胶囊"),
    MappingSeed(
        "COND_VITAMIN_D_DEFICIENCY", "基础营养补充", "多维多矿", "天然维生素D3片"
    ),
    MappingSeed(
        "COND_OSTEOPOROSIS_RISK", "骨骼关节肌肉", "骨密度", "复合柠檬酸钙胶囊"
    ),
    MappingSeed(
        "COND_SARCOPENIA_FRAILTY", "骨骼关节肌肉", "骨骼肌流失", "复合全骨营养餐"
    ),
    MappingSeed(
        "COND_MALNUTRITION_RISK", "基础营养补充", "蛋白/氨基酸", "复合全骨营养餐"
    ),
    MappingSeed(
        "COND_CHRONIC_CONSTIPATION",
        "胃肠道消化健康",
        "促进排便",
        "娇韵思®超高浓缩果蔬纤维粉",
    ),
)


def create_mapping_drafts(
    database: Database,
    workbook: Path,
    *,
    actor: str,
    source_ref: str,
) -> dict[str, int]:
    categories = read_function_categories(workbook)
    missing = sorted(
        {(seed.direction, seed.category) for seed in MAPPING_SEEDS} - categories
    )
    if missing:
        raise ValueError(f"classification workbook is missing categories: {missing}")
    now = _now()
    with database.transaction() as connection:
        products = {
            str(row["name_zh"]): (str(row["id"]), str(row["content_json"]))
            for row in connection.execute(
                "SELECT id, name_zh, content_json FROM product_candidates"
            )
        }
        for seed in MAPPING_SEEDS:
            product = products.get(seed.product_name)
            if product is None:
                raise ValueError(f"candidate product not found: {seed.product_name}")
            product_id, content_json = product
            metadata = {
                "nutrient": seed.category,
                "reason": f"{seed.category}可作为该健康风险相关的膳食补充方向考虑。",
                "safety_message": "请结合个人情况咨询专业人士。",
                "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
                "evidence_links": [f"classification:{source_ref}#{seed.category}"],
                "evidence_strength": "low",
                "priority": 10,
                "high_risk_marketing_claim": seed.high_risk
                or _has_high_risk_marketing_claim(content_json),
                "version": 0,
            }
            draft_id = f"mapping-draft:{seed.condition_code}:{product_id}"
            connection.execute(
                """
                INSERT INTO product_mapping_drafts(
                    id, condition_code, functional_direction, functional_category,
                    product_id, recommendation_json, status, source_ref, draft_method,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_review', ?, 'offline-ai-draft-v1', ?, ?, ?)
                ON CONFLICT(condition_code, product_id, functional_category) DO UPDATE SET
                    functional_direction = excluded.functional_direction,
                    recommendation_json = excluded.recommendation_json,
                    source_ref = excluded.source_ref,
                    draft_method = excluded.draft_method,
                    updated_at = excluded.updated_at
                """,
                (
                    draft_id,
                    seed.condition_code,
                    seed.direction,
                    seed.category,
                    product_id,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    source_ref,
                    actor,
                    now,
                    now,
                ),
            )
    return {
        "condition_count": len({seed.condition_code for seed in MAPPING_SEEDS}),
        "draft_count": len(MAPPING_SEEDS),
    }


def read_function_categories(workbook: Path) -> set[tuple[str, str]]:
    with ZipFile(workbook) as archive:
        shared = _shared_strings(archive)
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    categories: set[tuple[str, str]] = set()
    current_direction = ""
    for row in sheet.findall(".//x:sheetData/x:row", _NS):
        cells = {_cell_column(cell): _cell_text(cell, shared) for cell in row.findall("x:c", _NS)}
        direction = cells.get("B", "").strip() or current_direction
        category = cells.get("C", "").strip()
        if cells.get("B", "").strip():
            current_direction = cells["B"].strip()
        if direction and category and category != "类别":
            categories.add((direction, category))
    return categories


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.findall(".//x:t", _NS))
        for item in root.findall("x:si", _NS)
    ]


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", _NS))
    value = cell.find("x:v", _NS)
    if value is None or value.text is None:
        return ""
    return shared[int(value.text)] if cell.get("t") == "s" else value.text


def _cell_column(cell: ElementTree.Element) -> str:
    return "".join(character for character in cell.get("r", "") if character.isalpha())
