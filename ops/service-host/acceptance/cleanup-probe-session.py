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
import os
import pathlib
import sqlite3
import sys

DB = "/opt/health-flow/var/healthflow.db"


def connection_db_file(path: pathlib.Path) -> str | None:
    """Return the identity SQLite reports for a database, or None if unknown.

    `PRAGMA database_list` reports the file already opened by this connection,
    so this reads the identity of the database actually in use rather than
    re-resolving a path — which is what makes it usable to tell "the wrong file
    was opened" apart from "the right file has no such row".
    """
    try:
        connection = sqlite3.connect(path)
        row = connection.execute("PRAGMA database_list").fetchone()
        connection.close()
    except sqlite3.Error:
        return None
    return os.path.abspath(row[2]) if row and row[2] else None


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/e2e-state.json")
    if not path.is_file():
        print(f"no session file at {path}; nothing to remove")
        return 0
    data = json.loads(path.read_text())
    # Remove from the database the session was minted in, not a fixed path.
    # Session ids are local to a database; deleting by id against a different
    # one can remove an unrelated patient's session instead of the probe's.
    db = data.get("db_path") or os.environ.get("HEALTHFLOW_DB") or DB
    connection = sqlite3.connect(db)
    # Match on the token hash as well as the id: the state file names both, so
    # a stale or hand-edited state cannot delete a session it does not describe.
    token_hash = data.get("token_hash")
    if token_hash:
        removed = connection.execute(
            "DELETE FROM user_sessions WHERE id=? AND token_hash=?",
            (data["session_id"], token_hash),
        ).rowcount
    else:
        removed = connection.execute(
            "DELETE FROM user_sessions WHERE id=?", (data["session_id"],)
        ).rowcount
    connection.commit()
    remaining = connection.execute("SELECT count(*) FROM user_sessions").fetchone()[0]
    accounts = connection.execute("SELECT count(*) FROM user_accounts").fetchone()[0]
    connection.close()

    if removed == 0:
        # Nothing matched. Two very different situations look identical from
        # here, and only one of them is safe to call done:
        #
        #   - this is not the database the session was minted in (DB_FILE
        #     differs from the recorded one), so the probe session is still
        #     live somewhere and the state file is the only record of it;
        #   - it is the right database and the row is simply gone — expired,
        #     revoked, or already cleaned.
        #
        # DB_FILE is what the connection actually opened, so it distinguishes
        # "wrong file" from "right file, no row" without guessing.
        opened = connection_db_file(pathlib.Path(db))
        if opened and data.get("db_file") and opened != data["db_file"]:
            print(
                f"state was minted in {data['db_file']} but {db} opened as {opened}; "
                f"state file kept at {path}",
                file=sys.stderr,
            )
            return 1
        if token_hash and data.get("db_file") and opened == data["db_file"]:
            # Right database, row absent: the probe session is already gone.
            path.unlink()
            print("probe session already absent; state file removed")
            print(f"sessions remaining: {remaining} | accounts: {accounts}")
            return 0
        print(
            f"nothing removed from {db} and the database identity is unknown; "
            f"state file kept at {path}",
            file=sys.stderr,
        )
        return 1

    path.unlink()
    print(f"removed probe session(s): {removed}")
    print(f"sessions remaining: {remaining} | accounts: {accounts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
