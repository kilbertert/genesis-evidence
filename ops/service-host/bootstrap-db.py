#!/usr/bin/env python3
"""Create the evidence database schema and seed rows if it does not exist.

Safe to re-run: the schema uses CREATE TABLE IF NOT EXISTS and the seed is an
upsert, so this is idempotent.

The database path is taken from GENESIS_EVIDENCE_DATABASE, falling back to the
project's standard location. Pass a path as the first argument to override.

    ops/bootstrap-db.py                      # env var, or the standard path
    ops/bootstrap-db.py /tmp/probe.sqlite3   # explicit
"""

from __future__ import annotations

import os
import pathlib
import sys

from genesis_evidence.core.store import Database

DEFAULT_DATABASE = "/opt/genesis-evidence/var/genesis-evidence.sqlite3"


def database_path(argv: list[str]) -> pathlib.Path:
    if len(argv) > 1:
        return pathlib.Path(argv[1])
    return pathlib.Path(os.getenv("GENESIS_EVIDENCE_DATABASE", DEFAULT_DATABASE))


def main() -> int:
    path = database_path(sys.argv)
    database = Database(path)
    database.initialize()
    tables = database.table_names()
    print(f"db: {path}")
    print(f"tables: {len(tables)}")
    print(f"exists: {path.is_file()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
