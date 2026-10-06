# Genesis Evidence agent instructions

Read `GLOSSARY.md`, `docs/`, and relevant ADRs before changing code. Preserve
the product safety boundary and run the documented Python checks.

When a change alters behavior a reviewer or a patient-facing consumer can
observe, the documented Python checks are not enough on their own: use the
`verify-genesis-evidence` skill to drive the real service and capture evidence
before declaring the work done.

**Never point a verification run at `var/genesis-evidence.sqlite3`** — that is
the working database. The skill's helper creates a throwaway database for you;
see `CLAUDE.md` ("Verification") for the standard.

<!-- afk-bootstrap:managed:start -->
## AFK workflow gate

For idea or planning work, read `docs/afk-workflow.md` and the applicable
files under `docs/agents/` first.

`/grill-with-docs` ends only when its frontier is empty: report
`GRILLING_COMPLETE`, summarize the shared understanding, ask the user to
confirm it, and stop. Confirmation completes grilling only. Wait for the user
to explicitly invoke `/to-spec`, `/to-tickets`, `/implement`, or
`/implement-spec`; do not enter another phase automatically. Multi-session
work uses `/to-spec` then `/to-tickets` before implementation.
<!-- afk-bootstrap:managed:end -->
