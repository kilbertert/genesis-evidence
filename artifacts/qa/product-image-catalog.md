# Published Product Image Catalog

This artifact records the image binding delivered with the recommendation contract.

## Scope

- The supplier PDFs produced 43 candidate products.
- 10 products currently have reviewed, published recommendation mappings.
- All 10 published mappings use one of the 12 defined `COND_*` condition codes.
- The remaining 33 candidates stay `blocked` and are not exposed to patients.

## Published bindings

| Product | Condition code(s) | Image URL | Source visual |
| --- | --- | --- | --- |
| 郅臻堂®植物甾醇咀嚼片 | `COND_DYSLIPIDEMIA` | `/products/zhizhen-plant-sterol.png` | Single-product PDF, p. 3 |
| 复合全骨营养餐 | `COND_MALNUTRITION_RISK`, `COND_SARCOPENIA_FRAILTY` | `/products/whole-bone-nutrition-meal.png` | Single-product PDF cover |
| 复合柠檬酸钙胶囊 | `COND_OSTEOPOROSIS_RISK`, `COND_VITAMIN_D_DEFICIENCY` | `/products/calcium-citrate.png` | Product brochure, p. 20 |
| 复合槲皮素胶囊 | `COND_HYPERURICEMIA_RISK` | `/products/quercetin.png` | Product brochure, p. 20 |
| 天然维生素D3片 | `COND_OSTEOPOROSIS_RISK`, `COND_VITAMIN_D_DEFICIENCY` | `/products/vitamin-d3.png` | Product brochure, p. 21 |
| 奶蓟硫辛酸胶囊 | `COND_MASLD_RISK` | `/products/milk-thistle-alpha-lipoic.png` | Product brochure, p. 18 |
| 娇韵思®超高浓缩果蔬纤维粉 | `COND_CHRONIC_CONSTIPATION` | `/products/joyees-fruit-vegetable-fiber.png` | Product brochure, p. 7 |
| 护心素胶囊 | `COND_HYPERTENSION_RISK` | `/products/cardiotonic-element.png` | Product brochure, p. 19 |
| 活性叶酸胶囊 | `COND_ANEMIA_PATTERN` | `/products/active-folate.png` | Product brochure, p. 22 |
| 超级维BC片 | `COND_CKD_RISK` | `/products/super-bc.png` | Product brochure, p. 22 |

Images are cropped from supplier-provided materials marked for internal review. The whole-bone PDF contains no package photograph, so its source-backed cover visual is used and is not presented as a package label.

## Verification

- Tested commit: `0a43dad0d7b88cdbf8f0f1ba402cd179a323f63a`
- Executed at: `2026-08-29T02:30:10+08:00`
- Environment: Linux x86_64, Python 3.13.13, Ruff 0.16.2, mutmut 3.7.0
- Result: PASS. All 10 published products resolve to distinct same-origin image URLs, including normalized product names with spaces. The remaining 33 candidates stay blocked.
- Deterministic checks: `PYTHONPATH=src uv run pytest` passed (`313 passed`); `uv run ruff check .` passed; focused catalog and recommendation tests passed (`17 passed`).
- Risk checks: changed product modules average complexity A (3.88); the four targeted image URL and metadata-key mutants were all killed. The full configured baseline reported 1,753 mutants, including pre-existing survivors outside this change.
