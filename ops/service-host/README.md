# genesis-evidence: service host

This directory holds the deployment assets for running genesis-evidence on a
**service host** — a machine that serves real users, as opposed to the
development host.

They are separate from `ops/systemd/`, which holds the development host's
`systemctl --user` units. The two forms are deliberately not shared: a service
host has no interactive account to own a user manager, so its units are system
units running under a project-scoped service identity.

## Service identity

Each project gets **one** system identity, not one per process — two processes
of the same project share it, and two different projects never do. The identity
has no login shell, no password, no sudo, and no group besides its own. That
last part is what makes it worth having: a shared group would let one project's
defect read another project's credential files with no privilege escalation.

## Layout

The paths below are the convention this project declares in `.dev-host.toml`.
The host registry holds the machine-specific half.

| Path | Holds |
| --- | --- |
| `/opt/genesis-evidence` | project root, owned by the service identity |
| `/opt/genesis-evidence/artifacts` | the built wheel that was deployed |
| `/opt/genesis-evidence/var` | database and object store — **runtime data** |
| `/opt/genesis-evidence/ops` | operational scripts from this directory |
| `/var/log/genesis-evidence` | service logs, owned by the identity |
| `/var/backups/genesis-evidence` | host-side backups |

## Installation

The wheel is built from a merged revision and transferred as an **artifact with
its sha256**; the write gate on a service host refuses an unidentified write, so
this is enforced rather than merely asked for.

1. Create the identity and the directories above, owned by it.
2. Build the wheel from a merged revision and record its sha256.
3. Transfer it into `artifacts/`, then install it into the project virtualenv.
4. Write the two environment files from `examples/` with mode `640`, owned by
   the identity. Generate the keys **on the host** so they never transit a
   development machine.
5. Bootstrap the empty database with `bootstrap-db.py`. It takes the database
   path from its argument, else from `GENESIS_EVIDENCE_DATABASE`, else the
   project default — so it runs standalone, without sourcing an environment
   file first:

   ```
   su -s /bin/bash -c \
     '/opt/genesis-evidence/.venv/bin/python /opt/genesis-evidence/ops/bootstrap-db.py' \
     genesis-evidence
   ```

   Invoke it through the project virtualenv's interpreter rather than the
   script's shebang: the shebang resolves to the system Python, which does not
   have the package installed.

   Do this explicitly rather than relying on one service to create it at
   startup: the review workbench creates the schema as a side effect, while the
   evidence API **fails closed** if the tables are absent, so relying on start
   ordering leaves a race between the two.
6. Install the units and `systemctl enable --now` them.

## Verification

`auth-probe.sh` **asserts** the evidence API's key boundary from the host
itself, and exits nonzero if any case does not match. Run it as the service
identity; it reads the key from the environment file.

It asserts three cases: no key → `401`, a wrong key → `401`, and the correct key
→ **not** `401`. The third is deliberately *not* "200": a fresh deployment has
an empty database, so nothing is published to match against and the correct-key
request is expected to reach the domain logic and be rejected there. Asserting
"not 401" is what distinguishes "authenticated, nothing to return" from
"rejected at the door".

Use a **well-formed** request body when probing the boundary. A malformed body
fails request validation before the handler's key check runs, so it returns 422
regardless of the key — which tests the schema, not the authentication.

## Exposure

> **Current state (live listeners, verified 2026-09-28).** `10005` and `10006`
> bind **loopback**; `10007` binds `0.0.0.0` and serves real external users, so it
> is the only exposed listener. There is **no TLS**, and `AUTH_COOKIE_SECURE` is
> set to `false` so a browser will store the session cookie over plain HTTP.
> Those two facts travel together: restoring the cookie flag without TLS breaks
> login, and leaving it unset with TLS leaves credentials in the clear. What must
> change when the subdomain and certificate exist is recorded in the project's
> issue tracker, not only here.

### `10006`: the decision to withdraw its exposure, and how the acceptance changed

