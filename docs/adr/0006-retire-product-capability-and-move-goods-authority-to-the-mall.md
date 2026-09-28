# 0006: Retire the product capability and move goods authority to the mall

Status: Accepted

Date: 2026-09-28

Supersedes: 0001, 0002, 0003, 0004

Parent: PRD #159

**Removal status: executed in code.** This record settled the *decision*; the
slices of #173 then removed the product surface — the review routes and offline
tooling, the recommendation engine, the product fields on the evidence response,
and the five tables from the schema. One residue remains and is called out under
Consequences: an already-deployed database keeps the five tables and their rows
until a separate, deliberate drop runs. A fresh database is 26 tables; production
is still 31 until then.

## Context

ADRs 0001–0004 brought a nutrition-product capability into this repository: a
43-candidate catalog migrated from the archived `genesis-health`, a review and
publication line, a deterministic recommendation engine attached to confirmed
findings, and a patient-side product block on `POST /api/evidence/matches`.
Together they made this repository the authority for which goods a patient sees,
gated by a `published`-only rule that ADR 0004 states as:

> the recommendation engine must never bypass the published-only gate to reach
> blocked candidates

PRD #159 now places that authority elsewhere. The company already operates a
mall with tenants, storefronts, goods, pricing, stock, carts, and orders. A
health-detection entry point belongs in that ecosystem as one more service, not
as a parallel goods catalog: the mall's tenants configure their own goods, and a
second catalog here would be a second source of truth for the same question.

Two findings from the integration work made the boundary concrete:

- **The mall cannot be reached from the patient's browser.** Its gateway returns
  a CORS header combination that browsers reject for credentialed requests, so
  goods data must be fetched server-side. That removes any reason for this
  repository to hold goods at all.
- **The health condition → goods mapping is a vocabulary, not a catalog.** Which
  disease direction corresponds to which mall label is a global, slow-moving
  mapping that belongs in a versioned file; which goods a tenant tags with that
  label is the tenant's own configuration, already served by the mall's admin UI.

Retiring the capability also resolves a recorded conflict. #170's delivered
mapping file (`docs/condition-to-mall-tag.md`) states that once the mall becomes
the only gate for patient-visible goods, ADR 0004's published-only rule is in
direct conflict, and that the file cannot be used as a delivery basis until that
rule is superseded. This record is that supersession.

## Decision

**The mall is the sole authority for goods, and this repository retires its
product capability entirely.**

1. **Remove the product surface.** The catalog, the review/publish line, the
   recommendation engine, the mapping-draft tooling, the five product tables,
   the product-only review routes, the offline mapping scripts, and the
   product block on the evidence response are all removed. The evidence response
   returns `condition_code`, evidence text, and evidence links; nothing about
   goods.

2. **Supersede the published-only gate with a stated replacement.** The patient
   side no longer has a platform-side goods-review gate. The gate is now the
   mall's own sellability filter — review approved, on shelf, and belonging to
   the tenant/storefront — which the mall already applies. **This repository
   deliberately does not add a second filter on top of what the mall returns.**
   ADR 0004's published-only rule is superseded, not narrowed: it has no subject
   left once goods leave this repository.

3. **What this repository keeps is the semantic mapping.** `condition_code` →
   mall `(label name, label value)`, as a versioned file reviewed in this
   repository and delivered to the mall. It is a vocabulary, not goods data: it
   names no tenant, no storefront, no product, and no price.

4. **Re-adding a platform-side goods gate requires a new record.** If a future
   decision wants a platform-side filter over mall-returned goods — for
   regulatory classification, marketing-claim wording, or approval-document
   completeness — that is a new decision with its own record. It is not
   inherited from this one, and it must not be introduced by widening the
   mapping file's wording.

## Alternatives Considered

- **Keep the catalog and mirror mall goods into it.** Rejected: a mirrored
  catalog is a second source of truth for price, stock, and shelf state, and it
  drifts. The mall already answers those questions authoritatively.
