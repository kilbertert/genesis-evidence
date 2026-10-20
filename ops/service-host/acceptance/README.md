# Product acceptance

**`e2e-acceptance.sh` is the only acceptance script.** It takes the entry base
URL as its argument and defaults to the named HTTPS entry:

```bash
e2e-acceptance.sh                                     # https://health.ranlei.work
e2e-acceptance.sh http://<host-address>:10007         # the pre-TLS entry (rollback comparison)
INSECURE=1 e2e-acceptance.sh https://health.ranlei.work   # only for an untrusted chain
```

There used to be two. The second targeted retired `*.ranlei.work` hostnames and
could not run; keeping it "as the form to restore" meant two scripts gradually
disagreeing about the same deployment, and its review checks are now on the
private channel where they belong. It was deleted rather than left as a second
source of truth.

`e2e-acceptance.sh` runs the portal checks against the **public entry**, as an
external client, so a pass means the path a real user takes works end to end.
The review workbench no longer has a public entry — its exposure was withdrawn
on observed traffic — so its checks run over the private channel instead, and
the suite asserts that both internal listeners refuse from outside. That
negative assertion is what would catch a regression in the exposure.

`e2e-acceptance.sh` needs the review bearer and the probe state, both of which
live on the host. Rather than have the operator assemble them by hand, it reads
them through the project's host tool (`dev-host`), so the three commands compose
as written. It must therefore run where that tool and the project's
`.dev-host.toml` are available.

`e2e-acceptance.sh` targets the live entry directly and additionally proves the
**migrated data** is usable: it fetches an existing user's report and one of its
page files. That requires an authenticated session, which is obtained without
knowing any user's password.

`mint-probe-session.py` reads the health-flow database directly, so it must point
at **the same database the running service loads** — otherwise it mints a session
for rows nothing serves, and the run passes for the wrong reason. Its default
targets the service host's layout; set `HEALTHFLOW_DB` when auditing anywhere
else. The development host is the case that differs: its service loads a
project-local database while the default names the service host's path.

```bash
mint-probe-session.py        # on the host: writes /tmp/e2e-state.json (mode 0600)
#   ... run e2e-acceptance.sh ...
cleanup-probe-session.py     # on the host: removes that session
```

**Always run the cleanup.** The minted session is a live credential for a real
account; leaving it behind means it exists with nobody accountable for it.

While probing, note that `curl -b <file>` expects Netscape cookie-jar format. A
plain `name=value` file sends **nothing**, silently — which presents as an
authentication failure rather than a test bug.

## What it asserts

| Group | Assertion |
| --- | --- |
| A | The entry serves the real application, not a default page; an unrelated Host does not reach it |
| B | Protected routes are refused **without** a session |
| C | The metric catalogue is served through the entry |
| D | The review API refuses a missing bearer and a wrong bearer, and accepts the real one — over the private channel, since the workbench has no public entry |
| E | The entry's access log exists and contains no `ticket=` (the query string is a credential) |
| F | The review queue is readable through the entry — an entry-level smoke check only |
| G | The conditions catalogue is served |

`e2e-acceptance.sh` additionally asserts, against the live entry: the migrated
paper corpus is readable; the evidence API is **not** reachable from outside;
and an existing user's report and page file are retrievable.

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

## Security limits of this suite (read before running)

Two of these are inherent to the current deployment, not to the scripts:

- **The entry base URL decides whether the minted cookie crosses in the clear.**
  With the default (`https://health.ranlei.work`) it does not. Pointing the suite
  at the pre-TLS `http://<host-address>:10007` entry — which is only for a
  rollback comparison — puts the session cookie on the network unencrypted, so
  treat that cookie as exposed to any path that can observe it.
- **The review bearer does not cross the network at all.** `e2e-acceptance.sh`
  reads it and uses it on the service host, over loopback, so it is not subject
  to the paragraph above. Earlier revisions passed it into the script; that is
  no longer the case.
- **The state file holds a live session token.** It is written `0600` in a
  private directory, but a predictable path is still readable by root and by
  anyone who can read that file. It exists only for the duration of a run —
  mint, run, clean up — and must not be left behind.

One is a property of the scripts:

- `curl -b <file>` expects Netscape cookie-jar format; a `name=value` file sends
  nothing, silently. The suite passes the cookie as a literal string for this
  reason.

## TLS

The suite verifies the certificate chain by default, so an invalid or mismatched
certificate fails rather than passing. Pass `INSECURE=1` only for a certificate
that is legitimately not publicly trusted. This deployment's entry uses a
Let's Encrypt certificate, so the default is the correct mode; `INSECURE=1`
exists for the rollback comparison against a host that has no valid chain.

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
