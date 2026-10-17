import * as fs from "node:fs";
import * as path from "node:path";
import * as sandcastle from "@ai-hero/sandcastle";
import { claudeProfile } from "../profile.js";
import { runWithExtraction } from "../run-with-extraction";
import { PromptOutput } from "./output.js";

const OUTPUT_DIR = process.env.OUTPUT_DIR ?? "/tmp";

const result = await runWithExtraction({
  name: `architecture-review-${new Date().toISOString().slice(0, 10)}`,
  ...claudeProfile(process.env.AFK_PROFILE),
  logging: { type: "stdout" },
  promptFile: path.join(import.meta.dirname, "prompt.md"),
  output: sandcastle.Output.object({
    tag: "output",
    schema: PromptOutput,
  }),
  extractionPrompt: fs.readFileSync(
    path.join(import.meta.dirname, "extraction.md"),
    "utf8"
  ),
});

const output = result.output;

fs.writeFileSync(
  path.join(OUTPUT_DIR, "architecture_review_output.json"),
  JSON.stringify(output, null, 2)
);

// Both outcomes that carry a body write the same two files. The publish step
// decides the label from the status; keeping the filenames identical is what
// lets it do that without a second branch to get wrong.
if (output.status === "proposed" || output.status === "finding") {
  fs.writeFileSync(path.join(OUTPUT_DIR, "prd_title.txt"), output.title);
  fs.writeFileSync(path.join(OUTPUT_DIR, "prd_body.md"), output.body);
}

if (output.status === "proposed") {
  console.log(`\nProposed: ${output.title}`);
  console.log(
    `  duplication: ${output.duplicationBefore} -> ${output.duplicationAfter}`
  );
  console.log(`  deleted lines: ${output.deletedLines}`);
  console.log(`  candidates considered: ${output.candidatesConsidered.length}`);
  console.log(`  body written to ${OUTPUT_DIR}/prd_body.md`);
} else if (output.status === "finding") {
  console.log(`\nFinding: ${output.title}`);
  console.log(`  location: ${output.location}`);
  console.log(`  candidates considered: ${output.candidatesConsidered.length}`);
  console.log(`  body written to ${OUTPUT_DIR}/prd_body.md`);
} else {
  console.log(`\nSkipped — no qualified candidate.`);
  console.log(`  reason: ${output.reason}`);
}
