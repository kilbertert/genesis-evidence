# Product Recommendation Acceptance Result

- Parent requirement: `#103`
- Tested commit: `389a7cff9b1e648f2fb7e05a13ac1221f47c79f3`
- Build identity: `local-uv-389a7cf`
- Executed at: `2026-08-28T19:08:51+08:00`
- Environment: Linux x86_64, Python 3.13.13, uv 0.11.14, temporary SQLite/Object Store

## Results

| QA ID | Result | Deterministic evidence |
| --- | --- | --- |
| `QA-PUB-001` | PASS | Report and Evidence API tests return ordered published recommendations with required fields and `product_status=available`. |
| `QA-EXCL-002` | PASS | Recommendation and review API tests exclude blocked, withdrawn, manually flagged, and automatically detected high-risk products. |
| `QA-URG-003` | PASS | Recommendation tests suppress urgent, emergency, and severity-3 findings. |
| `QA-FORBID-004` | PASS | Patient-copy guard scans portal and product recommendation sources; injected forbidden copy fails the guard. |
| `QA-EMPTY-005` | PASS | Patient output returns an empty recommendation list and `暂无推荐`. |
| `QA-E2E-001` | PASS | Automated upload -> confirm -> assess -> review -> publish -> patient response -> withdraw flow passed. |

## Commands And Outputs

- Full deterministic suite: `308 passed`; Ruff, scope, schema, patient-copy, and product-catalog guards passed.
- Focused acceptance suite: `45 passed` (`tests/test_evidence_api.py`, `tests/test_report_assessment.py`, and product recommendation/catalog/review suites).
- Real catalog migration: `43` blocked products, `4` published recommendations, `576` source records, `0` products without sources.
- Real workbook draft: `12` condition mappings created with status `in_review`.
- Focused product-module coverage: `92%` total (`catalog.py` 93%, `mapping_drafts.py` 87%, `recommendations.py` 92%).
- Complexity: average grade `C (11.5)` for `core/matching.py`; `validate_number` and `EvidenceMatcher.match_published_cards` are grade `C` and covered by API and end-to-end tests.
- Mutation testing: `436/445` mutants killed; `9` survived in matcher ranking/unmatched edge branches and are retained as a documented residual test gap.
- OCR delegation review: 22/22 code files reviewed; 3 excluded Markdown files reviewed manually. One confirmed old-database seed fallback defect was fixed in `389a7cf`; no confirmed findings remain.

## Notes

- One non-blocking Starlette warning reports that `TestClient` currently uses a deprecated `httpx` integration.
- Mutation testing was run with the configured `core/matching.py` scope. Nine surviving mutants are limited to ranking and v2 unmatched edge branches and remain a recorded residual test gap.
- Upgrade coverage verifies that pre-existing published seed rows with an empty migrated `recommendation_json` retain patient-safe recommendation metadata.
- Test data and temporary databases were removed after execution.
