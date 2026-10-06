## Verification

Unit and integration tests are necessary but not sufficient. When a change
alters behavior a reviewer or a patient-facing consumer can observe, prove it on
the running service before you call it done: invoke the `verify-genesis-evidence`
skill, drive the affected feature, and keep the captured evidence.

`verify-genesis-evidence` lists the covered features in its `features/` map —
check there first rather than inventing a new harness.

**Always run against a throwaway database.** `var/genesis-evidence.sqlite3` is
the working database; the skill's helper creates an isolated temp database and
object store, and tears them down. An empty database renders empty queues and an
empty coverage matrix — that is a correct render of an empty database, not a
verified feature, so seed data through the API when the feature needs it.

When it cannot run — a missing `var/*.env`, a service that will not start, a
feature named on the map but unreachable — that is `blocked`: name the
prerequisite and the route attempted. A blocked verification is never reported as
a pass.

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