`10006` is the paper review workbench — an internal tool with a single
server-side reviewer identity and no public use case. It used to bind `0.0.0.0`
alongside the patient portal; it is loopback-only now (see Current state above).
Before deciding to withdraw that binding, the service's own access log was read
for the preceding two weeks and the client addresses classified:

| Listener | Requests from the platform's own egress | Requests from any other source |
| --- | --- | --- |
| `10006` review workbench | 70 | **0** |
| `10007` patient portal | 107 | **at least six distinct external addresses** |

Every apparent "external" hit on `10006` resolved to the platform's own egress
address, which is also where the acceptance harness runs. With no observed
external use, the binding is returned to loopback. This is the rule the policy
states, applied rather than assumed: an exposure is closed on **observed
traffic**, not on "nothing is using it". The patient portal is the
counter-example in the same measurement — it has real external clients, so its
entry stays until TLS replaces it.

**The host change ran on 2026-09-28** (service-host mutation, delivered as the
revision built from `b941fd1`): `GENESIS_EVIDENCE_REVIEW_HOST=127.0.0.1` was set
in `/opt/genesis-evidence/var/review.env` and `genesis-evidence-review.service`
restarted. Verified by `ss -ltn`, which shows `127.0.0.1:10006`, and by the
negative assertion in `e2e-acceptance.sh` — `10006` is unreachable from an
external client. If this is ever reverted, revert it deliberately.

**Acceptance follows the listener.** `e2e-acceptance.sh` no longer probes
`10006` through the public address; it asserts that both internal listeners
(`10005`, `10006`) refuse from outside, and reaches the workbench over the
private channel for the authenticated checks. The negative assertion is the
one that would catch a regression — which is why it is now the acceptance step
for this surface, rather than a positive probe that only passed while the
exposure was open.

Any future remote access to `10006` uses a separate channel (SSH tunnel or the
private plane) and does not restore a public binding.

### Public entry: `health.ranlei.work`

Both internal services bind loopback only. The portal's single public entry is
terminated by the host's web server and proxies to `127.0.0.1:10007`; the
services themselves are never reached by their port from outside the host.

`nginx/health.ranlei.work.conf` is that entry — one file, one hostname, one
backend. Its decisions are recorded in ADR 0019; the ones that need a reader of
*the file* to know:

- **Port 80 does ACME and a 301, nothing else.** No whole-site `root`.
- **The access log uses `$uri`.** The mall's entry page arrives as `/?ticket=…`
  and that ticket is a credential; `$uri` never records the query string. There
  is a comment in the file saying so, so removing the property means first
  deleting a sentence with a reason.
- **`X-Forwarded-For` is overwritten, not appended.** The application trusts
  proxy headers from a loopback peer, and this request is one.
- **HSTS without `includeSubDomains` or `preload`** — this deployment speaks for
  one name.
- **No `default_server`.** The `00-default-catchall.conf` that sorts before this
  file owns it; see the trap below.

TLS terminates there with a **Let's Encrypt** certificate (ECDSA, webroot),
matching the neighbouring TLS site on the machine. `cert-deploy-hook.sh` is
registered **on this lineage only** (`--deploy-hook` at issuance, recorded in
`/etc/letsencrypt/renewal/health.ranlei.work.conf`): a hook in the global
`renewal-hooks/deploy/` directory would copy our files and reload the server on
every other site's renewal. It copies the certificate into the panel's per-site
directory — nginx's worker cannot read `/etc/letsencrypt/live` — runs
`nginx -t`, and only then reloads.

**`certbot renew --dry-run` does not exercise that hook.** Measured on certbot
2.9.0: `--dry-run` skips deploy hooks. The verification that counts is running
the hook once with `RENEWED_LINEAGE` set to the live lineage.

The report portal's block raises `client_max_body_size` to 55m because a single
submission may carry two large report files.

#### Why not Cloudflare in front of the origin

The zone's SSL mode is `flexible`. Behind a proxy in that mode the browser leg
is TLS and the **Cloudflare → origin leg is plaintext**, while the page looks
completely normal — the worst kind of green, because the patient sees a padlock
and the session token still crosses the network in the clear. Switching the zone
to Full (strict) is a zone-wide setting shared with every other site under
`ranlei.work`, so it is not this project's to change. ADR 0019 records the full
comparison.

