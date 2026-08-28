# Product Recommendation Acceptance Result

- Parent requirement: `#103`
- Tested Genesis commit: `8ffbd124da8a572074dc92d49e04dbfeef0e0448`
- Tested HealthFlow commit: `64fa5506d2c2ed8a54ce60173b5e2dc0e7930a68`
- Build identity: `local-uv-8ffbd12-healthflow-64fa550`
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
- Mutation scope is configured for matcher, catalog, mapping drafts, and recommendation engine. Baseline: `1704` mutants, `1238` killed, `466` survived, no timeouts or suspicious results.
- Targeted safety triage killed recommendation ID/default/sorting, repeated publication version, risk-flag, and high-risk mapping mutations. Remaining reviewed examples are equivalent under SQLite case-insensitive row keys or persisted version invariants; the global survivors remain a documented residual test gap rather than a pass claim.
- HealthFlow: `143 passed`, `1 skipped`; changed Python files pass Ruff; frontend build and recommendation rendering passed at 375px, 414px, and desktop viewports.

## Canonical Production Verification

- Executed: `2026-08-28T20:24:58+08:00` to `2026-08-28T20:29:57+08:00` on the canonical user services (`genesis-evidence-review`, `genesis-evidence-portal`, and `health-flow`), using Genesis code `8ffbd124da8a572074dc92d49e04dbfeef0e0448` and HealthFlow code `64fa5506d2c2ed8a54ce60173b5e2dc0e7930a68`.
- Production catalog state after migration: `576` source records, `34` blocked candidates, `10` published recommendations, `2` mapping drafts retained as `needs_more_info`, and `0` products without sources. SQLite integrity check: `ok`.
- Positive controlled report: one real-model parsed LDL-C metric (`4.20 mmol/L`, reference `<= 3.40 mmol/L`); confirmation with the complete source excerpt produced `assessed`, `COND_DYSLIPIDEMIA`, `product_status=available`, the published plant-sterol recommendation, `unmatched=[]`, and `skipped=[]`.
- Negative real report: `59` metrics parsed with `0` processing warnings; no high/low abnormal flags, `0` findings, `0` unmatched items, and no product recommendation. Normal or insufficiently actionable rows were retained as explicit skipped outcomes.
- The first positive confirmation intentionally omitted the reference excerpt and returned `missing_source_evidence`; the service rejected it before matching. A second upload with the complete excerpt passed without weakening the evidence gate.

## Real Five-Image Acceptance

- Case: `QA-E2E-003`; input was the five real JPEG report images in the local `体检报告/` directory. The zero-byte PDF beside them was intentionally excluded.
- Upload completed at `2026-08-28T21:12:27+08:00`; extraction completed at `2026-08-28T21:14:17+08:00`; assessment completed at `2026-08-28T21:17:56+08:00`.
- Five files produced `64` metrics with `0` processing warnings. The confirmation covered all `64` metrics with subject consistency `same`.
- Three abnormal observations were retained with source evidence: total cholesterol `5.5 mmol/L` (reference `<5.2`), LDL-C `3.63 mmol/L` (reference `<2.60`), and Non-HDL `4.00 mmol/L` (reference `<3.40`).
- The assessed result contained one condition, `COND_DYSLIPIDEMIA` (血脂异常), and returned the published product `郅臻堂®植物甾醇咀嚼片` with `product_status=available` and `unmatched=[]`. The remaining `61` normal or non-actionable observations were explicit `skipped` outcomes.
- Automated API acceptance: `PASS`. Browser-level human acceptance remains intentionally pending after deployment; the temporary report and account are retained until that review completes.
- No raw images, names, account credentials, access tokens, provider run IDs, or unredacted response logs are retained in this artifact.

## Notes

- Full-repository HealthFlow Ruff currently reports pre-existing findings outside the changed files; this PR did not modify those paths.
- One non-blocking Starlette warning reports deprecated `TestClient` integration in Genesis.
- Raw reports, access tokens, isolated databases, and unredacted logs are not retained in this artifact.
