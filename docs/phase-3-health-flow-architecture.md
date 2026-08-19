# Phase 3: Health-Flow report integration

## Goal

Deliver the two confirmed product paths without combining their storage or
model runtimes:

1. `Health-Flow` owns report upload, page ordering, VLM/OCR parsing, page/BBox
   evidence, and the user's confirmation of extracted observations.
2. `genesis-evidence` owns locked topics, paper screening, claims, evidence
   profiles, knowledge-card versioning, publication gates, and the patient-safe
   evidence response.

The patient-facing product name remains **体检报告解读与健康风险提示**. Neither
service is allowed to turn a report into a diagnosis, prescription, dose, or
treatment plan.

## Runtime boundary

```text
Health-Flow
  upload multiple report files
  -> parse with page/BBox/evidence
  -> user confirms or corrects observations
  -> POST /api/evidence/matches

genesis-evidence
  confirmed metric_code + value + source evidence
  -> deterministic reference-range check
  -> metric_code -> condition catalog
  -> published knowledge cards only
  -> card + Claim/paper traceability + unmatched reasons
```

The boundary is HTTP/JSON. No shared SQLite file, ORM model, vector index, or
LLM prompt is shared between the services. A local deployment may put both
processes on the same host, but the contract remains the same.

## Evidence API v2

`POST /api/evidence/matches`

Headers:

- `X-Genesis-Evidence-Key`: required; both services must share a random key of at least 24 characters.
- `X-Correlation-Id`: optional RFC 9562 UUID; the server creates one when absent.
  Restricting this value to an opaque UUID keeps patient identifiers out of the
  audit entity key.

Request (only confirmed observations are accepted):

```json
{
  "schema_version": "2",
  "observations": [
    {
      "observation_id": "hf-metric-1",
      "confirmation_status": "confirmed",
      "metric_code": "fasting_glucose",
      "value": 6.8,
      "unit": "mmol/L",
      "reference_low": 3.9,
      "reference_high": 6.1,
      "evidence_text": "空腹血糖 6.8 mmol/L 3.9-6.1 H",
      "source_file_index": 1,
      "source_page": 2,
      "source_id": "hf-report-1/page-2"
    }
  ]
}
```

Response guarantees:

- `findings` contains only abnormal observations linked to the newest
  `published` card for each condition.
- Every finding includes card version, grade, patient copy, source observation
  IDs, and Claim/paper locators when the card has them.
- `unmatched` explicitly reports an abnormal metric with no published card.
- `skipped` reports normal observations or invalid/insufficient evidence; they
  never enter condition matching.
- A request is recorded in `audit_events` using its correlation ID and the
  service actor. The audit contains codes and counts, never report images or
  raw patient identifiers.

## Trust and safety gates

1. `confirmation_status` is a literal `confirmed`; unconfirmed or corrected
   data must be resolved by Health-Flow before this call.
2. `metric_code` must be in the canonical first-batch catalog.
3. The exact value and every supplied reference bound must appear in
   `evidence_text`.
4. Values without usable reference bounds are returned as `skipped`; they are
   not treated as abnormal by the first-stage deterministic matcher.
5. Only `knowledge_cards.status = 'published'` are readable here. Draft,
   in-review, approved, rejected, and stale cards are invisible.
6. The API returns stored patient copy verbatim. It does not ask an LLM to
   generate a disease conclusion or treatment instruction.

### Health-Flow field mapping

The upstream repository exposes one `MetricRecord` per parsed row. The adapter
must keep its coordinate/evidence fields and add the canonical metric code only
after the user confirms the row:

| Health-Flow field | Evidence API field | Rule |
|---|---|---|
| `metric_name` | `metric_code` | Adapter-owned name-to-code map; unknown names stay out of matching |
| `metric_value` | `value` | Parse one finite numeric value; do not infer from `abnormal_flag` |
| `unit` | `unit` | Preserve the report unit verbatim |
| `reference_range` | `reference_low` / `reference_high` | Parse only explicit bounds; missing bounds are skipped |
| `evidence_text` | `evidence_text` | Must contain the value and every returned bound |
| `page_number` | `source_page` | One-based; missing location is rejected by the adapter |
| uploaded file order | `source_file_index` | Preserve multipart order, never sort by filename |
| `source_id` | `source_id` | Preserve the upstream locator for UI highlighting |

`abnormal_flag`, VLM confidence, and free-form RAG conclusions are not trusted
by this service. The evidence service recalculates abnormality from confirmed
numeric bounds and reads only `published` cards.

The repository adapter is
`genesis_evidence.integrations.health_flow.build_evidence_request`. It requires
the caller to pass `confirmed=True`; pre-confirmation rows produce no request
observations and are returned in the adapter's `skipped` list. This keeps the
confirmation gate explicit even though Health-Flow's current `MetricRecord`
schema does not yet persist a confirmation field.

## Delivery stages

| Stage | Deliverable | Exit check |
|---|---|---|
| 0 | Baseline backup and audit | SHA-256 manifest and preserved legacy worktree |
| 1 | Evidence API v2 in this branch | Contract tests; published-only and evidence gates pass |
| 2 | Repository adapter | Deterministic Health-Flow row mapping and contract test pass |
| 3 | Real canary | De-identified report set, metric accuracy and traceability measured |
| 4 | Upstream cutover | Health-Flow confirmation UI calls v2; old portal frozen and rollback documented |

Stage 1 intentionally does not copy Health-Flow code into this repository and
does not add Milvus, Neo4j, GraphRAG, or a second evidence database.

## Rollback

Disable the Health-Flow adapter call and keep the existing portal/read-only
workbench processes running. Restore the SQLite backup recorded in
`docs/phase-3-baseline-audit.md` only for data rollback; code rollback remains
the normal PR revert path.
