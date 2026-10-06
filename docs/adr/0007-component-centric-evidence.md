# 0007: Component-centric evidence identity

Status: Accepted

Date: 2026-10-06

Supersedes: none (this record adds a missing decision; it does not overturn a
recorded one)

Parent: the 2026-08-11 knowledge-base plan
(`全球营养成分基础知识库_检索筛选抽取审核与入库方案_纯文字版.docx`, archived at
`_archive/2026/genesis-evidence-archive/2026-08-17-baseline/input-fixtures/`),
whose "以成分（Ingredient）为核心" clause was never implemented and was never
formally declined.

**Removal status: not executed.** This record settles the *decision* only. The
slices that make the code conform are separate tickets; until they land, the
repository's grouping axis is still `condition_code` × `scope_key`, and the
defect described under Consequences is live in the data.

## Context

The evidence line's grouping axis is `condition_code` × `scope_key`. The plan
of 2026-08-11 chose a different axis — the **ingredient**, with its exact
chemical form: its §1 states that "首期知识库应以'成分'为核心，而不是以疾病名称
或单篇论文为核心", and §8.4 lists "复合干预无法识别单一成分效果，却被拆分为单成分
结论" among the conditions that "不得进入正式证据库或用户层".

Neither axis was ever recorded as a decision. The code was built condition-first
from its first commit; the plan was filed as an input fixture during the
2026-08-17 baseline audit and has not been referenced by a requirement since.
`CONTEXT.md` says the plan of record is PRD #103 / #159, and neither PRD
addresses the grouping axis. So the repository has been running on an axis that
no record chose, and the plan's axis was never formally declined — it simply
was not built.

A review of the 2026-10-06 development database found the cost of that gap, and
it is a **correctness** cost rather than a modelling preference:

| Evidence Profile | ingredient_name it declares | distinct interventions actually pooled into it | results pooled |
| --- | --- | --- | ---: |
| `COND_OSTEOPOROSIS_RISK` / `outcome:bone-mineral-density` | "Calcium, vitamin D, protein, or dietary pattern intervention/exposure" | **10** | 31 |
| `COND_MASLD_RISK` / `metric:alt` | "Dietary pattern, weight-loss, or defined nutrition/lifestyle intervention" | **7** | 12 |
| `COND_DYSLIPIDEMIA` / `metric:ldl_c` | "Dietary oils and solid fats" | 4 | — |

The mechanism is visible in `review/scope.py:621`: `_synthesis_dimensions`
sources a profile's `ingredient_name` from the **locked topic's PICOTS**
(`intervention_or_exposure`), not from the research results that are actually
pooled.

An intervention check does exist — `_profile_scope_matches`
(`review/scope.py:380`) compares the topic's `intervention_or_exposure` against
the result's `ingredient_name` + `ingredient_form` + `dose` before a result may
enter a scope. It is not missing; it is the **wrong kind of test**. It is a
loose token-overlap match (`_picots_text_matches`, `review/scope.py:25`) with a
stopword list, an alias table, and a generic-nutrition marker rule, so a result
passes when its exposure text *overlaps* the topic's exposure phrase. The topic
phrase here is a class-level one — "Calcium, vitamin D, protein, or dietary
pattern intervention/exposure" — and a calcium trial, a vitamin D3 trial and a
protein trial each overlap it while denoting different substances. Text overlap
is a recall heuristic; the question that decides poolability is whether two
results denote **the same component**, which no amount of matching the topic's
class phrase can answer.

One certainty value is then computed over those calcium, vitamin D3, protein and
dietary-pattern trials together, and one patient-visible conclusion is generated
from it.

That is the compound-intervention failure the plan names, and it reaches
patients: `_automatic_profile` (`review/service.py:1152`) creates, approves and
publishes the card without a human signature — the evidence gate is what
publishes, and the evidence gate never asked what ingredient the pooled results
studied.

A second, non-correctness pressure points the same way. The 43
`COND_DYSLIPIDEMIA` / `metric:ldl_c` profiles resolve to only **2** distinct
pooled bodies, rebuilt repeatedly at runtime (versions run 2.0.10 → 4.0.45), and
34 of that condition's 106 profiles carry an **empty** `scope_key`. Without a
stable per-ingredient identity there is no key under which a rebuild is
recognised as a rebuild rather than as a new body, so corpora churn and
fragment. The repository currently holds 174 profiles and 36 published cards;
118 cards are `stale`, 69 of which were never published at all.

