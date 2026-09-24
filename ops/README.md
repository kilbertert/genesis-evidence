# User service deployment

> **This file describes the development host.** The services run on a service
> host in production; see `ops/service-host/` for that deployment. The unit
> files here exist so the same services can be run and exercised on the
> development host.

The development-host services, and the frozen `genesis-health` project they were
originally built beside:

- evidence API: `127.0.0.1:8125` → read-only published-evidence API; deliberately
  not exposed as a user domain
- user portal: `127.0.0.1:8127` (Health-Flow report portal)
- review: `127.0.0.1:8126` (paper review workbench)

The retired `*.ranlei.work` domains are gone; port numbers and exposure for the
current deployment live in `docs/deployment.md`, not here.

Create private `var/portal.env` and `var/review.env` from the examples, sync the
canonical environment, then install the three unit files into
`~/.config/systemd/user/`. The review process temporarily accepts the legacy
`GENESIS_REVIEW_API_KEY` when the new key is empty; set a dedicated new key to
remove that compatibility dependency. Set `GENESIS_EVIDENCE_REVIEWER_ID` to the
one authenticated human reviewer; audit identities are derived from this server-side value.

Each unit reads only this project's own `var/*.env`. An earlier revision also
loaded the frozen `genesis-health` project's env file as a compatibility
fallback; that dependency was removed on 2026-09-24, because a unit that reads
a project slated for archive will silently lose its environment the day that
project moves. If a value is needed, it belongs in this project's `var/*.env`.
The worker shares `var/review.env`, requires `PAPER_AI_API_KEY_FILE` or
`PAPER_AI_API_KEY`, and processes one persisted paper-extraction job at a time.
**It is currently `disabled` and `inactive`**: extraction is paused for
single-topic low-speed acceptance, so it claims no jobs. Completed data is kept
and failed jobs remain visible in the review workbench, where they require an
explicit retry. Re-enable it only for a deliberate, monitored batch.
The key file may be a two-column CSV containing an `apiKey` row. Keep it outside
the repository and private to the development account. Legacy `ARK_*` variables
remain supported. `PAPER_AI_TIMEOUT_SECONDS` bounds one streamed provider call;
set it for the configured model's observed long-document latency.

Never commit either private environment file. `var/portal.env` supplies
`OPENAI_API_KEY`, which the Evidence API needs for report extraction; the review
and worker units read `PAPER_AI_*` from `var/review.env`.
The public upload endpoint defaults to a global 10 requests/hour budget and
two concurrent model calls; tune only after observing real capacity.
