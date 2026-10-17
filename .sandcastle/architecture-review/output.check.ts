/**
 * Runnable regression check for the architecture-review pass's publication gate.
 *
 * Run: `npx tsx .sandcastle/architecture-review/output.check.ts`
 *
 * The pass published 19 proposals between 2026-08-27 and 2026-10-15 at a cost of
 * +27% source and +73% test lines, and every landed one relocated a declaration
 * rather than removing a copy. The gates below are the whole defence against
 * that happening again, so they get a runnable check rather than review-only
 * confidence — the same reason `.sandcastle/profile-network.check.ts` exists.
 *
 * A check that cannot fail is not a check: each assertion below is paired with
 * the shape it must reject.
 */
import { parseOutput } from "./output.js";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`architecture-review output check failed: ${message}`);
}

function rejects(raw: unknown, because: string): void {
  let threw = false;
  try {
    parseOutput(JSON.stringify(raw));
  } catch {
    threw = true;
  }
  assert(threw, `must be rejected: ${because}`);
}

const proposal = {
  status: "proposed",
  title: "Deepen X into one XOwner module",
  body: "body",
  oneLineSummary: "one line",
  candidatesConsidered: ["a", "b"],
  duplicationBefore: 13,
  duplicationAfter: 1,
  countCommand: 'grep -rn "startswith(\\"ai:\\")" src | wc -l',
  deletedLines: 42,
  preserved: ["the reopen gates keep their outcomes for every current actor"],
  measured: {
    evidence: ["review/service.py:265", "core/store/review.py:1874"],
    files: ["src/genesis_evidence/core/x.py"],
    tests: ["tests/test_x.py"],
  },
};

// A well-formed proposal passes — otherwise every rejection below proves nothing.
const accepted = parseOutput(JSON.stringify(proposal));
assert(accepted.status === "proposed", "a measured proposal must parse");

// THE gate: a tie means the declaration moved, not that it gained an owner.
// This is the real failure mode — `core/store/retirement.py` says "changing what
// counts as retireable is one edit here" and then writes `SET status = 'stale'`
// three times, while `core.card_lifecycle.RETIRED_STATUS` is imported by nobody.
rejects({ ...proposal, duplicationAfter: proposal.duplicationBefore }, "duplication was relocated, not removed");
rejects({ ...proposal, duplicationAfter: 99 }, "duplication went up");

// "Deepenings" that add without deleting are the pattern this pass fell into:
// landed PRs ran +173/-18 to +862/-123.
rejects({ ...proposal, deletedLines: 0 }, "nothing is deleted");

// Counts and the command must be present and reproducible — a claim without
// arithmetic is what a reviewer cannot argue with.
rejects({ ...proposal, duplicationBefore: undefined }, "no before count");
rejects({ ...proposal, countCommand: undefined }, "no way to reproduce the counts");
rejects({ ...proposal, preserved: [] }, "no statement of what must not change");

// One copy is not duplication: the pass must point at two, or say `skipped`.
rejects(
  { ...proposal, measured: { ...proposal.measured, evidence: ["only/one.py:1"] } },
  "fewer than two pieces of evidence",
);

// A defect finding is the preferred outcome, and it must carry the line that
// proves it — `location` + `observed` are what the reviewer checks first.
const finding = parseOutput(
  JSON.stringify({
    status: "finding",
    title: "A machine's claim rejection is stored as a person's",
    body: "body",
    oneLineSummary: "one line",
    location: "src/genesis_evidence/review/service.py:561",
    observed:
      '\'source-ai:checker\'.startswith(\'ai:\') -> False, while six readers test that prefix',
    candidatesConsidered: ["a"],
  }),
);
assert(finding.status === "finding", "a located finding must parse");
rejects(
  { status: "finding", title: "t", body: "b", oneLineSummary: "s", candidatesConsidered: ["a"] },
  "a finding with no location or observation",
);

// Choosing `skipped` must always be available — an output contract that cannot
// express "nothing qualified" is what made this pass propose a noun per day.
const skipped = parseOutput(JSON.stringify({ status: "skipped", reason: "every candidate relocated a copy" }));
assert(skipped.status === "skipped", "skipped must parse");

rejects({ status: "invented", reason: "?" }, "an unknown status");

// The workflow's publish step branches on these three literals; if this file and
// that step disagree, a run publishes nothing or publishes unlabelled.
const workflow = await import("node:fs").then((fs) =>
  fs.readFileSync(new URL("../../.github/workflows/architecture-review.yml", import.meta.url), "utf8"),
);
for (const status of ["proposed", "finding", "skipped"]) {
  assert(workflow.includes(status), `the publish step must handle status '${status}'`);
}

console.log("architecture-review output contract ok");
