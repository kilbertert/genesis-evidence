#!/usr/bin/env python3
"""Remove a probe session minted by mint-probe-session.py.

Acceptance mints a short-lived session for an existing account so it can make
authenticated requests without knowing a user's password. That session must be
removed afterwards — leaving it behind means a live credential for a real
account exists with nobody accountable for it.

    cleanup-probe-session.py [state-file]     # default /tmp/e2e-state.json
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

DB = "/opt/health-flow/var/healthflow.db"


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/e2e-state.json")
    if not path.is_file():
        print(f"no session file at {path}; nothing to remove")
        return 0
    data = json.loads(path.read_text())
    connection = sqlite3.connect(DB)
    removed = connection.execute(
        "DELETE FROM user_sessions WHERE id=?", (data["session_id"],)
    ).rowcount
    connection.commit()
    remaining = connection.execute("SELECT count(*) FROM user_sessions").fetchone()[0]
    accounts = connection.execute("SELECT count(*) FROM user_accounts").fetchone()[0]
    connection.close()
    path.unlink()
    print(f"removed probe session(s): {removed}")
    print(f"sessions remaining: {remaining} | accounts: {accounts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
