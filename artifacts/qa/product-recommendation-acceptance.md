# Product Recommendation Acceptance Result

- Parent requirement: `#103`
- Tested commit: `d703b3e4df2a32bf885a3091998082ae4176559a`
- Build identity: `local-uv-d703b3e`
- Executed at: `2026-08-28T18:26:35+08:00`
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

- Full deterministic suite: `281 passed`; Ruff, scope, schema, patient-copy, and product-catalog guards passed.
- Focused acceptance suite: `38 passed`.
- Real catalog migration: `43` blocked products, `4` published recommendations, `576` source records, `0` products without sources.
- Real workbook draft: `12` condition mappings created with status `in_review`.
- Focused product-module coverage: `92%` total (`catalog.py` 93%, `mapping_drafts.py` 87%, `recommendations.py` 91%).
- Complexity: average grade `A`; highest changed methods grade `C` and are covered by API and end-to-end tests.
- OCR delegation review: 19/19 reviewable files reviewed; the excluded Markdown ADR was reviewed manually; no remaining confirmed findings.

## Notes

- One non-blocking Starlette warning reports that `TestClient` currently uses a deprecated `httpx` integration.
- Mutation testing was not run because the repository has no configured mutation paths or baseline. Focused coverage, real-data migration, deterministic guards, and the end-to-end test are retained instead; add a bounded mutation configuration before making it a required gate.
- Test data and temporary databases were removed after execution.
