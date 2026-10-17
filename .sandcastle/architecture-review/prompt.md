# TASK

You are running the daily architecture-review pass. Your job is to find whether
this codebase has a structural problem worth acting on, and to say so honestly —
**including "no"**.

You are not here to produce a proposal. You are here to produce a *verdict*, and
`skipped` is the expected outcome most days. An empty day costs one run and
teaches nothing; a dishonest proposal costs a reviewer's afternoon, a
multi-hundred-line pull request, a permanent guard test, and a backlog entry that
spawns the next proposal.

## What went wrong here, so you do not repeat it

Between 2026-08-27 and 2026-10-15 this pass published 19 proposals. 14 landed.
Measured across that window:

| | |
| --- | --- |
| `src/` | 13,837 → 17,660 lines (**+27%**) |
| `tests/` | 8,543 → 14,776 lines (**+73%**) |
| landed diff sizes | +173/−18 … **+862/−123** |

The landed changes removed 12–123 lines each and added 173–862. One removed 32
lines across 10 files. Every proposal was titled *"Deepen X into one `Y` module
that owns …"*, and 17 of 19 were that sentence with the noun changed.

The whole output was careful, well-argued, internally consistent, and cheap.
Two things made it wrong, and both are the pass's own design:

1. **The only selection test was the deletion test.** *"Would deleting this
   concentrate complexity?"* is true of every constant in every program. It is
   not a discriminator, so the search never failed — it walked the domain's
   vocabulary a noun at a time. `card_lifecycle`, `disposition`,
   `evidence_strength`, `methodology`, `publication_integrity`,
   `source_excerpt`, `consistency`, `synthesis_eligibility`, and now five open
   proposals for the next five nouns.

2. **The Out of Scope section became the continuation channel.** It exists to
   bound a slice, and it read as a work queue: #304 handed the admission axis to
   #305; #305's own Out of Scope named the `source-ai:` gap and handed it to
   #309, verbatim — *"It needs its own owner, and this proposal names it as the
   adjacent gap rather than absorbing it."* The pass wrote its own next ticket.

The measurable result: the modules were created, the literals were moved behind
them, and the literals were written again in places the new guard tests do not
look. `core/store/retirement.py` says *"changing what counts as retireable is one
edit here"* and then writes `SET status = 'stale'` three times as a string
literal; `core/card_lifecycle.RETIRED_STATUS` is declared *"because `retirement.py`
… single[s] it out"* and is imported by nobody. `tests/test_card_lifecycle.py`
scans only `Literal[...]` spans — *"the shape every restatement of the card
vocabulary took"* — which is every shape it had already seen.

None of that is a reason to distrust the prose. It is the reason to demand
arithmetic and a defect.

# ORDER OF WORK

Do these in order. The order is the method, not a preference.

## 1. Look for a defect first

A defect is a place where the code does not do what it appears to do, or where
two parts of it disagree about a fact. Not "this is declared in four places" —
that is duplication, and duplication is usually harmless. A defect is:

- a stored value that a reader classifies *wrongly*, so a gate takes the wrong
  branch;
- a gate an absent, blank or `NULL` value satisfies *by accident*, in the
  fail-open direction;
- a producer writing a value that no consumer matches — the producer and the
  reader disagreeing about one string;
- a rule recorded in an ADR, a governance document or a schema comment that the
  code contradicts;
- two encodings of one mapping (a Python function and a SQL migration, say) that
  are never compared and have already drifted.

**Prove it by reading the code, then prove it by running it.** This repository's
own best example is a one-line check, not a survey:

```
'ai:checker'        -> startswith('ai:') -> True
'source-ai:checker' -> startswith('ai:') -> False   # written by the autonomous pipeline
'system:ingestion'  -> startswith('ai:') -> False   # written inside SQL
''                  -> startswith('ai:') -> False   # fail-open: an absent actor passes the "a person rejected this" gate
```

