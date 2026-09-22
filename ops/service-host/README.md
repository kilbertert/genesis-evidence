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

Both services bind loopback only. The public entry point is terminated in front
of them by the host's web server; the services themselves are never reached by
their port from outside the host.

`nginx/genesis-evidence.conf` is that entry: one server block per domain,
proxying to the loopback port of the matching service. It is deployed into the
host's vhost directory and, like the neighbouring internal entry there, does not
modify a panel-managed site.

The report portal's block raises `client_max_body_size` to 55m because a single
submission may carry two large report files.

### Verifying the entry

```
curl -H 'Host: <domain>' http://<host-address>/          # expect the real app
curl -H 'Host: other.example.com' http://<host-address>/ # expect NOT our app
```

The Host-header probe is the verification method: it reaches the entry as the
public traffic would, without moving DNS, so the path can be proven before any
traffic is switched. Two things to be careful about:

- **Check content, not just the status code.** A `200` may be a default site or a
  soft error page; only the response body shows which application answered.
- **Use a Host header other than the real one to confirm closure.** If an
  arbitrary host also reaches the app, the entry is not scoped.

### Reloading

Two nginx processes may exist on a host like this: the panel-managed one that
owns the public ports, and another inside a container that owns nothing public.
Reload the one that actually owns the ports — signalling the wrong one silently
changes nothing, and the entry keeps serving the previous configuration.

Also note that the panel's nginx is not necessarily supervised by
`systemctl`: on this host the LSB unit is in a failed state while nginx runs
fine, so `systemctl reload nginx` is the wrong command and reports an error that
has nothing to do with the configuration.