## Decision

**The ingredient — a canonical component identity plus its exact chemical form
— is the grouping axis for scientific evidence in this repository.** Disease
continues to organise *questions* and to route the patient side; it does not
define the unit over which evidence is pooled or over which certainty is
computed.

1. **Component identity becomes a first-class, catalogued fact.** The
   repository gains one canonical component identity and one form/derivation
   identity, versioned in code the way `core.conditions` and `core.metrics`
   already are, and resolved from extracted text by **exact-match-then-fail-
   closed**, never by substring or prefix containment. An unrecognised string is
   an explicit unmapped state, not a new component.

2. **A profile may pool only results that share one resolved component
   identity and one resolved form.** Determinacy becomes a property of the
   resolved identity, declared in the catalog, not inferred from the length of
   a free-text string. An intervention that cannot be attributed to one
   component — a multi-nutrient supplement, a whole dietary pattern, a
   multi-component product — is **not poolable** and does not reach a profile
   under a single-component claim. It stays in the evidence body, marked as
   ineligible for component-level synthesis, exactly as the existing design
   already retains high-risk and negative results.

   **Where the pooling decision must land, and why it is not a new label.** The
   slices must place this decision at **scope eligibility** — in
   `_profile_scopes` (`review/scope.py:574`) — and *not* add a non-poolable
   exemption to the profile-completeness gate. `create_card`
   (`review/store/review.py:595`) requires a profile to contain **every**
   scope-eligible approved result:

   ```python
   if {row["id"] for row in eligible} != set(claim_ids):
       raise ValueError("evidence profile must include every reviewed eligible result")
   ```

   So "approved but non-poolable" is **not representable today** — a profile
   that omitted such a result would be rejected outright. That completeness rule
   is in fact the mechanism that *forces* the pooling this record objects to:
   because every result whose outcome matches a topic is scope-eligible, the
   profile must contain all of them, including the calcium, vitamin D3 and
   protein trials. Making a result ineligible for a component scope is therefore
   the one change needed, and it leaves the completeness gate — a good rule,
   which prevents a body from silently dropping inconvenient evidence — exactly
   as it is. Adding a second eligibility path to that gate is the alternative
   and is worse: two places would then decide what belongs in a body.

   A result remains stored in `results` regardless of pooling, and membership of
   a synthesised body stays a separate fact in `evidence_profile_results`. The
   reason a reviewed result is unprofiled is carried by the eligibility
   decision, not by `results.status` (whose vocabulary is
   `reviewed` / `rejected` / `not_reported` — a review outcome, not a pooling
   one). Note that the 43 approved-but-unprofiled results in today's database
   are *not* an example of the target state: they are unprofiled because no
   profile was built for their scope at all, not because anything excluded them.
   The slice must not point at that number as precedent.

3. **Certainty is computed over one component and one form.** A GRADE
   assessment that mixes components is not a GRADE assessment of anything, so
   this follows from (2) rather than being a separate requirement.

4. **The change is introduced without a rewrite.** Existing published cards are
   re-derived from the results already stored in `results`; nothing is
   re-fetched and nothing is re-extracted. New table allocation is a consequence
   of (1) and must be decided under the table budget (§Table budget below), not
   assumed.

## Table budget

`scripts/check_schema.py` pins `MAX_TABLES = 26` and the schema is currently at
**26/26** — the budget is full, with no headroom. The repository's own rule is
that "新增一张表就是一次架构决定，必须同时回答'它属于哪个簇、它的不变量为什么不能
写在现有表上'".

This record answers that question for the catalog:

- **The component catalog does not need a table.** It is a code-owned,
  version-controlled vocabulary — the same shape as `core.conditions`
  (`CONDITIONS`) and `core.metrics` (`METRIC_LABELS`), neither of which owns a
  table in its own right. `conditions` has a table only because cards reference
  it by foreign key; a component catalog that is resolved at write time into
  the existing `results.ingredient_*` columns needs no such anchor.
- **What does need storage is the resolution outcome on a result**: which
  canonical component and form a result was resolved to, and whether it was
  poolable. That is a column set on `results`, not a new entity.

Consequently this decision does **not** by itself authorise a new table. If a
slice concludes that one is genuinely required, that slice must raise the budget
in `check_schema.py` and justify it explicitly, in its own pull request, rather
than carrying the increase in with this record.