- **Keep the platform-side `published` gate and apply it to mall-returned
  goods.** Rejected: the gate's inputs — this repository's product candidates
  and their review state — are exactly what is being retired, so the gate would
  have nothing to evaluate. Filtering mall goods by a second, differently-shaped
  review would also put two review systems on one shelf decision.
- **Keep `recommendations[]` on the response and fill it from the mall.**
  Rejected: it keeps goods data in the evidence contract, which is the coupling
  being removed; the patient page fetches goods through its own server-side
  proxy instead.
- **Reuse the mall's third-party goods face unchanged.** Rejected as
  insufficient: its inbound surface carries more enforcement than it appears to.
  Measured against `cloud-mall-api` on `dev_230201`, only the `tenant-id` header
  and the `mall-app-id` allowlist are active; the timestamp check, the IP
  allowlist, the rate limiter, and the per-endpoint HMAC verification are either
  commented out or inconsistent between endpoints. A read endpoint added there
  must require its own verification rather than inherit an assumption. That work
  is tracked separately (#167).
- **Move the mapping into the database instead of a versioned file.** Rejected:
  this repository has no goods-review UI, so mapping drafts could only be
  created by an offline script with no review surface. A file makes pull-request
  review the audit trail.

## Rationale

The boundary follows the authority. The mall owns goods because goods are the
mall's data, with the mall's tenancy, pricing, and fulfillment attached. This
repository owns evidence because evidence is what it was built to produce, and
its output is a health risk finding — which is a statement about a person, not
about merchandise.

Removing the capability rather than degrading it keeps one rule per question.
Two gates over the same shelf decision would mean a good could be visible or
invisible depending on which system was asked, and no operator could answer
"why is this product showing?" without reading both.

The one thing that must not be lost in the move is the reason the gate existed:
goods shown next to a health finding carry an implicit health claim. Moving the
decision does not remove that risk, it relocates it. This is precisely why the
replacement is recorded as a decision with a named owner (the mall's sellability
filter) rather than left as an absence, and why the mapping file states that the
mall must not read "goods resolved from a tag" as "goods cleared for patient
display".

## Consequences

- **Patients see what the mall returns.** Any regulatory classification,
  approval-document requirement, or marketing-claim wording check is the mall's
  to implement, as a separate initiative. This repository provides no backstop
  and must not be assumed to have one.
- **The mapping file is a delivery dependency.** A wrong label string fails
  silently — the mall's label lookup skips unmatched names and returns an empty
  goods list with HTTP 200. Renames therefore have to be coordinated with the
  mall, and the mapping needs at least one real "goods were actually returned"
  verification before it is treated as usable.
- **The evidence contract loses fields, so consumers change in the same batch.**
  The response models are strict (`extra="forbid"`) on both sides, so the
  producer and consumer must move together; a partial deploy fails to parse.
- **Already-stored evidence payloads carry the removed fields.** They are
  migrated rather than read leniently, so the strict contract stays strict and a
  genuine future contract drift is still caught.
- **The frozen-scope guard is back to its full set.** ADR 0001 unfroze
  `nutrition_product` / `supplier_product` / `supplement_recommendation` for the
  implementation slices. Those three names were never actually removed from
  `scripts/check_scope.py`'s `FROZEN_IDENTIFIERS`, so no guard edit is owed —
  the set already rejects them, and with the capability gone nothing in `src/`
  should ever mention them again.
- **Already-deployed databases keep the five product tables.** `Database.initialize()`
  only runs `CREATE TABLE IF NOT EXISTS`; it never drops. So the schema change makes
  a *new* database 26 tables, while the deployed database keeps 31 and their rows.
  This is deliberate, not an oversight: an automatic `DROP` inside `initialize()`
  would run on every service start, which makes a destructive, irreversible act a
  side effect of a deployment command. Dropping the residue is instead a separate
  deliberate operation with its own backup, on the same footing as any other
  service-host data change. Until it runs, `scripts/check_schema.py` measures a
  fresh database and does not describe production.
- **This record is the prerequisite that releases #170.** With the conflict
  resolved, the mapping file's "cannot be used as a delivery basis" note is
  lifted, leaving only the mall-side values it still needs.
