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

And the machine cannot reach the top of that scale: `_automatic_profile` in
`review/service.py:1176` computes the GRADE score, then applies
`min(score, 1 if single_primary_study else 2)` with the rationale string
**"AI-only synthesis is capped at moderate certainty."** Since
`STRENGTH_BY_SCORE[3] == "high"`, **`high` is unreachable by an AI-only path.**
That cap is the human-content guarantee, and it is enforced by arithmetic rather
than by a signature. It binds the **autonomous** path only: the manual
`EvidenceProfileInput` accepts a caller-supplied `certainty`, so a person can
still write `high` — which is the intended shape, since the cap exists to bound
what a machine may assert on its own.

Measured, the boundary holds exactly:

| Grade | Published | `action_status` served to a patient |
| --- | ---: | --- |
| `low` | 47 | `not_available` |
| `moderate` | 5 | `not_available` |
| `very_low` | 0 | never published |
| `high` | 0 (0 profiles at `high`, anywhere) | — |

And measured, **no card carries action advice today**: `card_capabilities` in
`core/contracts.py` returns `content_layer: "context_only"` and
`action_status: "not_available"` for **every** grade, including the
action-threshold ones — the `moderate` branch only supplies a different
`action_message` string ("…但当前知识卡尚未包含经审核的具体行动内容"). So
`ACTION_THRESHOLD_GRADES` currently decides **wording**, not capability. That
narrows this record's exposure considerably, and it is the fact the whole
patient-facing argument rests on.

### The fallback queue is mostly not a reasoning problem

Of 432 papers, 423 completed autonomous review; 57 raised
`autonomous_review_attention_required` (70 events over those 57 papers, since a
paper can be refused at more than one stage). Classified by the paper's
**current** state rather than by the stage that refused it, so the buckets are
disjoint and sum to 57:

| Bucket | Papers | What is missing |
| --- | ---: | --- |
| No stored full text | 22 | data — the collection path that would fetch it is paused |
| Full text present but never extracted | 2 | a retry |
| Refusal superseded — an extraction postdates the refusal | 11 | nothing; already drained |
| Still refused — full text present, extraction present | 18 | see decision 5: mostly a pipeline gap, not a capability limit |
| Held by a person's earlier rejection | 3 | a person's decision, by design |
| Other standing refusal | 1 | — |

Classifying by stage name instead is what makes this look like a bigger problem
than it is: the stage buckets sum to 60 over the same 57 papers (36 + 20 + 4),
because one paper carries both an `extraction`- and a capability-stage refusal
and two carry both an `extraction`-stage refusal and a person's rejection.

Three of those buckets are worth separating from the rest:

- **The 11 "superseded" papers are history, not work.** They were refused for
  "no stored full text", the full text arrived afterwards, and the pipeline moved
  on by itself. A stale refusal is not a standing limit; the next pass drains it.
  Read as current backlog it inflates the queue by a fifth.
- **These counts are a snapshot, and a stage-name reading of them over-counts.**
  They were taken on 2026-10-13 while the pipeline was still draining, so they
  must be re-measured rather than quoted. What is stable is the structure: the
  queue is mostly data and history, and the genuinely undecidable part is small.
- **The 3 held by a person are the binding constraint, not the capability.**
  A machine cannot reverse a person's rejection (decision 3), so for those papers
  the capability question is moot until a person moves first. They must not be
  counted as capability work.

These papers are not untouched: `_source_adjudication` already reads the full text
and attempts the adjudication (`ai:source-adjudicator`), and its fallback reason
states explicitly that full-text evidence could not support an autonomous
verdict. Decision 5 is where that reason is taken apart — it is a pipeline gap far
more often than it is a limit of what can be known.

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
   relaxed.** `min(score, 2)` on the AI path is what keeps a machine from
   asserting the top grade on its own. This cap matters *because* the parallel
   guard is weaker than it looks: **`card_capabilities` serves
   `action_status: "not_available"` at every grade**, so no card carries reviewed
   action content today and the code — not a policy — is what currently keeps a
   machine verdict at background context. Relaxing either the cap or that
   capability mapping changes the safety argument, not just a label. In
   particular, the day `action_status` becomes available for `moderate`, the
   action threshold stops being a wording distinction and starts being a
   behavioural one, and decision 5's evidence becomes mandatory rather than
   prudent.

3. **A machine may not silently overturn a person.** Where an earlier human
   `rejected` decision blocks a paper, the machine's only available action is to
   re-surface it for a person — never to reverse the decision. Where an existing
   human-signed `moderate` profile is superseded by an automatic one, the
   downgrade **fails** rather than proceeding, and the refusal is recorded as
   attention. Today that collision is an unhandled exception path in
   `_automatic_profile`; making it an explicit refusal is a required follow-up,
   not an optional one.

