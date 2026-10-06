---
name: verify-genesis-evidence
description: "Drive the genesis-evidence review workbench and read-only evidence API against an isolated throwaway database, and capture evidence that a change works. Use when you need to prove review-workbench or evidence-API behavior on genesis-evidence rather than assume it works from unit tests."
---

# Verify genesis-evidence

Drive the **real** genesis-evidence services against an **isolated throwaway
database** and capture evidence. This skill wraps harnesses the repo already has
(`tests/`, `scripts/check_review_workbench_layout.py`) rather than introducing a
parallel runtime.

Grounded against commit `a86edb9` on 2026-10-06.

> **Never point a verification run at `var/genesis-evidence.sqlite3`.** That is the
> working database (~49 MB). Every recipe below uses a fresh temp database. If you
> find yourself about to edit `var/*.env`, stop — the helper takes the paths as
> environment overrides instead.

## Surfaces

| Surface | Entry | Port |
|---|---|---|
| review workbench (paper review UI) | `genesis-evidence-review` | 8126 |
| evidence API (read-only, published evidence) | `genesis-evidence-api` | 8125 |
| health-flow report portal | — | 8127 — **not this repo**, see health-flow's `verify-healthflow` |

All three bind loopback. 8125 is a deliberate internal edge for health-flow, never
a user domain.

## Prerequisites — check these first, they fail loudly

```bash
cd <repo>
ls var/review.env var/portal.env    # missing -> copy from the canonical checkout
ls src/genesis_evidence             # missing -> wrong directory
command -v google-chrome            # missing -> no screenshots (DOM assertions still work)
```

**`var/*.env` is gitignored, so a fresh worktree or clone does not have it.** The
service refuses to start without it, and the failure looks like a startup error
rather than a missing prerequisite. Copy it from the canonical checkout:

```bash
cp /home/claude/Projects/genesis-evidence/var/review.env var/review.env
cp /home/claude/Projects/genesis-evidence/var/portal.env var/portal.env
```

It holds the review API key and the model endpoints. Never commit it. For a new
environment, start from `ops/examples/*.env.example`.

## Launch

Use the helper. It creates a temp sandbox (database + object store), starts the
service against it, and tears it down:

```bash
cd <repo>
uv run python .claude/skills/verify-genesis-evidence/scripts/observe.py --help
uv run python .claude/skills/verify-genesis-evidence/scripts/observe.py \
    --service review --out var/verify-evidence
```

Manual equivalent (if you need to poke at it interactively):

```bash
cd <repo>
T=$(mktemp -d)
set -a; source var/review.env; set +a          # real key, real model endpoints
export GENESIS_EVIDENCE_DATABASE="$T/evidence.sqlite3"   # ← throwaway
export GENESIS_EVIDENCE_OBJECTS="$T/objects"
PYTHONPATH=src uv run genesis-evidence-review
```

`GENESIS_EVIDENCE_REVIEW_PORT` (default 8126) selects the port;
`GENESIS_EVIDENCE_REVIEW_HOST` the bind address. Both come from `var/review.env`.

Teardown: kill the process you started. **Never kill by process name** — the same
uvicorn may be running as a systemd unit for another purpose.

## Doctor

Read-only; run before the first drive and after any failure.

```bash
curl -s -m 3 -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${PORT:-8126}/health"
#   200 -> service up. 000 -> nothing listening.

curl -s -m 3 -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $GENESIS_EVIDENCE_REVIEW_API_KEY" \
  "http://127.0.0.1:${PORT:-8126}/api/review/me"
#   200 -> the key you are using is the key the service expects.
#   401 -> key mismatch. This is the single most common false "the app is broken".
```

`/health` is unauthenticated; every `/api/review/*` route requires
`Authorization: Bearer <GENESIS_EVIDENCE_REVIEW_API_KEY>`. **`/health` answering 200
says nothing about auth** — always run the second probe.

## Drive

