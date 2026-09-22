#!/usr/bin/env python3
"""Mint a short-lived session for an existing account, for acceptance.

Acceptance must make authenticated requests as a real user without knowing
anyone's password. This issues one session for one account and writes **all**
the inputs the acceptance suite needs into a single state file, so the two
scripts compose without a human assembling intermediate files.

    mint-probe-session.py [state-file]     # default /tmp/e2e-state.json
    #   ... run e2e-acceptance.sh ...
    cleanup-probe-session.py [state-file]

The state file holds a live session token. It is written mode 0600 and removed
by the cleanup step; **always run cleanup**, or a working credential for a real
account exists with nobody accountable for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

DB = "/opt/health-flow/var/healthflow.db"
DEFAULT_STATE = "/tmp/e2e-state.json"
COOKIE = "healthflow_session"


def main() -> int:
    state_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STATE)

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    # Choose a real user (never test residue) who owns a report whose file has
    # at least one generated page: the suite fetches page 1, so a file with no
    # pages would fail for a reason unrelated to the migration.
    acct = c.execute("""
        SELECT a.id, a.email
        FROM user_accounts a
        JOIN medical_reports r ON r.patient_id = a.id
        JOIN report_files rf ON rf.report_id = r.id
        WHERE a.email NOT LIKE '%example%'
          AND a.email NOT LIKE '%acceptance%'
          AND rf.page_count >= 1
        GROUP BY a.id ORDER BY count(r.id) DESC LIMIT 1
    """).fetchone()
    if acct is None:
        raise SystemExit("no real account with a report file having at least one page")

    target = c.execute("""
        SELECT r.id AS report_id, rf.file_index, rf.page_count
        FROM medical_reports r JOIN report_files rf ON rf.report_id = r.id
        WHERE r.patient_id = ? AND rf.page_count >= 1
        ORDER BY r.created_at DESC LIMIT 1
    """, (acct["id"],)).fetchone()
    if target is None:
        raise SystemExit("selected account has no report with a retrievable page")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now()
    values = {
        "account_id": acct["id"],
        "token_hash": token_hash,
        "created_at": now.isoformat(sep=" "),
        "last_seen_at": now.isoformat(sep=" "),
        "expires_at": (now + timedelta(hours=1)).isoformat(sep=" "),
    }
    columns = [r[1] for r in c.execute("PRAGMA table_info(user_sessions)")]
    values = {k: v for k, v in values.items() if k in columns}
    c.execute(
        f"INSERT INTO user_sessions ({','.join(values)}) VALUES ({','.join('?' * len(values))})",
        tuple(values.values()),
    )
    c.commit()
    session_id = c.execute(
        "SELECT id FROM user_sessions WHERE token_hash=?", (token_hash,)
    ).fetchone()[0]
    c.close()

    # 0600 before writing: the file holds a live credential, and a predictable
    # /tmp path with default permissions is readable by any local user.
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({
            "cookie_name": COOKIE,
            "cookie": f"{COOKIE}={token}",
            "session_id": session_id,
            "account": acct["email"],
            "report_id": target["report_id"],
            "file_index": target["file_index"],
            "page_count": target["page_count"],
        }, fh)

    print(f"minted session for {acct['email']}")
    print(f"report {target['report_id']} file {target['file_index']} pages {target['page_count']}")
    print(f"state written: {state_path} (mode 0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
