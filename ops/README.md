# User service deployment

The new project runs beside the frozen `genesis-health` services:

- evidence API: `127.0.0.1:8125` → read-only published-evidence API; deliberately
  not exposed as a user domain
- user portal: `127.0.0.1:8127` → `genesis-evidence.ranlei.work` (Health-Flow
  report portal)
- review: `127.0.0.1:8126` → `genesis-evidence-review.ranlei.work`

Create private `var/portal.env` and `var/review.env` from the examples, sync the
canonical environment, then install the four unit files into
`~/.config/systemd/user/`. The review process temporarily accepts the legacy
`GENESIS_REVIEW_API_KEY` when the new key is empty; set a dedicated new key to
remove that compatibility dependency. Set `GENESIS_EVIDENCE_REVIEWER_ID` to the
one authenticated human reviewer; audit identities are derived from this server-side value.
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
