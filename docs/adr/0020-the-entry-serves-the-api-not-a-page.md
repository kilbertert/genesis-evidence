# 20. The named entry serves health-flow's API, not a patient page

Date: 2026-10-27
Status: Accepted

## Context

ADR 0019 gave the patient portal a named entry and moved TLS to the host's web
server. Its decision carried two things at once, and they are now separable:

- **The transport**: one named entry, TLS terminated on the host's web server,
  port 80 doing only ACME and a redirect, HSTS without `preload`, an own access
  log that records `$uri` (the mall's entry page arrives as `/?ticket=…`, and a
  ticket is a credential), a per-lineage certificate deploy hook, and a
  certificate-expiry gate.
- **The payload**: the entry served the patient's React page, and that page was
  where a patient uploaded a report and read the result.

The patient UI has since been decided to live inside the mall H5
(`kilbertert/Health-Flow#266`; health-flow ADR 0026). health-flow keeps the
report API — upload, confirmation, interpretation, consent — and loses the page.
The transport half of ADR 0019 is still exactly right: the API is reached from
a browser on another origin, so it still needs a name, TLS, an observable entry,
and the same certificate discipline.

Leaving ADR 0019 as the record of what this entry is for would tell a reader
that `health.ranlei.work` serves a portal. Nothing serves a page there any more.

## Decision

**The named entry is health-flow's API entry. The transport rules of ADR 0019
are kept unchanged; only what is served behind the name changes.**

- `health.ranlei.work` remains the single named entry for health-flow, with TLS
  terminated on the host's web server exactly as ADR 0019 defines it: port 80
  for ACME and `301` only, `127.0.0.1` upstream, `X-Forwarded-For` overwritten
  rather than appended, HSTS `max-age=31536000` without `includeSubDomains` and
  without `preload`, an own access log using `$uri`, the per-lineage certificate
  deploy hook, and the 21-day expiry gate.
- The path `/` no longer has to serve the patient page. It serves the API
  surface health-flow needs; the page is the mall H5's, on the mall's own
  domains.
- **The cross-origin allowlist is a decision of its own, not of this entry.**
  It is configuration on the application (health-flow ADR 0026), changed by an
  operator as the mall's domains are confirmed, and reviewed as an exposure
  change. This record does not name the mall's origins; a public record naming
  another organization's production domains is a map, not a decision.
- **The move to a company domain stays a separate, still-open ticket.** That
  ticket was `blocked_by` the named entry and is not discharged by this one. One
  consequence changes shape: a browser reads the API's origin from the module's
  own configuration, so the mall H5 can point at a new domain with one more mall
  build, instead of the fixed API host an installed app would have carried.

## 被否掉的替代形态

**1. Keep ADR 0019 as it stands and let a reader infer the change from
health-flow's ADR.** This is the strongest alternative, because ADR 0019's
transcript of *why* the entry exists is still accurate for the API, and one
record instead of two is one fewer thing to drift. It loses on what a reader
arrives with: someone opening ADR 0019 cold is told the entry serves the
patient portal, and nothing in this repository would correct them. A record
that describes the shipped shape is the thing ADR 0019 was written to be; the
shape changed, so the record changes.

**2. Supersede ADR 0019 outright and re-state the transport rules in the new
record.** Cleaner status lines, and it removes the pointer a reader has to
follow. It loses because it copies a set of security-critical settings — HSTS
directives, the `$uri` log, the deploy hook, the expiry threshold — into a
second place, and two copies of a security setting is how one of them silently
loses `includeSubDomains` two years from now. A partial supersede keeps one
home for the transport and rewrites only the sentence that is wrong.

**3. Drop the named entry and call health-flow from a bare address again.**
Nothing to maintain; the API is internal to two cooperating services. It loses
on the same measured property ADR 0019 recorded: an entry with no name cannot
be observed, and a `Secure`-flagged credential cannot be carried over plain
HTTP to an origin that has no certificate. The browser leg is still a browser
leg, even when the page it loads belongs to another organization.

## Consequences

- **Easier**: the repository's records now agree that health-flow is an API
  service to the patient, which is what the code is. The entry's security
  posture keeps a single home, so hardening it is one edit.
- **Harder**: the cross-origin allowlist is now a runtime configuration an
  operator must keep in step with the mall, and a missing entry there fails
  closed at the browser — the module's requests are refused before they reach
  the service. A public record cannot state which origins are allowed, so the
  operator's record is the only place that answers it.
- **Capped**: `health.ranlei.work` remains a personal-domain transition until
  the company-domain ticket lands; this decision does not shorten that.
- **Unchanged**: the evidence API stays internal, is not exposed by this entry,
  and is still reached only by health-flow.