## Alternatives Considered

**Keep the condition axis and reject the plan's clause.** The cheapest option,
and the one the repository has in fact been running. It was rejected because the
defect above is not a modelling preference that a reader can tolerate: a
certainty value computed over ten different interventions, published under one
patient-visible sentence, is a wrong answer, not an untidy one. Keeping the axis
means keeping the defect.

**Make the ingredient an axis *as well as* the condition, pooling on the
intersection.** Rejected as the worst of both. A profile keyed on
`(condition, ingredient, form)` still admits "calcium" and "vitamin D" as
separate bodies under `COND_OSTEOPOROSIS_RISK`, which fixes pooling within an
ingredient but leaves the review question — "does calcium raise BMD" — answerable
without stating the form or the population, and leaves the corpus churn
unaddressed because the empty-`scope_key` profiles would only acquire a key.

**Model the ingredient as a full ontology with synonyms, CAS identifiers and
dose conversion.** This is what the plan's §2.1 eventually wants, and it is
what the first slice must *not* build. Rejected for this slice under the
repository's own anti-abstraction rule: the immediate defect is that different
interventions are pooled, and naming the component exactly — including mapping
"Vitamin D3 (cholecalciferol)" to a canonical identity rather than inventing
one per string — resolves it. Identifiers, synonym normalisation and dose
conversion are additive later, and are needed only when a consumer asks a
question that exact naming cannot answer.

## Rationale

The repository's stated first principle is that the system must be able to say
what it is allowed to say, and every design choice is derivable from it. A
pooled certainty value that is not the certainty of any single component is
exactly the kind of claim the system should not be able to make, and today it
can: `README.md` §5 records the evidence gate as the sole publication
authority, and the gate checks *whether* claims are traceable, not *whether the
things being pooled together are the same thing*.

Choosing the plan's axis also restores a question the code currently cannot
answer. The plan's recall rule is to search `成分或形式 AND 人群或问题 AND
研究设计`; the repository's filter is to include a result when its outcome
matches the topic's outcome and its population matches the topic's PICOTS. With
one ingredient axis, "which studies inform the calcium recommendation" becomes a
SQL question; without it, the answer requires reading every extraction.

It also explains the corpus churn rather than merely observing it. A stable
per-ingredient key is what makes a rebuild recognisable as the same body; its
absence is why 43 profiles describe 2 bodies. Fixing the axis is the cheapest
path to fixing the churn, and it is the same change.

Finally, this record does not disturb what already works. The plan's §5.1
requirement — that Study, StudyPublication, Publication and Result are stored
separately and that facts attach at the Result layer — is already met, and the
defect lives one layer above it. The recorded divergence where the autonomous
pipeline publishes without a named human reviewer is untouched here and remains
governed by `README.md` §1; this record neither relies on nor changes it.

## Consequences

- Until the slices land, the defect is live: published cards under
  `COND_OSTEOPOROSIS_RISK` and `COND_MASLD_RISK` pool 7–10 distinct
  interventions each under one certainty and one patient-visible sentence.
- Component resolution is fail-closed on unrecognised text, so the first slice
  will surface a real unmapped population rather than silently widening. The
  parsing corpus is small enough to hand-review: **59 distinct ingredient
  strings** cover all 187 approved results.
- Measured exposure at 2026-10-06, so the slices can size themselves:
  **15 of 36 published cards (41.7%)** pool results carrying more than one
  distinct ingredient string, and **80 of 174 profiles** do. That 41.7% is an
  **upper bound on impact, not the impact**: several strings in that set denote
  one component (`Olive oil` / `Olive Oil` / `Virgin olive oil` / `EVOO`), and
  those cards must *unify* rather than split or retire. The number the slice
  actually has to establish — and must report — is the split between "one
  component, several spellings" and "every string denotes a different thing, or
  denotes no component at all". Until that split is known, no card-count
  prediction is honest.
- The plan's other unbuilt requirements — the seven search streams, the six
  gates G0–G5 as first-class state, the regulatory chain, and the
  safety/mechanism/preprint evidence layers — are **not** decided here. This
  record fixes the axis they will hang on; it does not authorise any of them.
- Profile and card count is expected to *fall* as bodies legitimately split and
  non-poolable results leave. A fall is the expected outcome, not a regression.
