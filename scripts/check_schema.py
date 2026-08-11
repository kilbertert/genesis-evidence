"""Keep the stage-one schema within its explicit table budget."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from genesis_evidence.core.store import Database

MAX_TABLES = 24


def main() -> None:
    with TemporaryDirectory() as directory:
        database = Database(f"{directory}/schema.sqlite3")
        database.initialize()
        table_names = database.table_names()
    if len(table_names) > MAX_TABLES:
        raise SystemExit(f"Schema has {len(table_names)} tables; maximum is {MAX_TABLES}")
    print(f"schema_tables={len(table_names)}")


if __name__ == "__main__":
    main()
