# EMIT STRUCTURED OUTPUT

You have finished the architecture-review pass. **Do not explore further or make
any changes** — only report the outcome.

End your response with a single `<output>` block holding one of the three shapes
below. The JSON is machine-parsed; do not add fields beyond those listed.

## A defect in the code — the preferred outcome

Emit this when the code does not do what it appears to do, and you can point at
the line that proves it.

<output>
{
  "status": "finding",
  "title": "Short statement of the defect, naming the code",
  "body": "Full Markdown body: the location, what the code does, what it appears to do, the consumer that is wrong, and the smallest correct fix. If the fix needs a design decision, say so and describe the defect only.",
  "oneLineSummary": "One line.",
  "location": "src/genesis_evidence/review/service.py:561",
  "observed": "The literal line you ran or read that shows the defect, e.g. \"'source-ai:checker'.startswith('ai:') -> False, while the six readers at service.py:265,272,277,536,542 and store/review.py:1874 test exactly that prefix\"",
  "candidatesConsidered": ["candidate 1", "candidate 2"]
}
</output>

`location` and `observed` are required: they are what a reviewer checks first,
and a finding without them is a claim, not a finding.

## A deepening — only if it survives every gate in the prompt

<output>
{
  "status": "proposed",
  "title": "PRD title (the workflow creates the issue from this)",
  "body": "The full PRD body in Markdown.",
  "oneLineSummary": "One line.",
  "candidatesConsidered": ["candidate 1", "candidate 2"],
  "duplicationBefore": 13,
  "duplicationAfter": 1,
  "countCommand": "grep -rn \"startswith(\\\"ai:\\\")\" src | wc -l",
  "deletedLines": 42,
  "preserved": ["what must not change"],
  "measured": {
    "evidence": ["file:line — each copy of the knowledge today", "file:line"],
    "files": ["src/..."],
    "tests": ["tests/..."]
  }
}
</output>

Field rules:

- `duplicationBefore` / `duplicationAfter` — integer counts over the knowledge
  you claim to concentrate, counted across `src/`. Run `countCommand` and report
  what it prints. **`duplicationAfter` must be strictly less than
  `duplicationBefore`**; the workflow rejects the run otherwise, and a tie means
  you relocated a declaration rather than giving it an owner.
- `countCommand` — the exact shell command that produces both counts, so a
  reviewer can re-run it rather than trust the numbers.
- `deletedLines` — how many lines this change would *delete*. Must be ≥ 1. An
  honest small number is worth more than a large claim.
- `preserved` — the behaviours the change must leave untouched. At least one.
- `measured.evidence` — at least two entries. Each is a `file:line` locator or a
  verbatim quote of a copy that exists today. If you cannot produce two, there is
  no duplication to concentrate and the answer is `skipped`.

## Skipped — no qualified candidate this run

Expected on most days. Choosing this is the correct work when the gates fail.

<output>
{
  "status": "skipped",
  "reason": "Which gate failed, for which candidates — e.g. 'all three candidates relocate a declaration without removing a copy; no defect found in the areas examined'."
}
</output>

`reason` should name the gate each candidate failed, so a later reader can tell a
quiet day from a broken pass. Do not pad it with a candidate you would not have
proposed anyway.
