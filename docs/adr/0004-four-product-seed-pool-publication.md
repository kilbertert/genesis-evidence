# 0004: Normalize the four-product minimum seed pool publication

Status: Accepted

Date: 2026-08-28

Parent: PRD #103

## Context

The PRD requires a small, controllable published set to drive the full lifecycle before any broad 43-candidate review program. The candidate list includes one eye-health product that does not map to the first-batch 12-condition catalog.

## Decision

Publish exactly four products as the initial safe recommendation pool, while the remaining 39 migrated candidates stay `blocked`:

- 郅臻堂®植物甾醇 → `COND_DYSLIPIDEMIA`
- 天然维生素D3 → `COND_VITAMIN_D_DEFICIENCY`, `COND_OSTEOPOROSIS_RISK`
- 复合柠檬酸钙 → `COND_VITAMIN_D_DEFICIENCY`, `COND_OSTEOPOROSIS_RISK`
- 复合全骨营养餐（PRD 简称「复合骨营养餐」）→ `COND_SARCOPENIA_FRAILTY`, `COND_MALNUTRITION_RISK`

越橘益视宝 is excluded from the seed pool because its eye-health direction is outside the 12-condition catalog.

## Alternatives Considered

- Publish all 43 candidates: expands coverage but mixes unreviewed supplier claims into patient recommendations before medical/regulatory review.
- Include the eye-health product: would require a new condition direction outside the current confirmed condition catalog.
- Publish zero products: safest but cannot demonstrate the end-to-end recommendation path.

## Rationale

Four products cover the PRD's dyslipidemia, vitamin-D/osteoporosis, sarcopenia/frailty, and malnutrition demonstrations with reviewed mappings. Keeping every other candidate `blocked` preserves the fail-closed boundary while giving the later review/Excel mapping slices a verified minimal end-to-end seed.

## Consequences

The backfill and publication audit must record this decision. The review line must support later promotion from the blocked pool, and the recommendation engine must never bypass the published-only gate to reach blocked candidates.
