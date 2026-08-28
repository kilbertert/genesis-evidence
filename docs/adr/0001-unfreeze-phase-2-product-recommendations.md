# 0001: Unfreeze phase-2 product-recommendation scope

Status: Accepted

Date: 2026-08-28

Parent: PRD #103

## Context

Phase-2 planning froze nutrition-product-recommendation surfaces outside `genesis-evidence`, with a deterministic scope guard that rejects identifiers such as `nutrition_product` and `supplement_recommendation` from the active package. The current patient path stops at `product_status=not_implemented`, and the legacy `genesis-health` repository is archived and not run.

## Decision

Product recommendations move into `genesis-evidence`. The product catalog, review/publish line, recommendation engine, and patient-side rendering become first-class modules in this repository, while `genesis-health` remains frozen and is not a runtime dependency.

## Alternatives Considered

- Keep the recommendation engine and catalog in `genesis-health`: preserves the old boundary but leaves patient closure in a frozen repository.
- Build a separate recommendation microservice: isolates governance but duplicates review identity, evidence locators, and finding assembly across services.
- Keep recommendations permanently out of scope: lowest risk but leaves the PRD patient story unresolved.

## Rationale

The recommendation is a companion result on an already-confirmed risk finding, so the single seam lives naturally next to `ReportStore.assess()` and `EvidenceStore.match_published_cards()`. Co-locating with the existing card review and patient-copy guards preserves traceability and avoids a second deployment boundary.

## Consequences

The frozen-scope guard must be updated in the implementation slices as the new modules land. `genesis-health` remains archived. Any future build of the recommendation modules must stay deterministic at runtime and keep supplier claims out of patient-visible copy.
