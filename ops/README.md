# User service deployment

The new project runs beside the frozen `genesis-health` services:

- portal: `127.0.0.1:8125` → `genesis-evidence.ranlei.work`
- review: `127.0.0.1:8126` → `genesis-evidence-review.ranlei.work`

Create private `var/portal.env` and `var/review.env` from the examples, sync the
canonical environment, then symlink the four unit files into
`~/.config/systemd/user/`. The review process temporarily accepts the legacy
`GENESIS_REVIEW_API_KEY` when the new key is empty; set a dedicated new key to
remove that compatibility dependency. Set `GENESIS_EVIDENCE_REVIEWER_ID` to the
one authenticated human reviewer; audit identities are derived from this server-side value.
The worker shares `var/review.env`, requires `ARK_API_KEY`, and processes one
persisted paper-extraction job at a time. Failed jobs remain visible in the
review workbench and require an explicit retry.

Never commit either private environment file. The report upload path is not
ready for a live model canary until `OPENAI_API_KEY` is configured.
The public upload endpoint defaults to a global 10 requests/hour budget and
two concurrent model calls; tune only after observing real capacity.
