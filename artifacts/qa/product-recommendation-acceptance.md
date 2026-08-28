# Product Recommendation Acceptance Result

- Parent requirement: `#103`
- Tested Genesis commit: `49d7639c6da343f913f44d1c38560b7e93129861`
- Tested HealthFlow commit: `b44f3652fc9f0c2b8e0bfa4d073d0d46ea2d9f57`
- Build identity: `local-uv-49d7639-healthflow-b44f365`
- Executed at: `2026-08-28T20:02:50+08:00`
- Environment: Linux x86_64, Python 3.13.13, uv 0.11.14, Node 24.15.0, isolated SQLite and loopback services

## Results

| QA ID | Result | Deterministic evidence |
| --- | --- | --- |
| `QA-PUB-001` | PASS | Report and Evidence API tests return ordered published recommendations with all required patient-visible fields. |
| `QA-EXCL-002` | PASS | Blocked, withdrawn, supplier-claim-risk, and risk-flag products cannot become patient-visible recommendations. |
| `QA-URG-003` | PASS | Urgent, emergency, severity-3, and known under-40 findings suppress recommendations. |
| `QA-FORBID-004` | PASS | Patient-copy guard rejects forbidden treatment or cure claims. |
| `QA-EMPTY-005` | PASS | No safe published match returns `recommendations=[]` and `暂无推荐`. |
| `QA-E2E-001` | PASS | Automated upload, confirm, assess, review, publish, patient response, and withdraw flow passed. |
| `QA-E2E-002` | PASS | HealthFlow upload through real model parsing, condition matching, and product recommendation passed for the controlled LDL-C report. |

## Real Data Review

- Catalog migration read `576` real source records, created `43` blocked candidates, and retained `4` approved seed recommendations with no source-less products.
- The real 2026 classification workbook produced `12` review drafts.
- Review API published `10` mappings. Two high-risk mappings remained `needs_more_info`: prediabetes to the probiotic product and dyslipidemia to the plant-sterol product generated from supplier claim data.
- The existing approved plant-sterol seed recommendation remained published and was used by the positive end-to-end case. No risk gate was bypassed.

## Cross-System Evidence

- Real 10-page report: `68` metrics parsed, no parsing warnings, and no numeric metric outside its reference range. The correct observable result was no false condition and no product recommendation.
- Controlled LDL-C acceptance report: `4.20 mmol/L` against upper bound `3.40 mmol/L`, parsed as `H`, confirmed with complete source evidence, and assessed as `COND_DYSLIPIDEMIA`.
- Positive response: `product_status=available`, plant-sterol recommendation returned, `unmatched=[]`, and `skipped=[]`.
- A confirmation request that omitted the reference bound was rejected as `missing_source_evidence`; the corrected complete request passed without relaxing the gate.

## Deterministic Checks

- Full Genesis suite: `312 passed`; Ruff, scope, schema, patient-copy, and product-catalog guards passed.
- Product risk suite: `25 passed`; focused module coverage `93%` (`catalog.py` 95%, `mapping_drafts.py` 87%, `recommendations.py` 93%).
- Complexity: product and matching modules average grade `A (3.95)`; branch-heavy recommendation and transition methods remain directly covered.
- Mutation scope is configured for matcher, catalog, mapping drafts, and recommendation engine. Baseline: `1704` mutants, `1202` killed, `502` survived, no timeouts or suspicious results.
- Targeted safety triage killed recommendation ID/default/sorting, repeated publication version, risk-flag, and high-risk mapping mutations. Remaining reviewed examples are equivalent under SQLite case-insensitive row keys or persisted version invariants; the global survivors remain a documented residual test gap rather than a pass claim.
- HealthFlow: `143 passed`, `1 skipped`; changed Python files pass Ruff; frontend build and recommendation rendering passed at 375px, 414px, and desktop viewports.

## Notes

- Full-repository HealthFlow Ruff currently reports pre-existing findings outside the changed files; this PR did not modify those paths.
- One non-blocking Starlette warning reports deprecated `TestClient` integration in Genesis.
- Raw reports, access tokens, isolated databases, and unredacted logs are not retained in this artifact.
