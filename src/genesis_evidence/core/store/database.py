"""Small SQLite boundary shared by domain-specific stores."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..conditions import CONDITIONS, ConditionDefinition
from .schema import SCHEMA


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.executemany(
                """
                INSERT INTO conditions(code, name, metrics_json, department, recheck_direction)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    metrics_json = excluded.metrics_json,
                    department = excluded.department,
                    recheck_direction = excluded.recheck_direction
                """,
                [
                    (
                        item.code,
                        item.name,
                        json.dumps(item.metrics, ensure_ascii=False),
                        item.department,
                        item.recheck_direction,
                    )
                    for item in CONDITIONS
                ],
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self) -> tuple[str, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return tuple(row["name"] for row in rows)

    def list_conditions(self) -> tuple[ConditionDefinition, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT code, name, metrics_json, department, recheck_direction
                FROM conditions ORDER BY code
                """
            ).fetchall()
        return tuple(
            ConditionDefinition(
                code=row["code"],
                name=row["name"],
                metrics=tuple(json.loads(row["metrics_json"])),
                department=row["department"],
                recheck_direction=row["recheck_direction"],
            )
            for row in rows
        )
