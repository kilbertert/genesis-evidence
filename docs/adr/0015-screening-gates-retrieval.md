# 0015: Screening gates retrieval gates extraction — the title/abstract decision had no autonomous entry point

Status: Accepted

Date: 2026-10-17

Supersedes: none. This record closes a lock that neither the queue nor the prompts could
break, and corrects a claim this repository's own deployment record made about it.

## Context

`nutrition-electrolytes` sat at 10 collected papers and zero cards for eight days. The
pipeline looked idle rather than blocked, because nothing failed: no job was queued, no
error was raised, and the topic's ledger simply stayed open.

Measured on a production copy (2026-10-17):

| | |
| --- | ---: |
| collected papers in the topic | 10 |
| `title_abstract_decision` set | **0** |
| full texts stored | 7 |
| full text retrievable | 3 (rights-restricted) |
| extractions | 0 |

The three facts below are each individually reasonable and jointly deadlock:

1. **Retrieval requires screening.** `list_pending_full_texts` selects
   `title_abstract_decision = 'included'` (`papers.py:885`). A paper nobody screened is a
   paper nobody fetches.
2. **Screening had no autonomous entry point.** `auto_review_paper` began with
   `trace = item.get("extraction_trace") or {}`; with no extraction it fell into
   `if not trace:` and returned `attention_required` ("paper has no stored full text")
   without ever reaching the screening loop. That loop sits *after* the branch, because it
   needs the model-derived `actor` that only exists once a trace does.
3. **The paper that most needs screening is the one with no full text**, which is exactly
   the branch that returned early.

So: no full text → no screening → no retrieval → no full text.

### Why the papers with full texts looked screened

Seven of the ten already had full texts (retrieved during collection). Those were pushed
into extraction — `enqueue_extraction` gates on `full_text_available`, not on the
title/abstract decision — and only *after* an extraction produced a trace did the
screening loop become reachable. The decisions appeared as a side effect of extraction.

**This is the part the earlier deployment record got wrong.** `acceptance-20261015-…`
said "extraction screened the seven papers it touched". That is true of the *copy* that
run produced, never of production: production's own rows still showed `ta = NULL` for all
ten two days later. A record that describes a throwaway copy's state as though it were the
system's is worse than no record, because the next reader plans from it.

### Two latent defects the lock hid

Fixing the entry point exposed two more, both of which would have failed the moment
screening ran without an extraction:

- **`_collection_dict` suggested a full-text decision for `pending` papers.** Its condition
  was `full_text_retrieval_status != "not_retrieved"`, which lumps "retrieved" together
  with "still waiting". `screen_collection_paper` refuses a full-text decision without a
  stored full text, so the suggestion could only raise (`papers.py:443`).
- **A full-text suggestion could be derived from a design nobody had measured.** The
  suggested decision is computed from `design = extraction.get("study_design", "uncertain")`.
  Without an extraction that is `"uncertain"`, which is never in a topic's
  `eligible_study_designs` — so the suggestion was `excluded / ineligible_study_design`,
  excluding a paper *because the extraction that would have justified including it had not
  run yet*.

## Decision

**Screen before deciding a paper is stuck, and only suggest a decision whose inputs exist.**

1. **The screening loop is one method, `_apply_screening_suggestions`, called from both
   stages.** It is extracted rather than duplicated because the second call site is the one
   that matters: `if not trace:` now runs it *before* the "no stored full text" fallback,
   so a paper without a full text is screened rather than declared stuck.

2. **A decision is never suggested for a paper in a state that cannot accept it.** The
   full-text branch now requires `full_text_retrieval_status == "retrieved"` (not merely
   "not `not_retrieved`") **and** `design != "uncertain"`. Both are preconditions the store
   will enforce anyway; producing a suggestion that fails its own precondition is a defect
   in the suggester, not a workload for the caller.

3. **The actor for a trace-less screening is `ai:screening-ledger`, not a model.** A
   title/abstract suggestion is derived deterministically from the collection record, so no
   model produced it. Naming one would put a false attribution in the audit trail — the
   same failure ADR 0012 decision 6 exists to prevent on the ingestion path.

4. **A rights-restricted paper is a closed outcome, not a pending one.** When retrieval
   returns "rights do not permit evidence processing", the record is `not_retrieved` with a
   reason, a reviewer and a timestamp — which satisfies the topic ledger. The topic then
   proceeds with whatever evidence it does have, and a topic with no included evidence
   correctly refuses to build profiles.