#### The certificate-expiry gate

`cert-expiry-check.sh` fails when any certificate **this deployment answers for**
has 21 days or less left; the `systemd/cert-expiry-check.{service,timer}` pair
runs it daily. It is **read-only**. It runs as root only because the per-site
certificate directories are `700 root` — the project identity cannot open its
own certificate — and the unit has no `ReadWritePaths`.

**It judges two classes separately, because this host is shared.** The sweep
covers every site under the vhost certificate root — that is what makes the
output a ledger rather than a guess — but only the directories listed in the
script's `OWNED_DIRS` decide the exit status. Everything else is reported as
`[observed]`; a lapse there prints `EXPIRING*` and does not fail the unit.

The bare run checks the **union** of the sweep and `OWNED_DIRS`, and that is not
redundant: a gate that only looks where certificates are cannot notice that ours
is gone. A deleted site directory or a `CERT_ROOT` pointed elsewhere would leave
no row at all, and the run would pass on the strength of everyone else's healthy
certificates — the one failure this unit exists to catch would be the one it
cannot see. `--self-check` drives that exact path, in both directions.

That split is not squeamishness. Measured when this was written:
`api.qumall.qushiyun.com` was down to **10 days**, and its renewal is the
company's to fix, not ours. A unit that goes permanently red for a certificate
we cannot renew is a unit whose red stops being read — and then the one that
matters gets ignored too. Recording it is what keeps it visible without making
our status a function of someone else's directory.

**Until there is an alerting channel, `systemctl --failed` is the only signal**
for the owned class: the unit enters `failed` and stays there. That sentence is
also in the unit file, so the next reader does not believe it has notification.
An `observed` lapse has no such signal — it is in the daily output, and whoever
reads the ledger is what turns it into action.

It is proven by feeding it failing samples, not by watching it stay quiet. Both
directions of the classification are asserted, so the split cannot be inert:

```bash
/opt/genesis-evidence/ops/cert-expiry-check.sh --self-check
```

### The default-server trap

nginx makes the **first** server block on a port the default for unmatched names,
and file order is alphabetical. A new file that sorts before the others will take
over as the default for that port — so an unrelated host reaching it lands on
whoever wrote last, silently.

That happened here: the first version of this file, listening on 443 without a
catch-all, became the HTTPS default and served our application to any hostname.
The fix is an explicit `default_server` block that returns `444`, so "no match"
says no match instead of answering as us. That block now lives in
`00-default-catchall.conf`, which sorts before every site file; a site file must
**not** carry `default_server` of its own.

### Verifying the entry

```
# real domain: expect the actual application
curl -sk --resolve <domain>:443:<host-address> https://<domain>/ | grep '<title>'

# unrelated host: expect NOT our application
curl -sk --resolve other.example.com:443:<host-address> https://other.example.com/
```

The Host-header / SNI probe is the verification method: it reaches the entry as
the public traffic would, without moving DNS, so the path can be proven before
any traffic is switched. Three things to be careful about:

- **Check content, not just the status code.** A `200` may be a default site or a
  soft error page; only the response body shows which application answered.
- **Probe with an unrelated host too, over the same protocol you changed.** An
  HTTP-only closure check will miss an HTTPS default that answers everything —
  that is exactly how the trap above survived its first review.
- **Confirm the company's own sites still answer** after touching a shared
  server, by Host header, including any that listen on the same ports.

### Reloading

Two nginx processes may exist on a host like this: the panel-managed one that
owns the public ports, and another inside a container that owns nothing public.
Reload the one that actually owns the ports — signalling the wrong one silently
changes nothing, and the entry keeps serving the previous configuration.

Also note that the panel's nginx is not necessarily supervised by
`systemctl`: on this host the LSB unit is in a failed state from a date before
this work while nginx runs fine, so `systemctl reload nginx` is the wrong command
and its error has nothing to do with the configuration. Run `nginx -t` before
every reload and check `nginx -T` to see the configuration actually resolved,
rather than assuming the file on disk is the one being served.