### Review workbench (browser)

The page serves unauthenticated at `/`, but its API calls need the key. The page
reads it from a `#key` input, persists it to `sessionStorage['reviewKey']`, and only
becomes useful after clicking `连接` (`#connect`).

`chrome --headless` cannot type or click, so drive it the way the repo's own harness
does — inject a script into a copy of the page:

```bash
google-chrome --headless=new --no-sandbox --disable-gpu \
  --window-size=1440,900 --virtual-time-budget=6000 \
  --screenshot=shot.png "http://127.0.0.1:8126/"
```

To pre-authenticate, inject before the app script:

```html
<script>
  sessionStorage.setItem('reviewKey', 'REVIEW_API_KEY');
  addEventListener('DOMContentLoaded', () => document.getElementById('connect').click());
</script>
```

**A screenshot of the login prompt is not evidence a feature works.** Seed the key,
click 连接, then assert the authenticated view actually rendered (see `features/`).

### Evidence API (HTTP)

```bash
curl -s "http://127.0.0.1:8125/health"
curl -s "http://127.0.0.1:8125/api/metrics"
```

## Evidence

Screenshots and response bodies go to `var/verify-evidence/` (**gitignored** — copy
what you rely on somewhere the commit or PR carries it).

```bash
google-chrome --headless=new --no-sandbox --disable-gpu \
  --window-size=1440,900 --virtual-time-budget=6000 \
  --screenshot=var/verify-evidence/<feature>.png "http://127.0.0.1:8126/"
```

Proof standards:

- Exercise the **real user path** through the browser or the real endpoint. Driving
  a Python function directly, or a `TestClient`, is a unit test — not this.
- Capture the **action and the resulting state**, not only the final screen.
- Verify side effects (rows written, files in the object store) alongside the view.
- The repo's `scripts/check_review_workbench_layout.py` establishes the house
  technique: rewrite a copy of the page with a probe `<script>`, render it headless,
  read the JSON it leaves in the DOM. Reuse that shape for state assertions instead
  of eyeballing a screenshot.

## Cleanup

- Kill the process you started; remove the temp sandbox directory.
- **Never kill by process name** — the same service name may be a systemd unit.
- The throwaway database is the point: `rm -rf` the temp dir and the working
  `var/genesis-evidence.sqlite3` is untouched.
- Cleanup removes instances and scratch state, **never the evidence** — after
  teardown confirm the files under `var/verify-evidence/` still exist.

## Helpers

| Helper | Invocation | Purpose |
|---|---|---|
| `scripts/observe.py` | `uv run python .claude/skills/verify-genesis-evidence/scripts/observe.py --service review --out var/verify-evidence` | Isolated sandbox + launch + doctor + screenshot + teardown |
| `scripts/check_review_workbench_layout.py` (repo, pre-existing) | `uv run python scripts/check_review_workbench_layout.py` | Headless-Chrome layout probe; the pattern this skill follows |
| `tests/test_review_api.py` (repo, pre-existing) | `uv run pytest tests/test_review_api.py` | Builds isolated DBs via `create_app(database_path=tmp_path/...)` |

## Known limits

- **The review workbench has no seeded fixtures of its own.** An empty throwaway DB
  renders empty queues and an empty coverage matrix — that is a *correct* render of
  an empty database, not a verified feature. Proving admission/claim/card flows
  needs data; the repo's `tests/test_review_api.py` shows how to create it through
  the API.
- `/api/review/*` reads and writes real review state. Point it at an isolated DB
  **always**; the helper does this for you.
- The paper-extraction path calls an external model (`PAPER_AI_*`). Those calls are
  a production boundary — don't fake them, and don't claim the extraction chain is
  verified when you substituted its output.
- `--virtual-time-budget` makes the page render fast-forwarded; a feature that needs
  real network round-trips may screenshot before it settles. Prefer DOM assertions
  (`--dump-dom`) over screenshots for state, screenshots for layout.
