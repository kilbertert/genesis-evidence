#!/usr/bin/env python3
"""Load a curated seed of published knowledge cards into a database.

Idempotent, and safe to re-run: each row is upserted with
`INSERT ... ON CONFLICT DO UPDATE`, which updates the existing row in place.

It deliberately does **not** use `INSERT OR REPLACE`. SQLite implements that by
deleting the conflicting row and inserting a new one, so replacing a row that
other rows reference through a plain foreign key raises
`FOREIGN KEY constraint failed` — the seed's `conditions` row is referenced by
topics, profiles, and cards. Updating in place avoids the delete entirely.

Values are bound as parameters, so text containing quotes, newlines, or
non-ASCII characters is stored verbatim.

    load-seed.py <seed.json> [target-database]
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

# Parents before children, so foreign keys resolve as rows are inserted.
ORDER = (
    "conditions",
    "studies",
    "papers",
    "paper_extractions",
    "results",
    "claims",
    "evidence_topics",
    "evidence_profiles",
    "knowledge_cards",
    "card_claims",
)


def primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    """Column names making up the table's primary key."""
    columns = []
    for row in connection.execute(f"PRAGMA table_info({table})"):
        if row[5]:  # pk flag, 1-based position
            columns.append((row[5], row[1]))
    return [name for _, name in sorted(columns)]


def upsert(connection: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    key = primary_key(connection, table)
    if not key:
        raise SystemExit(f"{table} has no primary key; cannot upsert safely")
    # Every column except the key goes into the DO UPDATE assignment.
    updates = [c for c in columns if c not in key]
    statement = (
        f"INSERT INTO {table} ({','.join(columns)}) "
        f"VALUES ({','.join('?' * len(columns))})"
    )
    if updates:
        assignments = ",".join(f"{c}=excluded.{c}" for c in updates)
        statement += f" ON CONFLICT({','.join(key)}) DO UPDATE SET {assignments}"
    else:
        statement += f" ON CONFLICT({','.join(key)}) DO NOTHING"
    connection.executemany(statement, [tuple(r[c] for c in columns) for r in rows])
    return len(rows)


def main() -> int:
    seed_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/seed-cards.json")
    db_path = pathlib.Path(
        sys.argv[2] if len(sys.argv) > 2 else "/opt/genesis-evidence/var/genesis-evidence.sqlite3"
    )
    seed = json.loads(seed_path.read_text())

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    counts = {}
    for table in ORDER:
        counts[table] = upsert(connection, table, seed["tables"].get(table) or [])
    connection.commit()

    published = connection.execute(
        "SELECT count(*) FROM knowledge_cards WHERE status='published'"
    ).fetchone()[0]
    cards_with_claims = connection.execute(
        "SELECT count(DISTINCT card_id) FROM card_claims"
    ).fetchone()[0]
    connection.close()

    print("upserted:", counts)
    print("published cards:", published, "| cards with claims:", cards_with_claims)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
