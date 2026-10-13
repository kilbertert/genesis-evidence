# 0012: The machine-signature boundary is the action threshold, not a human click

Status: Accepted

Date: 2026-10-13

Supersedes: none as a *written* decision — but it corrects two documents that
state the opposite of what the system does. `mem-20260814-ranlei-007` is
withdrawn by this record; `docs/evidence-governance-standards.md` is corrected
in the same change.

## Context

The literature review pipeline has been described, since 2026-08-14, as having
three layers: AI suggestion, deterministic gate, and a named human signature.
The recorded form of that (`mem-20260814-ranlei-007`) is explicit that
`needs_review` "必须同时具名确认并填写人工裁决" — a model may draft, never decide.

**That line was never enforced, and nobody decided to stop enforcing it.**
Measured against the production database on 2026-10-13 (read-only):

| Artefact | Machine-signed | Human-signed |
| --- | ---: | ---: |
| Claims decided | 2356 / 2361 (99.8%) | 5 |
| Evidence Profile `certainty` | 225 / 226 (99.6%) | 1 |
| Published cards | 52 / 52 (100%) | 0 |
| `card_published` events | 122 | 6 (all Aug 20–23) |
| Consistency adjudications | 129 automatic | — |

The system and its own documentation have contradicted each other since at least
2026-08-20. The memory is injected as authoritative context into every session,
so the contradiction is load-bearing: an agent reading it treats a settled,
running behavior as a rule that must not be broken, and cannot reconcile it with
the database.

### The real boundary is already in the code, and it is not a human

What the system actually enforces is **not** "a person approves each card" but a
**grade boundary**, declared as data in `core/evidence_strength.py`:

- `PATIENT_VISIBLE_STRENGTHS = {high, moderate, low}` — may reach a patient as
  *context*;
- `ACTION_THRESHOLD_STRENGTHS = {high, moderate}` — may additionally carry an
  *action message*;
- their difference, `CONTEXT_ONLY_STRENGTHS = {low}`, is derived, not restated.

`is_patient_visible(status, grade)` is the single predicate the writer
(`transition_card`) and every reader (`patient_visible_sql`) share, so a card can
never be published-but-invisible for a reason no reader states.

And the machine cannot reach the top of that scale: `_profile_suggestion` in
`review/service.py` computes the GRADE score, then applies
`min(score, 1 if single_primary_study else 2)` with the rationale string
**"AI-only synthesis is capped at moderate certainty."** Since
`STRENGTH_BY_SCORE[3] == "high"`, **`high` is unreachable by an AI-only path.**
That cap is the human-content guarantee, and it is enforced by arithmetic rather
than by a signature.

Measured, the boundary holds exactly:

| Grade | Published | Carries an action message? |
| --- | ---: | --- |
| `low` | 47 | no — `context_only` |
| `moderate` | 5 | **yes** |
| `very_low` | 0 | never published |
| `high` | 0 (0 profiles at `high`, anywhere) | — |

### The fallback queue is mostly not a reasoning problem

Of 432 papers, 423 completed autonomous review; 57 raised
`autonomous_review_attention_required`. Classified by what blocks them:

| Bucket | Papers | What is missing |
| --- | ---: | --- |
| Genuine capability boundary — full text present, extraction present, AI still declines | **20** | a better adjudication, not more data |
| No stored full text | 22 | data |
| Full text present but never extracted | 2 | a retry |
| Held by a person's earlier rejection | 4 | a person's decision, by design |

The 20 are not untouched: `_source_adjudication` already reads the full text and
attempts the adjudication (`ai:source-adjudicator`), and its fallback reason
states explicitly that full-text evidence could not support an autonomous
verdict. It succeeds on 5 and declines on 20.

Extraction is the one stage with no autonomous HTTP path at all. The deployed
service host runs no extraction worker (`docs/deployment.md`), and the deployed
`ops/collect_topics.py` docstring records that a large batch is what forced the
pause (`mem-20260821-ranlei-002`).

## Decision

**Where a deterministic check can re-derive a claim, the machine signs. Where the
claim would carry an action message to a patient, the machine is capped — and the
cap is the control.**

1. **Upstream of the profile — screening, extraction, claim review, internal
   admission — the machine signs.** These carry source-anchored checks (excerpt
   attestation via `core/source_excerpt.attest_segments`, unique
   study/publication relation, structured `Result`), so a wrong verdict is
   *detectable without re-running the model*.

2. **The AI-only certainty cap is the human-content guarantee and must not be
   relaxed.** `min(score, 2)` on the AI path is what keeps every machine-authored
   card at `context_only` or below the top grade. An `action`-threshold card
   reaching a patient on a machine signature is acceptable **only** because no
   model can reach `high` alone; that sentence is therefore load-bearing, and
   changing the cap changes the safety argument, not just a grade.

3. **A machine may not silently overturn a person.** Where an earlier human
   `rejected` decision blocks a paper, the machine's only available action is to
   re-surface it for a person — never to reverse the decision. Where an existing
   human-signed `moderate` profile is superseded by an automatic one, the
   downgrade **fails** rather than proceeding, and the refusal is recorded as
   attention. Today that collision is an unhandled exception path in
   `_profile_suggestion`; making it an explicit refusal is a required follow-up,
   not an optional one.