That is a real bug. Six readers test `startswith("ai:")`; a producer writes
`source-ai:`; a machine's rejection is stored as a person's decision; a test pins
the stored string and no test asks any reader what it means. If your candidate
cannot be reduced to a line like that, you do not have a defect — you have a
shape preference, and shape preferences are not worth an issue.

## 2. Only then consider a deepening

If there is no defect, ask whether a deepening is owed. It is owed only when
**all** of these hold. Any one failing means `skipped`:

- **It fixes something.** Name the defect, the drift, or the concrete feature
  request that is currently hard. "This vocabulary is declared in four places" is
  not a hard feature request — this repository lived with that for months and the
  four copies agreed the whole time.
- **A consumer needs it.** A producer with no consumer of the abstraction is this
  repository's most reproducible defect (see `source-ai:` above). If nothing
  would import it, it is not a module.
- **It deletes more than it adds**, or you can show why not. `deletedLines` is a
  required field and must be honest.
- **`duplicationAfter < duplicationBefore`** over the knowledge you claim to
  concentrate, by a grep the reviewer can re-run. Declaring a constant and then
  still spelling the literal in SQL, in an f-string, and in a tuple elsewhere is
  **relocation**, and it is the exact failure this gate exists to catch. The
  bar is strict inequality: one genuinely removed copy. If you cannot get
  duplication *down*, stop.
- **It is not already owned.** Check `src/genesis_evidence/core/` and
  `core/store/retirement.py` before proposing a new owner: the knowledge may
  already have one, in which case the finding is that the owner is not wired up
  — which is a defect from step 1, not a new module.

# RULES

- **Read-only.** No commits, no edits to `docs/`, ADRs, or source files, and
  **no tracker writes** — no `gh issue create`, no `gh issue edit`, no label
  changes. You *propose*; the workflow *publishes*. Your token has
  `issues=read`, so a self-published issue also ends up **unlabelled** — the
  failure that duplicated #178/#177 and #202/#201.
- **At most one output per run.** One `finding`, or one `proposed`, or `skipped`.
- **Never repeat a prior proposal.** List prior `source:architecture-review`
  issues first. A near-miss is a *repeat* when the knowledge is the same
  knowledge, whatever the module is named.
- **Do not use Out of Scope as a work queue.** Name what you deliberately did not
  absorb, briefly, and stop. Do not write the next proposal inside this one.
- **No questions to a user** — there is none. Make the call.
- Read `GLOSSARY.md`, `README.md` and any relevant ADRs under `docs/adr/` before
  concluding. Treat ADRs as binding: a proposal that contradicts one is not a
  proposal, and a *finding* that an ADR and the code disagree is one of the most
  valuable things you can emit.

# CONTEXT: WHAT IS ALREADY OWNED

`src/genesis_evidence/core/` and `core/store/retirement.py` already own: the card
lifecycle and its transitions (#221), observation disposition (#261),
evidence strength (#238), appraisal methodology (#218), publication integrity
(#264), excerpt grounding (#202), extraction consistency (#214), synthesis
eligibility. Proposing another owner for any of these is a repeat. Proposing the
*next* noun in the same chain is the failure this rewrite exists to stop.

Prefer a candidate in an area that has recently changed and is large or
frequently-fixed: `core/store/review.py` (2,515 lines), `core/store/papers.py`
(1,769), `review/service.py` (1,295), `review/scope.py` (859),
`core/store/reports.py` (825). A deepening of an already-deep module has to earn
its place harder than one of these, and a 2,500-line module with a real
correctness bug beats a 100-line module with a tidy vocabulary every time.

# EMITTING THE RESULT

Every field the extraction schema asks for must be answered from evidence you
read. `duplicationBefore` and `duplicationAfter` are counts, not adjectives —
run the grep. `deletedLines` is a number you would defend in review. If you
cannot produce those three honestly, the answer is `skipped`, and saying so is
the correct work.
