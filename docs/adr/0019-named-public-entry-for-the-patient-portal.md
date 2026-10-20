# 19. The patient portal gets a named entry; TLS terminates on the host's web server

Date: 2026-10-20
Status: Accepted

## Context

The patient portal has been reached as `http://<address>:10007` — a bare IP and
port. That single fact produces every symptom this change answers, and they are
not independent problems with separate fixes:

- **The session cookie cannot carry `Secure`.** An IP address cannot sign a
  certificate, and a `Secure` cookie is refused by the browser over `http://`, so
  the session token — and the reviewer's bearer key — cross the network in the
  clear. Turning `Secure` on without TLS does not harden anything; it breaks
  login silently.
- **The application is the entry.** To be reachable at all the portal binds
  `0.0.0.0`, so every application change is also an exposure change, and the
  host's web server can never be the gate it is supposed to be.
- **The entry cannot be observed.** Without a hostname there is no
  distinguishable access log and no Host-based boundary, so "how many people use
  this entry" has no answer — and observed traffic is precisely what should
  decide whether an entry keeps existing.

The review workbench's public exposure was withdrawn on that basis on
2026-09-28 (zero external sources over two weeks). This entry gives a name to
the portal **only**; it does not reopen the workbench.

## Decision

**One public entry, named, with TLS terminated on the host's web server.**

- `health.ranlei.work` is the transition hostname. `server_name` is that single
  name — no `www.`, no IPv6 listener, because DNS carries one `A` record and a
  second listener would be configuration nobody exercises.
- Port 80 does two things and nothing else: ACME validation and a `301` to
  HTTPS. It is **not** a site root.
- Port 443 proxies to the portal's loopback port. The portal binds `127.0.0.1`,
  so the only external entry is this one server block.
- The entry owns its `access_log`, with a format that uses **`$uri`**. The mall's
  entry page arrives as `/?ticket=…`, and that ticket is a credential; `$uri`
  records `/` and never the query string.
- **The certificate comes from Let's Encrypt**, ECDSA, webroot — the same shape
  as the neighbouring TLS site on the machine. Renewal copies the certificate
  into the host's per-site certificate directory and reloads nginx from a
  **per-lineage deploy hook**, not a global one.
- `X-Forwarded-For` is **overwritten** with `$remote_addr`, not appended. Nothing
  reads the client IP today; the point is that appending leaves a client-supplied
  value at the head of the chain, and the first IP-based rule anyone writes would
  read a forged address. The application trusts proxy headers from a loopback
  peer, and this request is from a loopback peer.
- HSTS is `max-age=31536000` with **no** `includeSubDomains` and **no**
  `preload`: this deployment speaks for one name, and preload is effectively
  irreversible.
- A certificate-expiry gate fails when any certificate this deployment answers
  for has 21 days or less left. It is read-only, it is fed a failing sample in
  its own self-check, and until there is an alerting channel its only signal is
  the unit entering `failed` and appearing in `systemctl --failed`.

**What this does not do**: it does not give the review workbench a public name,
does not expose the evidence API, does not add CSP or other security headers
(beyond HSTS), and does not migrate the domain off personal assets. Those are
separate decisions, and one of them is a ticket that names the dependency chain.

The name is a **transition**, not the target. `health.ranlei.work` depends on a
personal registrar, a personal Cloudflare account, and renewal automation the
operator controls; a health-report portal's availability should not depend on
one person's domain renewal. The migration to a company domain is `blocked_by`
this work and is recorded as its own ticket rather than assumed to follow.

## Alternatives Considered

Ordered by how strong the rejected option's case is.

**1. Cloudflare proxy in front of the origin, with a Cloudflare Origin
certificate (the strongest rejected option).** This is what the previous
`*.ranlei.work` entry did, and it is attractive: Cloudflare terminates TLS, the
origin certificate is free and long-lived, and no ACME plumbing is needed. It
fails on a measured property of this zone — its SSL mode is **`flexible`**.
Behind a proxy in flexible mode the browser leg is TLS and the Cloudflare →
origin leg is **plaintext**, while the page looks completely normal. That is the
worst kind of green: the patient sees a padlock, the session cookie is flagged
`Secure`, and the token still crosses the network in the clear on the last hop.
DNS-only with an Origin certificate fails differently — no public CA signs it, so
browsers do not trust it. Switching the zone to Full (strict) would fix both, but
a zone-level setting is shared with every other site under `ranlei.work`, so it
is not this ticket's to change. A self-consistent public-CA certificate with
DNS-only chosen over a zone-wide change is the decision.

**2. Serve the portal under the mall's domain as a path.** This would give the
patient the mall's own trusted name and no new certificate. It is not available:
the mall gateway owns that hostname and its prefix, and a measured request to
that path returns the gateway's default error page rather than reaching an
upstream. Making it work means changing the mall's routing, which is a different
owner's system and a different change.

**3. Keep `IP:port` and harden only the application layer.** This is the
smallest change and it is what the deployment has been doing. It cannot work:
`Secure` cannot be enabled over plain HTTP without breaking login, the entry
cannot be observed without a hostname, and the application must keep binding
`0.0.0.0` — so the three symptoms above stay, and each application change stays
an exposure change. It is the state being retired, not an alternative to it.

## Rationale

The choice is driven by which options are **self-consistent about the last hop**.
A certificate strategy is only meaningful if the leg it protects is the leg that
carries the token; a scheme that terminates TLS somewhere else and then speaks
plaintext to the origin does not harden anything, it only relocates the exposure
and hides it behind a padlock. A public-CA certificate on a DNS-only record makes
every hop match, and it changes no shared setting.

The two boundaries that stay where they are — the workbench's withdrawn exposure
and the evidence API's internal edge — are kept because observed traffic, not
convenience, decided them; and an entry change is exactly the change that must
not widen them as a side effect.

HSTS without `preload` and without `includeSubDomains` follows the same rule as
the single `server_name`: assert authority over exactly the name this deployment
serves. A browser-vendor preload submission is effectively permanent, and it
should not be made by a change whose own name is a transition.

The expiry gate is included here rather than treated as monitoring work because
the alternative is not "no monitoring", it is **silent non-renewal**: renewal is
already automatic twice a day, and nothing on the machine fails when it stops.
21 days is chosen because the renewal window opens at 30 days — by day 21 there
have been roughly 18 failures, which rules out a transient, with time left for a
human. A 14-day threshold turns "renewal broke" into next week's outage.
