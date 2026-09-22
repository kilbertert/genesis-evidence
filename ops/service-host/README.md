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
5. Bootstrap the empty database with `bootstrap-db.py`. Do this explicitly
   rather than relying on one service to create it at startup: the review
   workbench creates the schema as a side effect, while the evidence API
   **fails closed** if the tables are absent, so relying on start ordering
   leaves a race between the two.
6. Install the units and `systemctl enable --now` them.

## Verification

`auth-probe.sh` checks the evidence API's key boundary from the host itself.
Run it as the service identity; it reads the key from the environment file.

It exercises three cases — no key, a wrong key, and the correct key. The third
is expected to return a **4xx business rejection**, not 200: a fresh deployment
has an empty database, so there is nothing published to match against. What the
correct-key case proves is that authentication *passed* and the request reached
the domain logic.

Use a **well-formed** request body when probing the boundary. A malformed body
fails request validation before the handler's key check runs, so it returns 422
regardless of the key — which tests the schema, not the authentication.

## Exposure

Both services bind loopback only. The public entry point is terminated in front
of them by the host's web server; the services themselves are never reached by
their port from outside the host.
