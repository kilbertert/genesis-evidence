# Product acceptance

Runs the acceptance scenarios against the **public entry**, as an external
client. Nothing is probed on loopback or from the host itself, so a pass means
the path a real user takes works end to end: DNS name, TLS, proxy, service.

```bash
acceptance.sh <host-address> <review-bearer-key>
```

The host address is resolved per-request with `curl --resolve`, so the check
runs **before** DNS points at the host and without moving any live traffic. The
reviewer key is an argument rather than a file so the script carries no
credential.

It exits nonzero if any case fails and prints `SUMMARY pass=<n> fail=<n>`. `curl --resolve`
bypasses DNS by design, so a pass validates TLS and routing to the given
address, not the public DNS record — DNS is checked separately at cutover.

## What it asserts

| Group | Assertion |
| --- | --- |
| A | Both entries serve their real application, not a default page |
| B | Protected routes and upload are refused **without** a session; registration yields a session; the session then grants access |
| C | The metric catalogue is served through the entry |
| D | The review API refuses a missing bearer and a wrong bearer, and accepts the real one |
| E | An upload returns `202 processing` and its extraction job is **persisted as queued** — with the worker disabled, which is what proves the queue is durable rather than parsed inline |
| F | The review queue is readable through the entry — an entry-level smoke check only |
| G | The conditions catalogue is served |

The negative cases are deliberate. A suite that only asserts the happy path
passes against a deployment that let anonymous callers through; groups B and D
exist so that cannot happen. Verify the suite itself by running it with an
intentionally wrong bearer key — it must report failures and exit nonzero.

### What this suite does not cover

**The patient-facing evidence chain is asserted elsewhere, on purpose.** Group F
reads the review queue, which is served regardless of whether any knowledge card
references those papers and regardless of whether the cards are still published.
It would pass while every card were withdrawn. That is a real limit of what a
public-entry check can prove here, because the evidence API is deliberately not
exposed publicly — it is an internal edge that only HealthFlow calls.

The chain itself is asserted by `match-probe.sh`, which runs **on the service
host**: it submits a confirmed abnormal metric, and requires that the response
carries a finding with a card whose status is `published` and which is traceable
to a real paper. Run it as the service identity:

```bash
su -s /bin/bash -c '/opt/genesis-evidence/ops/match-probe.sh' genesis-evidence
```

## TLS

The suite verifies the certificate chain by default, so an invalid or mismatched
certificate fails rather than passing. Pass `INSECURE=1` only when the
certificate is legitimately not publicly trusted — a Cloudflare Origin
certificate is trusted by Cloudflare, not by browsers, so reaching the origin
directly requires it. Run from the public internet through Cloudflare and the
chain verifies normally.

This is a visible per-run opt-out rather than a blanket `-k`, so a certificate
problem is not silently tolerated.

## Seeding published evidence

A new tenant has no published cards, so the match path returns
`no_published_knowledge_card` and the main chain cannot be demonstrated. That is
correct behaviour for an empty tenant, not a defect.

`export-seed.py` and `load-seed.py` seed a **narrow, real subset**: published
cards for one condition and two metric scopes, with the foreign-key closure they
need. It is a curated subset for testing, **not** a migration — the target stays
a new tenant with test-seeded content rather than a copy of production.

```bash
export-seed.py            # writes a JSON subset from a source database
load-seed.py <seed.json> [target-database]
```

The loader is idempotent (rows are keyed by primary key and replaced) and binds
values as parameters, so text containing quotes, newlines, or non-ASCII survives
intact. Records and uploaded files produced by acceptance runs are test residue
and should be cleared afterwards so the tenant does not accumulate fake data.
