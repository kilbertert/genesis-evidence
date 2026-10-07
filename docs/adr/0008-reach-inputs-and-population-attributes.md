# 0008: Reach needs a patient-side input, and the evidence service has none

Status: Accepted

Date: 2026-10-07

Supersedes: none (this record adds a missing decision; it does not overturn a
recorded one)

**Execution status: the decision is to *not* build it here.** This record settles
where the reach inputs live and what the evidence service will refuse to do
without them. The slices that extend the boundary are separate tickets; until
they land, the evidence API has no demographic fields, and the conditions that
depend on them stay unreachable.

## Context

`GLOSSARY.md` names three ways a `condition` reaches a patient: **指标**
(report-recognisable), **问卷** (the report has no corresponding item), and
**人群属性** (sex, life stage). The catalog expansion (PRD #239) gave the second
batch real conditions, and two of them expose a gap the first batch never did:

- `COND_MALE_OSTEOPOROSIS` and `COND_MENOPAUSE_HEALTH_RISK` are restricted by
  sex, and `COND_COGNITIVE_DECLINE_RISK` by age;
- `COND_BPH_RISK` is male-only.

`EvidenceMatcher` routes by `metric_code` alone. It receives no sex, no age, and
no questionnaire answers — `EvidenceMatchRequest` carries observations and
nothing else (`core/contracts.py`), and the health-flow adapter has no field to
populate from. So a metric claimed by a sex-restricted condition would reach
**every** patient: a man's LDL result would be answered with 更年期健康风险, and a
woman's low bone density with 男性骨质疏松风险.

The tempting shortcut is to treat 「人群属性」 as a filter the *matcher* applies —
reject the condition when the patient's sex disagrees. That does not work and
would be worse than not building it, for two reasons.

1. **The fact is not available, at the layer that needs it.** The evidence
   service is a read-only published-card matcher behind an HTTP boundary
   (`/api/evidence/matches`). The patient's sex and birth date live in
   health-flow, which owns the patient record. Pushing them into the matcher
   means extending a versioned public contract that has an existing v2/v3
   compatibility surface — a decision with its own review, not a side effect of
   a catalog change.
2. **The wrong layer would own the rule.** Sex-restricted *evidence* is not the
   same thing as sex-restricted *matching*. Whether a 45-year-old man should see
   更年期 content is a question about the patient, and it belongs where the
   patient is known. Encoding it as a metric filter puts a patient-attribute
   predicate inside the evidence catalog, where the catalog stops being a
   description of *the evidence* and starts being a description of *the patient*.

## Decision

**Reach inputs other than a confirmed report observation are supplied by the
caller, never inferred by this service; and until the boundary carries them, a
condition that depends on one is unreachable by design.**

1. **A condition whose reach depends on a population attribute claims no
   metric.** It stays in the catalog — it is a real health problem, and the
   覆盖矩阵 must show it — with an empty `metrics` tuple, exactly like
   `COND_CHRONIC_CONSTIPATION`. Unreachable is a *state*, not a defect
   (`GLOSSARY.md`, 「可触达」).
2. **Population attributes are not evidence scopes.** The resolver already
   produces `condition:<code>` scopes for metric-less conditions, but no
   `metric:` scope can be derived from a patient's sex, and the matcher does not
   consume `condition:` scopes for reach. Do not invent a `population:` scope
   family to paper over the missing input.
3. **The inputs this would require, and who supplies them** — recorded so the
   later ticket starts from facts rather than rediscovery:

   | Input | Needed by | Supplier |
   | --- | --- | --- |
   | `patient_sex` | 男性骨质疏松、良性前列腺增生、更年期 | health-flow patient record |
   | `patient_age` or birth date | 认知功能下降、更年期、前列腺增生 | health-flow patient record |
   | 问卷 responses | 认知、干眼、失眠、骨关节炎、便秘 | health-flow questionnaire feature (separate ticket, per #239) |

4. **The evidence service refuses to guess.** A condition that needs an input
   this service does not have is not served. Fail-closed, not best-effort: a
   wrong finding reaches a patient as a factual claim about their body, and a
   missing one does not.

## Consequences

- The four sex/age-restricted conditions stay at zero servable scopes, and the
  覆盖矩阵 shows them that way. That is the honest render of what the service
  can do today, not an oversight.
- The body-composition half of this ticket is unaffected: `BMI`, `腰围`, and
  `体脂率` are ordinary report metrics, and 超重与肥胖 reaches patients through
  the report path with everything else.
- Extending `/api/evidence/matches` with patient context is a **contract
  change**: it needs its own ticket, its own acceptance scenarios, and a schema
  version decision (the current surface already serves v2 and v3). It is not
  done here.
- If a blood-derived or specimen-ambiguous name ever needs disambiguation (see
  the 裸 `pH`/CKD cases in T5/T6), the specimen — not the patient — is the input;
  the two problems are distinct and must not be merged.

## Rationale

The service is a **matcher of confirmed report observations to published
cards**. Every input it has describes the report; none describes the patient.
That is not an accident of the current code — it is what lets the boundary stay
a pure function of a versioned request, with no patient record to read, no
session, and no identity. Adding a demographic field to gain four conditions
trades that property for a much smaller win than it looks: the conditions it
unlocks are exactly the ones whose evidence base is youngest, so they would
serve `planned` scopes at best.

Fail-closed is the right default here because the failure modes are asymmetric.
A served finding is read as a statement about the reader's body. Sending a man
to the gynecology department, or telling a woman she has a male-only condition,
is a different class of error from saying nothing.

The asymmetry does not depend on the patient being *told* why nothing appeared.
A metric-less condition never enters the matcher, so it can appear in neither
`findings` nor `unmatched`; `patient_reply_v3`'s explanations are about
observations it could not use, and it has nothing to say about a condition it was
never asked about. Silence here is real silence, not an explained omission — an
honest limit of the current design, recorded rather than papered over. Making that
silence legible is a separate question (T9 显式化 covers the "not in scope"
notice for observations; the population case has no observation to notice).

## Alternatives Considered

- **Add `patient_sex` to `EvidenceMatchRequest` now.** Rejected: it extends a
  versioned public contract to serve four conditions, and health-flow is not yet
  sending it. The change would be unexercised until health-flow ships its half,
  which is exactly the "fixture the real flow never produces" failure.
- **Filter in the matcher using a static rule** (e.g. drop 更年期 when the
  patient is male). Rejected: the matcher does not know the patient, so the rule
  would have to be a guess — and there is nothing to guess from.
- **Give the restricted conditions a metric anyway and accept the over-reach.**
  Rejected: it tells a man he may be entering menopause. The asymmetry of harm
  (a wrong finding vs. a missing one) settles it.
