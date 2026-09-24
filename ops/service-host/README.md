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

> **Current state.** Two of the three listeners are still on the interim
> public-IP entry while a company subdomain is arranged: `10007` (patient
> portal) binds `0.0.0.0` and serves real patients; `10005` and `10006` bind
> loopback. There is **no TLS**, and `AUTH_COOKIE_SECURE` is set to `false` so a
> browser will store the session cookie over plain HTTP. Those facts travel
> together: restoring the cookie flag without TLS breaks login, and leaving it
> unset with TLS leaves credentials in the clear. What must change when the
> subdomain and certificate exist is recorded in the project's issue tracker,
> not only here.

### Exposure of `10006` was withdrawn on observed traffic (2026-09-24)

`10006` is the paper review workbench — an internal tool with a single
server-side reviewer identity and no public use case. It had been binding
`0.0.0.0` alongside the patient portal. Before withdrawing that binding, the
service's own access log was read for the preceding two weeks and the client
addresses classified:

| Listener | Requests from the platform's own egress | Requests from any other source |
| --- | --- | --- |
| `10006` review workbench | 70 | **0** |
| `10007` patient portal | 107 | **at least six distinct external addresses** |

Every apparent "external" hit on `10006` resolved to the platform's own egress
address, which is also the address the acceptance harness runs from. With no
observed external use, the binding was returned to loopback, and the operator
recorded it in the private operations inventory.

This is the rule the policy states, applied rather than assumed: an exposure is
closed on **observed traffic**, not on "nothing is using it". The patient
portal is the counter-example in the same measurement — it has real external
clients, so its entry stays until TLS replaces it.

Any future remote access to `10006` uses a separate channel (SSH tunnel or the
private plane) and does not restore a public binding.

### Target state: loopback behind the host's web server

Both services bind loopback only. The public entry point is terminated in front
of them by the host's web server; the services themselves are never reached by
their port from outside the host.

`nginx/genesis-evidence.conf` is that entry: one server block per domain,
proxying to the loopback port of the matching service. It is deployed into the
host's vhost directory and, like the neighbouring internal entry there, does not
modify a panel-managed site.

It was **removed from the host** during the retirement above, and the file is
kept here as the form to restore. Reinstating it means changing the domain names
in it — the previous ones are retired — and pointing it at the ports in use.

TLS terminates there with the **Cloudflare Origin certificate** for
`*.ranlei.work`, matching the existing entry path (Cloudflare → origin). The
certificate a browser sees is Cloudflare's; this one only has to satisfy
Cloudflare in Full (strict) mode. Plain HTTP **redirects** to HTTPS rather than
serving, because patient sessions and the reviewer's bearer key must not cross
the network in the clear.

That requirement is not hypothetical: it is precisely what the interim state
above violates, and the reason the interim state is bounded.

The certificate and its private key are installed under the panel's per-site
certificate directory — mode `700` on the directory, `600` on the key. They are
**not** in this repository, and are delivered by a private channel.

The report portal's block raises `client_max_body_size` to 55m because a single
submission may carry two large report files.

### The default-server trap

nginx makes the **first** server block on a port the default for unmatched names,
and file order is alphabetical. A new file that sorts before the others will take
over as the default for that port — so an unrelated host reaching it lands on
whoever wrote last, silently.

That happened here: the first version of this file, listening on 443 without a
catch-all, became the HTTPS default and served our application to any hostname.
The fix is an explicit `default_server` block that returns `444`, so "no match"
says no match instead of answering as us. Any file added to this directory
should carry one for this reason.

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
