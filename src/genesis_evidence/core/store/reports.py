"""Persistence and deterministic confirmation for personal health reports."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...reports.extraction import (
    PendingReportExtraction,
    ReportFile,
    evidence_contains_value,
)
from ..conditions import CONDITIONS
from .database import Database
from .papers import ObjectStore

METRIC_CODES = frozenset(metric for condition in CONDITIONS for metric in condition.metrics)


class ReportAccessDenied(PermissionError):
    """Raised when a report access token is invalid."""


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    decision: Literal["confirmed", "corrected", "excluded"]
    metric_code: str | None = None
    value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    reference_low: float | None = None
    reference_high: float | None = None

    @model_validator(mode="after")
    def require_only_corrected_values(self) -> ConfirmationInput:
        supplied = (
            self.value is not None
            or self.unit is not None
            or self.reference_low is not None
            or self.reference_high is not None
        )
        if self.decision == "excluded":
            if self.metric_code is not None or supplied:
                raise ValueError("excluded observations cannot contain final values")
        elif not self.metric_code:
            raise ValueError("confirmed observations require metric_code")
        elif self.decision == "confirmed" and supplied:
            raise ValueError("confirmed observations use the model values unchanged")
        elif self.decision == "corrected" and (self.value is None or self.unit is None):
            raise ValueError("corrected observations require value and unit")
        return self


@dataclass(frozen=True, slots=True)
class ReportHandle:
    report_id: str
    access_token: str


class ReportStore:
    def __init__(self, database: Database, objects: ObjectStore) -> None:
        self.database = database
        self.objects = objects

    def create(self, files: Sequence[ReportFile]) -> ReportHandle:
        if not files:
            raise ValueError("at least one report file is required")
        report_id = str(uuid.uuid4())
        access_token = secrets.token_urlsafe(32)
        now = _now()
        stored_files = []
        for index, item in enumerate(files, start=1):
            if not item.content:
                raise ValueError(f"report file {index} is empty")
            filename = Path(item.filename).name[:180] or f"report-{index}"
            suffix = Path(filename).suffix.casefold().lstrip(".") or "bin"
            stored_files.append(
                (
                    index,
                    filename,
                    self.objects.put(item.content, suffix=suffix),
                    item.media_type,
                )
            )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reports(id, access_token_hash, status, created_at, updated_at)
                VALUES (?, ?, 'uploaded', ?, ?)
                """,
                (report_id, _token_hash(access_token), now, now),
            )
            connection.executemany(
                """
                INSERT INTO report_files(
                    report_id, file_index, original_name, object_key, sha256, media_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (report_id, index, filename, stored.key, stored.sha256, media_type)
                    for index, filename, stored, media_type in stored_files
                ],
            )
            self._audit(connection, report_id, "uploaded", {"file_count": len(files)})
        return ReportHandle(report_id, access_token)

    def save_extraction(self, report_id: str, extracted: PendingReportExtraction) -> None:
        if extracted.status != "pending_confirmation":
            raise ValueError("extraction must stop at pending_confirmation")
        now = _now()
        with self.database.transaction() as connection:
            report = connection.execute(
                "SELECT status FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            if report is None:
                raise ValueError("report not found")
            if report["status"] != "uploaded":
                raise ValueError("only uploaded reports can accept an extraction")
            filenames = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT original_name FROM report_files
                    WHERE report_id = ? ORDER BY file_index
                    """,
                    (report_id,),
                ).fetchall()
            )
            if filenames != extracted.files:
                raise ValueError("extraction files do not match the uploaded report")
            connection.execute(
                "UPDATE reports SET status = 'extracted', updated_at = ? WHERE id = ?",
                (now, report_id),
            )
            self._audit(
                connection,
                report_id,
                "extracted",
                {"provider": extracted.provider, "model": extracted.model},
                actor="system",
            )
            connection.executemany(
                """
                INSERT INTO report_observations(
                    id, report_id, source_file_index, source_page, original_name,
                    model_value, model_unit, reference_low, reference_high, model_flag,
                    evidence_text, extraction_status, default_decision, validation_issues_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        report_id,
                        item.source_file_index,
                        item.source_page,
                        item.name,
                        item.model_value,
                        item.model_unit,
                        item.model_reference_low,
                        item.model_reference_high,
                        item.model_flag,
                        item.evidence,
                        item.extraction_status,
                        item.default_decision,
                        json.dumps(item.validation_issues, ensure_ascii=False),
                    )
                    for item in extracted.observations
                ],
            )
            connection.execute(
                """
                UPDATE reports SET
                    status = 'pending_confirmation', subject_consistency = ?,
                    extraction_provider = ?, extraction_model = ?, extraction_run_id = ?,
                    inferred_age = ?, inferred_sex = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    extracted.subject_consistency,
                    extracted.provider,
                    extracted.model,
                    extracted.run_id,
                    extracted.inferred_age,
                    extracted.inferred_sex,
                    now,
                    report_id,
                ),
            )
            self._audit(
                connection,
                report_id,
                "pending_confirmation",
                {"observation_count": len(extracted.observations)},
                actor="system",
            )

    def get(self, report_id: str, access_token: str) -> dict[str, object]:
        with self.database.connect() as connection:
            report = self._authorized_report(connection, report_id, access_token)
            observations = connection.execute(
                """
                SELECT ro.*, oc.decision, oc.final_metric_code, oc.final_value, oc.final_unit,
                    oc.final_reference_low, oc.final_reference_high, oc.confirmed_at
                FROM report_observations ro
                LEFT JOIN observation_confirmations oc ON oc.observation_id = ro.id
                WHERE ro.report_id = ?
                ORDER BY ro.source_file_index, ro.source_page, ro.rowid
                """,
                (report_id,),
            ).fetchall()
            files = connection.execute(
                """
                SELECT file_index, original_name, sha256, media_type, page_count
                FROM report_files WHERE report_id = ? ORDER BY file_index
                """,
                (report_id,),
            ).fetchall()
        payload = dict(report)
        payload.pop("access_token_hash", None)
        payload["files"] = [dict(row) for row in files]
        payload["observations"] = [
            {**dict(row), "validation_issues": json.loads(row["validation_issues_json"])}
            for row in observations
        ]
        return payload

    def confirm(
        self,
        report_id: str,
        access_token: str,
        confirmations: Sequence[ConfirmationInput],
    ) -> None:
        with self.database.transaction() as connection:
            report = self._authorized_report(connection, report_id, access_token)
            if report["status"] != "pending_confirmation":
                raise ValueError("report is not pending confirmation")
            rows = connection.execute(
                "SELECT * FROM report_observations WHERE report_id = ? ORDER BY rowid",
                (report_id,),
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            supplied = {item.observation_id for item in confirmations}
            if len(supplied) != len(confirmations) or supplied != set(by_id):
                raise ValueError("every observation must be confirmed exactly once")
            now = _now()
            values = []
            for item in confirmations:
                row = by_id[item.observation_id]
                if item.decision == "excluded":
                    values.append(
                        (item.observation_id, "excluded", None, None, None, None, None, now)
                    )
                    continue
                if item.metric_code not in METRIC_CODES:
                    raise ValueError(f"unknown metric_code: {item.metric_code}")
                value = row["model_value"] if item.decision == "confirmed" else item.value
                unit = row["model_unit"] if item.decision == "confirmed" else item.unit
                low = row["reference_low"] if item.decision == "confirmed" else item.reference_low
                high = (
                    row["reference_high"]
                    if item.decision == "confirmed"
                    else item.reference_high
                )
                _validate_final_values(row["evidence_text"], value, low, high)
                values.append(
                    (
                        item.observation_id,
                        item.decision,
                        item.metric_code,
                        value,
                        unit,
                        low,
                        high,
                        now,
                    )
                )
            connection.executemany(
                """
                INSERT INTO observation_confirmations(
                    observation_id, decision, final_metric_code, final_value, final_unit,
                    final_reference_low, final_reference_high, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            connection.execute(
                "UPDATE reports SET status = 'confirmed', updated_at = ? WHERE id = ?",
                (now, report_id),
            )
            self._audit(
                connection,
                report_id,
                "confirmed",
                {
                    "confirmed": sum(item.decision != "excluded" for item in confirmations),
                    "excluded": sum(item.decision == "excluded" for item in confirmations),
                },
            )

    @staticmethod
    def _authorized_report(connection, report_id: str, access_token: str):
        report = connection.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if report is None or not hmac.compare_digest(
            report["access_token_hash"], _token_hash(access_token)
        ):
            raise ReportAccessDenied("report access denied")
        return report

    @staticmethod
    def _audit(
        connection,
        report_id: str,
        action: str,
        detail: object,
        *,
        actor: str = "user",
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(entity_type, entity_id, action, actor, detail_json, created_at)
            VALUES ('report', ?, ?, ?, ?, ?)
            """,
            (report_id, action, actor, json.dumps(detail, ensure_ascii=False), _now()),
        )


def _validate_final_values(
    evidence: str,
    value: float | None,
    reference_low: float | None,
    reference_high: float | None,
) -> None:
    if value is None or not math.isfinite(value) or not evidence_contains_value(evidence, value):
        raise ValueError("confirmed value lacks source evidence")
    bounds = tuple(bound for bound in (reference_low, reference_high) if bound is not None)
    if any(not math.isfinite(bound) for bound in bounds) or (
        len(bounds) == 2 and bounds[0] > bounds[1]
    ):
        raise ValueError("confirmed reference range is invalid")
    if any(not evidence_contains_value(evidence, bound) for bound in bounds):
        raise ValueError("confirmed reference range lacks source evidence")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
