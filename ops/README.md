# User service deployment

The new project runs beside the frozen `genesis-health` services:

- portal: `127.0.0.1:8100` → `genesis-evidence.ranlei.work`
- review: `127.0.0.1:8101` → `genesis-evidence-review.ranlei.work`

Create private `var/portal.env` and `var/review.env` from the examples, sync the
canonical environment, then symlink the three unit files into
`~/.config/systemd/user/`. The review process temporarily accepts the legacy
`GENESIS_REVIEW_API_KEY` when the new key is empty; set a dedicated new key to
remove that compatibility dependency.

Never commit either private environment file. The report upload path is not
ready for a live model canary until `OPENAI_API_KEY` is configured.
The public upload endpoint defaults to a global 10 requests/hour budget and
two concurrent model calls; tune only after observing real capacity.
