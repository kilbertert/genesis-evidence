# 0002: One-time product-catalog migration and self-governance

Status: Accepted

Date: 2026-08-28

Parent: PRD #103

## Context

The legacy repository `genesis-health` contains 43 supplier product candidates in `var/literature/literature.db` under `nutrition_product_candidates`, all blocked pending quality, regulatory, and medical review. The PRD requires those candidates to seed a governed catalog without keeping the legacy repository alive at runtime.

## Decision

Run a one-time, idempotent backfill from the legacy `literature.db` into `genesis-evidence` product-owned tables. All 43 candidates arrive as `blocked`, with source and provenance records. After backfill, `genesis-evidence` owns product data and review/publish decisions; no code path reads the legacy repository at runtime.

## Alternatives Considered

- Read the legacy database live on each recommendation request: avoids a migration step but reintroduces a cross-repository runtime dependency and makes publication state split-brain.
- Manually re-enter candidates from documentation: removes provenance and is error-prone for 43 rows.
- Treat the Excel functional-category workbook as the product database: it is a classification seed without SKU/dose data, so it cannot serve as the governed catalog.

## Rationale

A one-time backfill keeps the product line autonomous after migration, preserves supplier provenance for audit, and makes repeatability testable. Keeping all 43 candidates blocked until human review keeps the existing trust boundary intact.

## Consequences

The backfill script must be deterministic and repeatable, and CI must prove `blocked=43` / `published=4` counts plus the absence of legacy runtime reads. Later slices own review and publication only inside `genesis-evidence`.
