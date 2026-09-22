"""Load a curated seed of published knowledge cards into a database.

Inserts with bound parameters, so values containing quotes or non-ASCII text are
stored verbatim. Idempotent: rows are keyed by primary key and replaced.

    load-cards.py <seed.json> [database]
"""

import json
import pathlib
import sqlite3
import sys

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
        rows = seed["tables"].get(table) or []
        if not rows:
            counts[table] = 0
            continue
        columns = list(rows[0].keys())
        statement = (
            f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) "
            f"VALUES ({','.join('?' * len(columns))})"
        )
        connection.executemany(statement, [tuple(r[c] for c in columns) for r in rows])
        counts[table] = len(rows)
    connection.commit()

    published = connection.execute(
        "SELECT count(*) FROM knowledge_cards WHERE status='published'"
    ).fetchone()[0]
    connection.close()
    print("inserted:", counts)
    print("published cards now:", published)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