4. **Retroactive automation is not redesign in disguise.** The 5 published
   `moderate` cards and 47 `low` cards were machine-signed without the
   reproducibility evidence that decision 5 establishes. They are **grandfathered
   on the evidence that already exists**, and no migration is owed: every
   automatically generated profile's `certainty_rationale` already begins
   "AI GRADE assessment (literature-review-ai/<version>)", so the policy version
   that produced each row is stored and per-row distinguishable today (220 / 226
   rows; the 6 without it were not produced by this path). A *second* stored label
   saying "grandfathered" would add nothing the version string does not already
   answer. They are not
   re-adjudicated as part of this record, and they do not become the precedent
   that skips decision 5 for future cards.

5. **The capability boundary is a pipeline gap before it is a model limit, and
   must be closed on that basis.** `_source_adjudication` is **deterministic — it
   calls no model at all** (it parses the stored full text, attests the issue's
   quoted evidence against it, and cross-checks Claim/Result structure). An
   earlier draft of this record said to re-adjudicate the boundary papers "with a
   stronger model"; that is incoherent, because the step has no model to
   strengthen.

   Measured on 2026-10-13, over every material (`must_resolve`) issue across the
   168 papers carrying a consistency report:

   | Disposition | Issues |
   | --- | ---: |
   | Adjudication proceeds | 22 |
   | Declines — the issue's `evidence` is **not a verbatim quote** from the full text | **43** |
   | Declines — the issue's `evidence` carries no source quote at all | 15 |

   The dominant decline is the second row, and it is not a disagreement about
   meaning — it is the same complaint, one stage later. `_EXTRACTION_PROMPT`
   already requires that "每个 evidence 必须从全文复制一段连续、逐字原文", and
   `ai_extraction.py` **enforces exactly that for the extraction's own evidence**
   — the `attest(...)` call at line 515, whose `missing` non-empty case raises
   `PaperAnalysisError` — and the codebase's only `attest(` call is that one. The
   consistency stage imposes the same requirement in the prompt but **never
   attests `issue.evidence`**, so a model that paraphrases when it reports a
   difference blocks the deterministic adjudicator that was built to resolve it.

   Therefore, before any accuracy question is asked of these papers:
   - **Attest `issue.evidence` (or the fields of `ConsistencyReport` that carry
     it) the way the extraction path already does**, and test how many of the 43
     the deterministic adjudicator then clears. That is a repair carrying no
     epistemic risk: it makes the machine check itself, not assert more.
   - Only the residue — material issues that quote the source verbatim and still
     cannot be adjudicated — is a genuine capability boundary, and that residue is
     the population a person must handle.
   - **Do not build a "stronger model" harness for this step.** There is nothing
     for it to answer.
   - **Zero errors remains the bar for a verdict that changes patient-facing
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
remove it. A third channel triples cost to move the same unresolved differences —
and decision 5 shows that the differences are unresolved mostly because the
pipeline does not yet attest the consistency stage's own evidence, not because
the source is ambiguous. Adding a channel would multiply a defect it does not
address.

## Consequences

**Easier.** "Who decided this, on what grade, under which policy version" becomes
answerable per row. The no-full-text and never-extracted papers become ordinary
data work with no judgement attached. Decision 5's repair is cheap, bounded, and
falsifiable, and if it clears the bulk of the 43 it converts what looked like a
human-throughput ceiling into an ordinary bug fix.

**Harder, and now capped.** The pipeline's throughput ceiling for *contributing
new grades* is not the model's rate: an AI-only path cannot lift a topic to
`high` or clear the action threshold, so topics whose evidence deserves an action
message need a human in the loop by construction. The residue after decision 5 —
material issues that quote the source verbatim and still cannot be adjudicated —
is **permanent for those papers**: undecidable from their text, with nothing a
stronger model could do because no model is involved. The `needs_review` verdict
is therefore not a backlog to drain but a standing population that needs a human
channel sized for it. Note the distinction: a `needs_review` verdict marks two
very different things — "the channels disagree" (which the pipeline handles by
attesting the difference against the source and adjudicating, and which cleared
22 of 65 quotable material issues) and "the source could not decide it" (which
only a person resolves). The second is the standing population; the first drains
itself, and
the 11 superseded refusals above are that draining observed.

**A risk this record accepts, explicitly.** 5 `moderate` cards are live on machine
signatures with no measured error rate, and are grandfathered (decision 4) rather
than endorsed. The exposure is narrower than the grade suggests — no card carries
reviewed action content, so a wrong `moderate` changes a patient's displayed
certainty, not their behaviour. If patient-facing accuracy ever needs defending,
this is still the first place it is weak, and decision 5 is the remedy.

**A cost this record does not resolve.** 22 papers need full text absent from the
corpus, and the collection path that would fetch it is the one paused by
`mem-20260821-ranlei-002` for resource reasons. Re-enabling it is a separate
decision with its own capacity evidence.

**What this record does not establish.** It does not claim the machine-signed
99.8% is *correct* — only that it is recorded, checkable, and consistent with
what runs. No error rate has been measured. If one is needed for a compliance
purpose, it does not exist yet and this record does not supply it.
