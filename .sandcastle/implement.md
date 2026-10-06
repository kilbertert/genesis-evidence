# AFK implementation

Implement exactly GitHub issue {{ISSUE_NUMBER}}: {{ISSUE_TITLE}}.

Read `GLOSSARY.md`, `AGENTS.md`, the issue, `docs/`, `.sandcastle/CODING_STANDARDS.md`, and the smallest set of relevant source
and tests before editing. Work on one issue only.
Run the Economy ladder defined in the coding standards before choosing an
implementation; stop at the first option that fully satisfies the issue.

Requirements:

1. Preserve the project's existing contracts and fail-closed guarantees. Do
   not invent a parallel runtime contract.
2. Make the smallest coherent change and add or update focused tests for
   behavior you change.
3. Run `uv sync --extra dev && uv run pytest && uv run ruff check` before
   committing. Do not weaken or skip checks.

This repo HAS a project verification skill, `.claude/skills/verify-genesis-evidence`.
If your change alters behavior a reviewer or a patient-facing consumer can
observe, run that skill and capture its evidence before committing. Tests are not
a substitute: it drives the real service against a throwaway database. **Never
point it at `var/genesis-evidence.sqlite3`.** Its evidence is usually gitignored,
so put what you rely on somewhere the commit carries. If you cannot run it, say
so and why in the commit body; do not report it as done.
4. Run `node .sandcastle/policy-check.mjs commit` before committing.
5. Inspect `git diff --check` and the changed-file list before committing.
6. Commit the completed work with a Conventional Commit message.

If the issue is complete, print `<promise>COMPLETE</promise>` after the commit.
If a required human decision, credential, or external environment is missing,
do not guess; explain the blocker and print `<promise>BLOCKED</promise>`.

Do not merge, push, close issues, or modify GitHub state from inside the
container. The host runner owns delivery.
