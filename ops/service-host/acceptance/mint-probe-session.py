"""Mint a short-lived session for an existing migrated account, for acceptance.

Does NOT change any user data other than adding one session row, which the
caller removes afterwards. No password is needed or used.

Prints the cookie name, token, and the target report/file so the caller can make
authenticated requests as that user.
"""
import hashlib
import json
import pathlib
import secrets
import sqlite3
from datetime import datetime, timedelta

DB = "/opt/health-flow/var/healthflow.db"
COOKIE = "healthflow_session"

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

# Pick a real user (not test residue) who actually owns a report with a file.
acct = c.execute("""
    SELECT a.id, a.email
    FROM user_accounts a
    JOIN medical_reports r ON r.patient_id = a.id
    JOIN report_files rf ON rf.report_id = r.id
    WHERE a.email NOT LIKE '%example%' AND a.email NOT LIKE '%acceptance%'
    GROUP BY a.id ORDER BY count(r.id) DESC LIMIT 1
""").fetchone()
if acct is None:
    raise SystemExit("no real user with a report and file found")

target = c.execute("""
    SELECT r.id AS report_id, rf.file_index, rf.page_count
    FROM medical_reports r JOIN report_files rf ON rf.report_id = r.id
    WHERE r.patient_id = ? ORDER BY r.created_at DESC LIMIT 1
""", (acct["id"],)).fetchone()

token = secrets.token_urlsafe(32)
now = datetime.now()
cols = [r[1] for r in c.execute("PRAGMA table_info(user_sessions)")]
row = {
    "account_id": acct["id"],
    "token_hash": hashlib.sha256(token.encode()).hexdigest(),
    "created_at": now.isoformat(sep=" "),
    "last_seen_at": now.isoformat(sep=" "),
    "expires_at": (now + timedelta(hours=1)).isoformat(sep=" "),
}
row = {k: v for k, v in row.items() if k in cols}
c.execute(
    f"INSERT INTO user_sessions ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
    tuple(row.values()),
)
c.commit()

sid = c.execute(
    "SELECT id FROM user_sessions WHERE token_hash=?",
    (hashlib.sha256(token.encode()).hexdigest(),),
).fetchone()[0]
c.close()

pathlib.Path("/tmp/e2e-session.json").write_text(json.dumps({
    "cookie": COOKIE, "token": token, "session_id": sid,
    "account": acct["email"], "report_id": target["report_id"],
    "file_index": target["file_index"], "page_count": target["page_count"],
}))
print("minted session for:", acct["email"])
print("report:", target["report_id"], "file:", target["file_index"], "pages:", target["page_count"])
