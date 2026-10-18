# 17. The reviewer is the adversary, not the author

Date: 2026-10-18
Status: Accepted
Scope: AFK scaffold template 1.7.24 (a new `## 0.` section in
`.sandcastle/review-prompt.md`); no runtime code is touched.

## Context

The AFK **review prompt** (`.sandcastle/review-prompt.md`, loaded by `planner.ts`)
asked a reviewer to read the diff, look for anything dodgy, and write a test that
breaks it — but it never said **what the reviewer is in relation to the author**.
This repository's implementer and reviewer are the **same model family running the
same prompts**, so their independence is weak to begin with, and the prompt gave
the reviewer no adversarial mandate at all. **The gap is the mandate, not the
reviewing ability.**

The sibling template 1.7.23 wrote that mandate into
`scaffold/.sandcastle/review-prompt.md` and stopped there. That file is the
bootstrap's source, so the section reached only projects created **after** 1.7.23;
measured across the three live consumer repositories (this one included), **0 of 3**
had it. This is the shape ADR 0003 records — a rule delivered to *the path the
author had in mind* rather than *every path that loads it* — and afk-bootstrap
1.7.24 closes it.

## Decision

**This repository's review prompt gains `## 0. You are the adversary, not the
author`, as the first step of the review process (above the numbered list):**

- you did not write this change, you are not here to agree with it, and your job
  is to **make it fail**;
- produce the **concrete counterexample**: the input, the state, or the sequence
  that breaks it. **A finding is a failing output you ran, not a concern you
  hold**;
- if you cannot break it after a real attempt, say **what you tried and what
  held** — that attempt is the evidence;
- **a green suite is not evidence**: the tests in this diff were written by the
  author, so they encode the author's assumptions. The bug is in the input the
  author **did not consider**. Look for that input first.

## Alternatives Considered

- **Deliver it to new projects only and leave the existing repositories alone.**
  That is exactly what 1.7.23 did, and exactly the defect ADR 0003 records: a rule
  that reaches only the path the author had in mind. The existing repositories are
  the ones **actually running AFK**, so withholding it there means the adversarial
  mandate exists only for projects that have not started work.

- **Deliver it by whole-file restage (`cmp`).** Measured: this repository's
  `review-prompt.md` differs from the template by **17 lines** (the hand-applied
  `uv run pytest` substitution and this repository's own gate wording), so the
  `cmp` guard refuses and the step silently no-ops here. An **anchored**
  insertion is used instead (anchor `## 1. Read the diff and look for anything
  dodgy`, measured to occur **exactly once** in this repository).

- **Also add the section to `.sandcastle/review/prompt.md`** (the fixer that runs
  after the two axes). That is a different stage with a different job — it consumes
  two axis reports and makes repairs, it is not the first-pass reviewer — and the
  scaffold does not carry the section in that file either.

- **Get independence by using a different model.** Not available: every AFK project
  on this host **shares one model route**. So the adversarial quality comes from
  **fresh context plus this mandate plus a required artifact (a concrete
  counterexample)** — not from model diversity.

## Consequences

- The reviewer has, for the first time, an explicit adversarial mandate and a
  criterion that can be checked: a counterexample, rather than a concern.
- **The independence is still limited, and that has to be said plainly.** It comes
  from fresh context and this mandate, **not from model diversity**. This is
  recorded here so it is not read as a stronger guarantee than it is.
- **The change touches only the review prompt, no runtime code**, so no service
  behavior changes; and it does not alter this repository's verification command
  or its tests.
- **Cost**: a reviewer may produce more "suspicious but not defective" entries.
  The mitigation is the criterion itself — what is asked for is a **failing output
  that was run**, not "this looks risky".
