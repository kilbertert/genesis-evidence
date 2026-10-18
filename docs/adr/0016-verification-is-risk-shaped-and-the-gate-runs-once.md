# 16. Verification is risk-shaped, and the full gate runs once

Date: 2026-10-18
Status: Accepted
Scope: AFK scaffold template 1.7.23 (the `.sandcastle/*` prompts and
`CODING_STANDARDS.md`); no runtime code is touched.

## Context

The three AFK implementation prompts carried this block, sitting directly above
the verification gate:

```
Use red-green-refactor where applicable:

1. RED: write one failing test
2. GREEN: implement to pass it
3. REPEAT until the issue is done
4. REFACTOR the code
```

**It is not a testing convention; it is a loop.** `3. REPEAT until the issue is
done` reads as "keep going", and the gate immediately below it is the *full*
`uv run pytest` plus ruff. So the instruction an agent actually reads is "run
everything, then go round again".

Measured on the sibling template (AI-Ops, 1805 test files): the full
`uv run pytest` takes **299.62 s** against **2.91 s** for a single file —
**≈103×**. The run logs (`issue-92..99`) show the gate invoked **four or more
times per issue**. An issue therefore spends its budget re-running a suite it has
already run. **The test-writing is not the cost; the repetition is.**

Worth stating precisely: **this repository has never enabled a coverage
threshold.** There is no `fail_under`, CI does not measure coverage, and
`.sandcastle/policy-check.mjs` never reads it. What the measurement exposes is
not "too many tests" but "one gate asked to run many times in a single lap".

## Decision

**Rewrite the block that carries the loop: keep "let a test lead where a test is
the cheapest oracle", and demote the full gate from "every lap" to "once, at the
end".**

The new text asks for the **narrowest check that can falsify the current change**
(the test files touching it, the typecheck, the linter) while iterating, and the
full gate once at the end. It also says plainly not to manufacture tests to
satisfy a quota — prose, formatting, a rename, or one-line wiring owes none —
and to name the input that would break the change, or say in the commit body that
you cannot, rather than write a test that asserts the implementation back to
itself.

`CODING_STANDARDS.md`'s `Update tests alongside behavior changes; keep the suite
green.` becomes: a regression test is the durable record that old behavior still
holds (**strengthened, not weakened**), and the full suite is a gate you run once
before delivery rather than a loop you spin after every edit.

**This is complementary to `verify-genesis-evidence`, not a replacement.** That
skill drives the real service against a throwaway database and answers "does the
behavior a reviewer or consumer observes hold"; this decision governs the *scope
and cadence* of pytest. Neither treats the other as its evidence — the prompt's
"Tests are not a substitute" line, and the **Never point it at
`var/genesis-evidence.sqlite3`** guard, are both unchanged.

## Alternatives Considered

- **Adopt wholesale the external doctrine that says "stop writing tests, and a
  red suite is no longer an incident."** It collides head-on with
  `.sandcastle/consensus-contract.json`, which lists `deterministic-ci` as a
  non-exceptionable invariant: red suite = red CI = cannot merge. Adopting it
  would sever this repository's own merge gate. The one part adopted is
  **"the author is not the adversary"** (adversarial verification must change
  hands), because this repository's implementer and reviewer are the same model
  family running the same prompt, so their independence is weak.

- **Keep blanket TDD and merely shard the suite.** This does not reach the
  structure that costs: `3. REPEAT` reads as an unbounded loop. The 103× ratio
  says what is being saved is **repetitions**, not test volume.

- **Let `verify-genesis-evidence` carry all verification and cut pytest to a
  minimum.** The over-correction in the other direction: that skill needs a live
  service and a real database, so its cost and feedback latency are wrong for
  every edit — and it cannot cover the pure-logic invariants that
  `test_governance_artifacts.py` and friends exist to pin. The two serve
  different purposes.

- **Deliver the rule by whole-file restage (a `cmp` snapshot).** This
  repository's live prompts already differ from the template by 9–25 lines — the
  hand-applied `uv run pytest` substitution, plus this repository's own
  verification paragraph naming its skill, its throwaway-database warning, and
  its `var/genesis-evidence.sqlite3` red line. A restage would destroy that
  paragraph, and a guard that refuses would make the upgrade silently no-op
  here. An anchored edit is used instead.

## Consequences

- The number of full-suite runs per issue inside the loop drops from 4+ to 1.
- **The standing of regression tests is raised, not lowered**: the invariant
  tests in `test_governance_artifacts.py` and the retrieval/screening gates are
  untouched, and the coding-standards line is rewritten from "keep the suite
  green" to "a regression test is the durable record that old behavior still
  holds, and worth writing for exactly that reason".
- This repository's own verification paragraph and its database red line survive
  byte-intact.
- **Cost**: having the narrowest check judged by the change's author means a
  misjudged scope can under-run. The compensation is that CI still runs the full
  suite, and the prompt requires naming the input that would break the change
  rather than issuing itself a green light.
- No runtime code changes, so no service behavior is affected.
