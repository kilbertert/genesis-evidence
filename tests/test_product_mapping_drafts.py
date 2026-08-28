from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.products.mapping_drafts import create_mapping_drafts
from genesis_evidence.products.recommendations import load_published_products
from genesis_evidence.review.api import create_app

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
REVIEWER = "nutrition-reviewer-1"

PRODUCT_NAMES = (
    "护心素胶囊",
    "百糖伏®益生菌胶囊",
    "郅臻堂®植物甾醇咀嚼片",
    "奶蓟硫辛酸胶囊",
    "复合槲皮素胶囊",
    "超级维BC片",
    "活性叶酸胶囊",
    "天然维生素D3片",
    "复合柠檬酸钙胶囊",
    "复合全骨营养餐",
    "娇韵思®超高浓缩果蔬纤维粉",
)

CATEGORIES = (
    ("心脑血管养护", "血压管理"),
    ("心脑血管养护", "血糖管理"),
    ("心脑血管养护", "血脂管理"),
    ("心脑血管养护", "尿酸管理"),
    ("肝脏解毒养护", "脂肪肝"),
    ("基础营养补充", "多维多矿"),
    ("基础营养补充", "蛋白/氨基酸"),
    ("骨骼关节肌肉", "骨密度"),
    ("骨骼关节肌肉", "骨骼肌流失"),
    ("胃肠道消化健康", "促进排便"),
)


