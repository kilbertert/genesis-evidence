import { z } from "zod";

/**
 * The architecture-review pass's output contract.
 *
 * Split out of `architecture-review.ts` so `output.check.ts` can exercise the
 * gate without running the agent. The one assertion that matters here is that
 * `duplicationAfter < duplicationBefore`: it is the difference between a
 * deepening that concentrates knowledge and one that relocates it, and nothing
 * downstream can tell those apart.
 */

const Measured = z.object({
  evidence: z.array(z.string().min(1)).min(2),
  files: z.array(z.string().min(1)).min(1),
  tests: z.array(z.string().min(1)).min(1),
});

export const Proposal = z
  .object({
    status: z.literal("proposed"),
    title: z.string().min(1).max(256),
    body: z.string().min(1),
    oneLineSummary: z.string().min(1),
    candidatesConsidered: z.array(z.string().min(1)).min(1),
    duplicationBefore: z.number().int().min(0),
    duplicationAfter: z.number().int().min(0),
    countCommand: z.string().min(1),
    deletedLines: z.number().int().min(1),
    preserved: z.array(z.string().min(1)).min(1),
    measured: Measured,
  })
  .refine((value) => value.duplicationAfter < value.duplicationBefore, {
    message:
      "duplicationAfter must be strictly less than duplicationBefore — relocating a declaration is not an owner",
    path: ["duplicationAfter"],
  });

export const Finding = z.object({
  status: z.literal("finding"),
  title: z.string().min(1).max(256),
  body: z.string().min(1),
  oneLineSummary: z.string().min(1),
  location: z.string().min(1),
  observed: z.string().min(1),
  candidatesConsidered: z.array(z.string().min(1)).min(1),
});

export const Skipped = z.object({
  status: z.literal("skipped"),
  reason: z.string().min(1),
});

export const PromptOutput = z.discriminatedUnion("status", [
  Finding,
  Proposal,
  Skipped,
]);

/** Parse a raw `<output>` payload. Throws with zod's message on a gate failure. */
export function parseOutput(raw: string) {
  return PromptOutput.parse(JSON.parse(raw));
}