## The third gap: a fix with no driver

Making screening *possible* without a full text (decision 1) did not make it *happen*.
Measured on 2026-10-18, every entry point drove `auto_review_paper` for a different
population, and none of them covered a paper that had never been screened:

| Driver | Which papers it reaches |
| --- | --- |
| `literature/extraction_worker.py` | papers with a `queued` job |
| `review/api.py` | a paper a person clicks on |
| `scripts/rebuild_evidence_bodies.py` | papers already admitted |

An unscreened paper has none of those: it has no job (jobs are created after a full text
arrives), nobody is clicking it, and it is not admitted. Four papers — three in
`nutrition-electrolytes`, one in `nutrition-ckd-risk` — sat in exactly that state and were
closed only by hand.

**Decision 8: the backlog drives screening, and does it before retrieval.**
`PaperStore.list_unscreened_papers` is the dual of `list_pending_full_texts` — one selects
`title_abstract_decision IS NULL`, the other `'included'` — and `literature/backlog.py`
consumes the first, then the second. The order is not a preference: retrieval selects on
`'included'`, so screening it has not done cannot be reached by retrieving afterwards.

The backlog rather than the worker, because the backlog already owns the other
"make the queue make progress" operations (`supersede_excluded_extraction_failures`,
`restore_screening_excluded_extractions`, retrying failed jobs) and adding to it introduces
no new long-running process.

## Alternatives Considered

**Have a person screen the ten papers through the workbench.** This is the strongest
alternative for today's symptom and the cheapest single action: the workbench step 1
already offers the decision, and one reviewer could close the topic in a few minutes. It
loses because it treats the recurrence as a non-event. The same state can be produced by
any future collection run whose retrieval lags its screening — which is the normal case,
since retrieval is the slow, network-bound step. Fixing the entry point makes the
autonomous path do what the workbench already lets a person do; leaving it means every
such topic needs a human to notice, which is precisely the "nobody asked" failure this
record is about.

**Let extraction run first and screen afterwards, as it effectively did for the seven.**
Its case: extraction is what produces the study design, and screening quality is better
with it. It loses because extraction itself requires a full text, and a full text requires
screening. For the seven it worked only because the full text happened to have arrived
during collection; for the three it cannot work at all. It also puts a costly, model-billed
step in front of a cheap, deterministic one.

**Relax `screen_collection_paper` to allow a full-text decision without a stored full
text.** It loses on correctness, not cost: a full-text decision is an assertion about
content the system does not have. Permitting it would let a paper be marked "included at
full text" with nothing to include, and the ledger would report a closure that the evidence
does not support.

**Drop the `design != "uncertain"` guard and let the extraction-less paper be excluded as
`ineligible_study_design`.** This is what the first attempt at the fix actually did, and
it looked like it worked: the ledger closed, every row had a decision. It loses because
the decision is false. The paper was excluded for a design it was never measured to have,
and `ineligible_study_design` is a scientific conclusion the system drew from missing
data. Closing a ledger with a wrong verdict is worse than leaving it open.

## Consequences

**Easier.** A topic whose retrieval and screening are out of step now converges without a
human noticing. The three classes of papers that previously had no path — pending
retrieval, retrieved but unextracted, and rights-restricted — each reach a correct terminal
or intermediate state.

**Harder, and now bounded.** Screening runs before extraction for papers without full
texts, so a `title_abstract` decision is always made on title/abstract information alone.
That is the correct basis for that stage, but it means **the title/abstract stage can no
longer benefit from anything extraction knows.** A topic whose PICOTS cannot be judged from
the title and abstract must rely on the full-text stage, which now waits for a design —
that is, for an extraction — for any paper that has a full text.

**A cost this record accepts.** For a paper with no full text, `auto_review_paper` still
returns `attention_required` after screening (that is its honest state: nothing can be
extracted). What changed is that the paper is now *screened* first, so the next pass sees
`ta = included` and retrieval can proceed. The attention result is unchanged and should not
be read as "nothing happened".

**What this record does not establish.** It does not show that the topic produces cards
once unblocked. The remaining precondition is a paper that reaches `full_text_decision =
included`, and in this topic the seven papers with full texts were excluded on study design
*after* extraction — a scientific outcome, not a lock. The lock is fixed; whether this
particular topic has usable evidence is a separate question, and the answer so far is that
it does not.