4. **Retroactive automation is not redesign in disguise.** The 5 published
   `moderate` cards and 47 `low` cards were machine-signed without the
   reproducibility evidence that decision 5 establishes. They are **grandfathered
   and labelled as such** — their `certainty_rationale` records the policy version
   that produced them. They are not re-adjudicated as part of this record, and
   they do not become the precedent that skips decision 5 for future cards.

5. **The gate for new `action`-threshold machine signatures is the one that does
   not exist yet.** "A machine may sign a card that carries an action message"
   currently rests on a cap, not on measured accuracy. Before that is relied on
   for new cards, the 20 capability-boundary papers are re-adjudicated with a
   stronger model:
   - If the stronger model resolves them **with quotable source evidence**, the
     boundary was model capability, and the evidence requirement is what admits
     a machine signature at the action threshold.
   - If it **still declines**, the ambiguity is in the source text, and no model
     may auto-adjudicate it — those papers stay human-signed permanently, and the
     correct output is an explicit "cannot be decided from this text", not a
     forced verdict.
   - **Zero errors is the bar for a verdict that changes patient-facing
     direction.** Every other unresolved difference must quote the source that
     decides it.

6. **Identity is split.** `GENESIS_EVIDENCE_REVIEWER_ID` currently doubles as the
   ingestion run's authorisation identity and as the autonomous pipeline's gate
   identity; the environment also carries `ranlei` in `requested_by`. An
   AI-authored decision must be recorded under an `ai:`-prefixed actor, and a
   person's name must appear only where a person acted. Today the audit cannot
   distinguish the two for the ingestion path — the exact question the audit
   exists to answer.

## Alternatives Considered

**Re-enforce the written rule: require a human signature on every published
card.** This is the strongest alternative and the one the existing memory argues
for. Its case is real — the cards reach patients, "a model said so" is not a
person's accountability, and 5 `moderate` cards carry action messages. It loses on
two measured facts. First, it has not been in force since at least 2026-08-20, so
enacting it is building a control, not restoring one; the 52 published cards would
have to be retroactively invalidated or explicitly grandfathered, and neither is
free. Second, it demands the review that does least: a person clicking through 47
`low` cards is a rubber stamp, and a rubber stamp is worse than a recorded machine
signature because it launders machine decisions as human ones. **The stronger,
narrower version of this alternative — a person signs only the 5
action-threshold cards — is accepted in substance by decision 5**, which makes
that signature rest on reproducibility evidence rather than on a click.

**Remove the AI-only cap and let the model reach `high`.** Its case: the cap is
arbitrary, the GRADE arithmetic already accounts for risk of bias, inconsistency,
indirectness and imprecision, and a well-powered meta-analysis of several RCTs
arguably *is* high-certainty even when a machine assembled it. It loses because
`high` is precisely the grade that changes what a patient does, and no evidence
exists that machine synthesis is calibrated at that level — 0 profiles have ever
reached it, so there is no observation to appeal to. Keep the cap until decision
5's experiment produces an error rate; then the question can be reopened with
data.

**Drop the in-code identity gate and let the caller supply any actor string.**
Its case: `_reviewer()` only rejects the empty string, so the gate is already
nominal, and a weak check that reads as strong is worse than none. It loses
because the audit trail needs a non-empty actor to remain a trail at all — the
requirement is real even where its enforcement is nominal. The split in decision
6 is the part worth keeping.

**Extend to two independent extractions before trusting automation (Cochrane
MECIR C39).** Its case: two independent screenings are the accepted defence
against selection error, and `docs/evidence-governance-standards.md` already
declines to claim the standard. It loses for a reason this record cannot fix:
extraction here is *already* dual-channel (A + B + consistency), and the measured
result is that **all 8 papers in the 2026-10-13 acceptance run returned
`needs_review`**. A second channel reproduces the disagreement rate; it does not
remove it. A third channel triples cost to move the same unresolved differences,
whose cause is the source text — which is exactly what decision 5 tests.

## Consequences

**Easier.** "Who decided this, on what grade, under which policy version" becomes
answerable per row. The 22 + 2 data problems become ordinary data work with no
judgement attached. The 20-paper experiment is cheap, bounded, falsifiable, and
produces a fact about the corpus rather than an argument about models.

**Harder, and now capped.** The pipeline's throughput ceiling for *contributing
new grades* is not the model's rate: an AI-only path cannot lift a topic to
`high` or clear the action threshold, so topics whose evidence deserves an action
message need a human in the loop by construction. If decision 5 returns "still
declines", that ceiling is **permanent for those 20 papers** — they are
undecidable from their text, and no future model change lifts it. The
`needs_review` verdict is therefore not a backlog to drain but a standing
population that needs a human channel sized for it.

**A risk this record accepts, explicitly.** 5 cards carrying action messages are
live on machine signatures with no measured error rate. They are grandfathered
(decision 4), not endorsed. If patient-facing accuracy ever needs defending, this
is the first place it is weak, and decision 5 is the remedy.

**A cost this record does not resolve.** 22 papers need full text absent from the
corpus, and the collection path that would fetch it is the one paused by
`mem-20260821-ranlei-002` for resource reasons. Re-enabling it is a separate
decision with its own capacity evidence.

**What this record does not establish.** It does not claim the machine-signed
99.8% is *correct* — only that it is recorded, checkable, and consistent with
what runs. No error rate has been measured. If one is needed for a compliance
purpose, it does not exist yet and this record does not supply it.