def _workbook(path) -> None:
    rows = [
        "<row>"
        f'<c r="B1" t="inlineStr"><is><t>{direction}</t></is></c>'
        f'<c r="C1" t="inlineStr"><is><t>{category}</t></is></c>'
        "</row>"
        for direction, category in CATEGORIES
    ]
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rows)}</sheetData></worksheet>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def _database(tmp_path) -> Database:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO product_candidates(
                id, canonical_key, name_zh, content_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, '{}', 'blocked', 'now', 'now')
            """,
            [
                (f"product-{index}", f"product-{index}", name)
                for index, name in enumerate(PRODUCT_NAMES, start=1)
            ],
        )
    return database


def test_excel_seed_creates_twelve_review_pending_mapping_drafts(tmp_path) -> None:
    database = _database(tmp_path)
    workbook = tmp_path / "分类.xlsx"
    _workbook(workbook)

    summary = create_mapping_drafts(
        database,
        workbook,
        actor="ai:offline-mapper",
        source_ref="2026 classification workbook",
    )

    assert summary == {"condition_count": 12, "draft_count": 12}
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT condition_code, status, draft_method
            FROM product_mapping_drafts ORDER BY condition_code
            """
        ).fetchall()
    assert len(rows) == 12
    assert {row["condition_code"] for row in rows} == {
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
    assert {row["status"] for row in rows} == {"in_review"}
    assert {row["draft_method"] for row in rows} == {"offline-ai-draft-v1"}


def test_reviewer_accepts_rejects_or_requests_more_mapping_information(tmp_path) -> None:
    database = _database(tmp_path)
    workbook = tmp_path / "分类.xlsx"
    _workbook(workbook)
    create_mapping_drafts(database, workbook, actor="ai:offline-mapper", source_ref="fixture")
    client = TestClient(
        create_app(database_path=database.path, api_key=API_KEY, reviewer_id=REVIEWER)
    )
    drafts = client.get("/api/review/product-mappings", headers=HEADERS).json()
    by_condition = {item["condition_code"]: item for item in drafts}
    assert by_condition["COND_CHRONIC_CONSTIPATION"]["recommendation"]["reason"]

    published = client.post(
        f"/api/review/product-mappings/{by_condition['COND_CHRONIC_CONSTIPATION']['id']}/transition",
        headers=HEADERS,
        json={"target": "published", "note": "批准映射", "decision_ref": "mapping-1"},
    )
    rejected = client.post(
        f"/api/review/product-mappings/{by_condition['COND_CKD_RISK']['id']}/transition",
        headers=HEADERS,
        json={"target": "rejected", "note": "不适合当前范围", "decision_ref": "mapping-2"},
    )
    more_info = client.post(
        f"/api/review/product-mappings/{by_condition['COND_PREDIABETES']['id']}/transition",
        headers=HEADERS,
        json={
            "target": "needs_more_info",
            "note": "补充法规信息",
            "decision_ref": "mapping-3",
        },
    )

    assert published.json()["status"] == "published"
    assert rejected.json()["status"] == "rejected"
    assert more_info.json()["status"] == "needs_more_info"
    cannot_reject_published = client.post(
        f"/api/review/product-mappings/{by_condition['COND_CHRONIC_CONSTIPATION']['id']}/transition",
        headers=HEADERS,
        json={"target": "rejected", "note": "迟到驳回", "decision_ref": "mapping-4"},
    )
    assert cannot_reject_published.status_code == 400
    with database.connect() as connection:
        products = load_published_products(connection)
    constipation = next(item for item in products if item["product_id"] == "product-11")
    assert constipation["condition_codes"] == ["COND_CHRONIC_CONSTIPATION"]

    create_mapping_drafts(database, workbook, actor="ai:offline-mapper", source_ref="fixture")
    rerun_status = {
        item["condition_code"]: item["status"]
        for item in client.get("/api/review/product-mappings", headers=HEADERS).json()
    }
    assert rerun_status["COND_CHRONIC_CONSTIPATION"] == "published"
    assert rerun_status["COND_CKD_RISK"] == "rejected"
    assert rerun_status["COND_PREDIABETES"] == "needs_more_info"


def test_publishing_multiple_drafts_for_one_product_preserves_all_conditions(tmp_path) -> None:
    database = _database(tmp_path)
    workbook = tmp_path / "分类.xlsx"
    _workbook(workbook)
    create_mapping_drafts(database, workbook, actor="ai:offline-mapper", source_ref="fixture")
    client = TestClient(
        create_app(database_path=database.path, api_key=API_KEY, reviewer_id=REVIEWER)
    )
    drafts = client.get("/api/review/product-mappings", headers=HEADERS).json()
    by_condition = {item["condition_code"]: item for item in drafts}

    for condition_code in ("COND_SARCOPENIA_FRAILTY", "COND_MALNUTRITION_RISK"):
        response = client.post(
            f"/api/review/product-mappings/{by_condition[condition_code]['id']}/transition",
            headers=HEADERS,
            json={
                "target": "published",
                "note": "批准映射",
                "decision_ref": f"mapping-{condition_code}",
            },
        )
        assert response.json()["status"] == "published"

    with database.connect() as connection:
        product = next(
            item
            for item in load_published_products(connection)
            if item["product_id"] == "product-10"
        )
    assert product["condition_codes"] == [
        "COND_MALNUTRITION_RISK",
        "COND_SARCOPENIA_FRAILTY",
    ]


def test_high_risk_mapping_cannot_be_published(tmp_path) -> None:
    database = _database(tmp_path)
    workbook = tmp_path / "分类.xlsx"
    _workbook(workbook)
    create_mapping_drafts(database, workbook, actor="ai:offline-mapper", source_ref="fixture")
    client = TestClient(
        create_app(database_path=database.path, api_key=API_KEY, reviewer_id=REVIEWER)
    )
    draft = next(
        item
        for item in client.get("/api/review/product-mappings", headers=HEADERS).json()
        if item["condition_code"] == "COND_PREDIABETES"
    )

    response = client.post(
        f"/api/review/product-mappings/{draft['id']}/transition",
        headers=HEADERS,
        json={"target": "published", "note": "尝试批准", "decision_ref": "mapping-risk"},
    )

    assert response.json()["status"] == "needs_more_info"
    with database.connect() as connection:
        assert all(
            "COND_PREDIABETES" not in item["condition_codes"]
            for item in load_published_products(connection)
        )
