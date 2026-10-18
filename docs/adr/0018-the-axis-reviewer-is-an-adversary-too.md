# 18. The other reviewer gets the adversarial mandate too

Date: 2026-10-18
Status: Accepted
Scope: AFK scaffold template 1.7.25 (a new `# YOUR JOB` section in
`.sandcastle/review/axis-prompt.md`); no runtime code is touched.

## Context

This repository has **two** review paths, and both are reviewers:

| Path | Prompt it loads | Covered by 1.7.24 |
|---|---|---|
| Local ralph (`planner.ts:292`) | `.sandcastle/review-prompt.md` | yes |
| Actions `agent:review` (`review.ts:178`) | `.sandcastle/review/axis-prompt.md` | **no** |

1.7.24 delivered the adversarial mandate to the first one, and its ADR explicitly
considered — and correctly rejected — `.sandcastle/review/prompt.md` (the fixer
that runs after the two axes; a different job). What that reasoning missed is that
**the same directory holds `review/axis-prompt.md`, which is the first-pass
reviewer on the other path**.

So the rule reached one review path and not the other. **This is the fourth time
the AFK scaffold has shipped "the rule went to the path the author had in mind,
not to every path that loads it"** — the earlier three being: the write-back duty
reaching 2 of 5 implement prompts; the review mandate written to the scaffold only
(0 of 3 consumers); and the delivery fix missing the second file in the same
directory. afk-bootstrap 1.7.25 closes it.

## Decision

**This repository's `.sandcastle/review/axis-prompt.md` gains `# YOUR JOB`, with a
two-tier criterion:**

- **demonstrate it if you can**: `If you can reproduce a failure by running
  something … do that and report the output; a demonstrated failure beats a
  described one.`
- **when you cannot, describe it precisely enough to be checked**: `"The input X
  reaches this branch, and the guard above it only covers Y" is a finding. "This
  looks fragile" is not — it names no input, so nobody can check it.`

Plus: **a green suite is not evidence** (the tests in this diff were written by
the author, so they encode the author's assumptions), and if a real attempt still
yields no counterexample, say what you tried.

**The boundary of "read-only"**: this prompt forbids `Do not edit files, commit,
push, or reply to GitHub comments` — **it does not forbid executing**. `review.ts`
uses the same `claudeProfile` as the other paths and runs `sandbox-prepare.sh` in
`onSandboxReady`, so it has a sandbox. The section therefore says the reviewer
**may** run something to reproduce a failure; it simply must not change anything.

## Alternatives Considered

- **Copy the section from `review-prompt.md`.** That criterion says "a finding is
  a failing output you ran", but this prompt's output is a JSON `findings` array
  and it must often judge situations it **cannot actually reproduce** (a missing
  upstream, production data). Copying would make "you ran it" the only acceptable
  form, which pushes the reviewer either to pretend it ran something or to
  self-reject a genuinely valuable described counterexample.

- **Assert that this reviewer "cannot execute".** afk-bootstrap's first version
  wrote exactly that (`You cannot run the code`) and it was wrong. Left in, it
  would **discourage a reviewer from reproducing a failure it is able to
  reproduce** — worse than saying nothing, because it wears the plausible costume
  of "I am read-only, so I only describe".

- **Ship it to the scaffold only and let projects pick it up later.** That is
  precisely the 1.7.23 mistake (0 of 3 consumers). The existing repositories are
  the ones **actually running** AFK.

## Consequences

- Both review paths now carry the adversarial mandate, each worded to what that
  reviewer can actually do.
- **The independence is still limited, and that has to be said plainly.** It comes
  from fresh context and this mandate, **not from model diversity** — every AFK
  project on this host shares one model route.
- **Only the review prompt changes, no runtime code**, so no service behavior is
  affected.
- **Cost**: the repository now holds two similar-looking adversarial texts with
  different criteria. That is deliberate — merging them would load the other
  path's criterion onto a reviewer that often cannot satisfy it. Both texts state
  their reason, so a reader will not mistake one for copy-paste debris.
