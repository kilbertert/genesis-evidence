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
    ReportExtractionError,
    ReportExtractionUnavailable,
    ReportFile,
    evidence_contains_value,
)
from ..conditions import CONDITIONS
from ..contracts import EvidenceMatchObservation
from .database import Database
from .papers import ObjectStore

METRIC_CODES = frozenset(metric for condition in CONDITIONS for metric in condition.metrics)
CONDITIONS_BY_METRIC = {
    metric: tuple(condition for condition in CONDITIONS if metric in condition.metrics)
    for metric in METRIC_CODES
}
EVIDENCE_RANK = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}
ASSESSMENT_SORTING_VERSION = "published-card-reference-range-v1"


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

    def claim_next_extraction(self) -> str | None:
        now = _now()
        with self.database.transaction() as connection:
            report = connection.execute(
                "SELECT id FROM reports WHERE status = 'uploaded' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if report is None:
                return None
            report_id = str(report["id"])
            connection.execute(
                "UPDATE reports SET status = 'extracted', updated_at = ? WHERE id = ?",
                (now, report_id),
            )
            self._audit(connection, report_id, "extraction_started", {}, actor="system")
        return report_id

    def load_files(self, report_id: str) -> tuple[ReportFile, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT original_name, object_key, media_type FROM report_files
                WHERE report_id = ? ORDER BY file_index
                """,
                (report_id,),
            ).fetchall()
        if not rows:
            raise ValueError("report files do not exist")
        return tuple(
            ReportFile(
                content=self.objects.read(row["object_key"]),
                filename=row["original_name"],
                media_type=row["media_type"],
            )
            for row in rows
        )

    def recover_running_extractions(self) -> int:
        now = _now()
        with self.database.transaction() as connection:
            reports = connection.execute(
                "SELECT id FROM reports WHERE status = 'extracted'"
            ).fetchall()
            for report in reports:
                report_id = str(report["id"])
                connection.execute(
                    "UPDATE reports SET status = 'uploaded', updated_at = ? WHERE id = ?",
                    (now, report_id),
                )
                self._audit(
                    connection, report_id, "extraction_recovered", {}, actor="system"
                )
        return len(reports)

    def fail_extraction(self, report_id: str, error: Exception) -> None:
        now = _now()
        message = (
            str(error)
            if isinstance(error, (ReportExtractionError, ReportExtractionUnavailable))
            else "报告智能解读失败，请重新上传或稍后重试。"
        )
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE reports SET status = 'abandoned', extraction_warnings_json = ?,
                    updated_at = ? WHERE id = ? AND status = 'extracted'
                """,
                (
                    json.dumps([message], ensure_ascii=False),
                    now,
                    report_id,
                ),
            ).rowcount
            if updated:
                self._audit(
                    connection,
                    report_id,
                    "extraction_failed",
                    {
                        "error_class": type(error).__name__,
                        "error_message": str(error)[:4000],
                    },
                    actor="system",
                )

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
            if report["status"] not in {"uploaded", "extracted"}:
                raise ValueError("only queued reports can accept an extraction")
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
                    extraction_warnings_json = ?, inferred_age = ?, inferred_sex = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    extracted.subject_consistency,
                    extracted.provider,
                    extracted.model,
                    extracted.run_id,
                    json.dumps(extracted.warnings, ensure_ascii=False),
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
        payload["warnings"] = json.loads(payload.pop("extraction_warnings_json"))
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
            supplied = {item.observation_id: item for item in confirmations}
            if len(supplied) != len(confirmations):
                raise ValueError("an observation can only be confirmed once")
            if not set(supplied).issubset(by_id):
                raise ValueError("confirmation contains an unknown observation")
            now = _now()
            values = []
            for row in rows:
                item = supplied.get(row["id"])
                if item is None or item.decision == "excluded":
                    values.append(
                        (row["id"], "excluded", None, None, None, None, None, now)
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
                    "confirmed": sum(item.decision != "excluded" for item in supplied.values()),
                    "excluded": len(rows)
                    - sum(item.decision != "excluded" for item in supplied.values()),
                },
            )

    def assess(self, report_id: str, access_token: str) -> dict[str, object]:
        now = _now()
        with self.database.transaction() as connection:
            report = self._authorized_report(connection, report_id, access_token)
            if report["status"] not in {"confirmed", "assessed"}:
                raise ValueError("only confirmed reports can be assessed")
            observations = connection.execute(
                """
                SELECT ro.id, oc.final_metric_code, oc.final_value,
                    oc.final_reference_low, oc.final_reference_high
                FROM report_observations ro
                JOIN observation_confirmations oc ON oc.observation_id = ro.id
                WHERE ro.report_id = ? AND oc.decision <> 'excluded'
                ORDER BY ro.rowid
                """,
                (report_id,),
            ).fetchall()
            cards = {}
            for row in connection.execute(
                """
                SELECT id, condition_code, version, grade, published_at
                FROM knowledge_cards WHERE status = 'published'
                ORDER BY published_at DESC, version DESC
                """
            ).fetchall():
                cards.setdefault(row["condition_code"], row)

            finding_by_condition = {}
            unmatched = []
            for observation in observations:
                if not _is_abnormal(observation):
                    continue
                conditions = CONDITIONS_BY_METRIC.get(observation["final_metric_code"], ())
                missing = []
                matched = False
                for condition in conditions:
                    card = cards.get(condition.code)
                    if card is None:
                        missing.append(condition.code)
                        continue
                    matched = True
                    # ponytail: generic reference-range deviations stay level 1/routine until
                    # reviewed metric-specific thresholds are published with the knowledge card.
                    finding = finding_by_condition.setdefault(
                        condition.code,
                        {
                            "condition": condition,
                            "card": card,
                            "observation_ids": [],
                            "urgency": "routine",
                            "severity": 1,
                            "needs_recheck": True,
                            "epidemiology": "",
                        },
                    )
                    finding["observation_ids"].append(observation["id"])
                if missing and not matched:
                    unmatched.append(
                        {
                            "observation_id": observation["id"],
                            "condition_codes": missing,
                        }
                    )
            findings = sorted(finding_by_condition.values(), key=_finding_sort_key)
            old_assessment = connection.execute(
                "SELECT id FROM assessments WHERE report_id = ?", (report_id,)
            ).fetchone()
            if old_assessment:
                connection.execute(
                    "DELETE FROM assessment_findings WHERE assessment_id = ?",
                    (old_assessment["id"],),
                )
                connection.execute("DELETE FROM assessments WHERE id = ?", (old_assessment["id"],))
            assessment_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO assessments(id, report_id, sorting_version, unmatched_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    report_id,
                    ASSESSMENT_SORTING_VERSION,
                    json.dumps(unmatched, ensure_ascii=False),
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO assessment_findings(
                    id, assessment_id, condition_code, card_id, card_version,
                    source_observation_ids_json, urgency, abnormality_severity,
                    evidence_strength, needs_recheck, department, epidemiology_background,
                    sort_position, sorting_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        assessment_id,
                        item["condition"].code,
                        item["card"]["id"],
                        item["card"]["version"],
                        json.dumps(item["observation_ids"]),
                        item["urgency"],
                        item["severity"],
                        item["card"]["grade"],
                        int(item["needs_recheck"]),
                        item["condition"].department,
                        item["epidemiology"],
                        position,
                        json.dumps(_sorting_dimensions(item), ensure_ascii=False),
                    )
                    for position, item in enumerate(findings)
                ],
            )
            connection.execute(
                "UPDATE reports SET status = 'assessed', updated_at = ? WHERE id = ?",
                (now, report_id),
            )
            self._audit(
                connection,
                report_id,
                "assessed",
                {"findings": len(findings), "unmatched": len(unmatched)},
                actor="system",
            )
        return self.get_assessment(report_id, access_token)

    def get_assessment(self, report_id: str, access_token: str) -> dict[str, object]:
        with self.database.connect() as connection:
            report = self._authorized_report(connection, report_id, access_token)
            assessment = connection.execute(
                "SELECT * FROM assessments WHERE report_id = ?", (report_id,)
            ).fetchone()
            if assessment is None:
                raise ValueError("report has not been assessed")
            findings = connection.execute(
                """
                SELECT af.*, c.name AS condition_name, c.recheck_direction,
                    kc.patient_visible_body
                FROM assessment_findings af
                JOIN knowledge_cards kc ON kc.id = af.card_id AND kc.status = 'published'
                JOIN conditions c ON c.code = af.condition_code
                WHERE af.assessment_id = ? ORDER BY af.sort_position
                """,
                (assessment["id"],),
            ).fetchall()
        visible = []
        for row in findings:
            item = dict(row)
            item["source_observation_ids"] = json.loads(item.pop("source_observation_ids_json"))
            item["sorting"] = json.loads(item.pop("sorting_json"))
            item["needs_recheck"] = bool(item["needs_recheck"])
            visible.append(item)
        return {
            "report_id": report_id,
            "status": report["status"],
            "sorting_version": assessment["sorting_version"],
            "findings": visible,
            "unmatched": json.loads(assessment["unmatched_json"]),
            "message": "" if visible else "暂无已审核内容",
        }

    def match_published_cards(
        self,
        observations: Sequence[EvidenceMatchObservation],
        *,
        correlation_id: str,
        actor: str = "health-flow",
    ) -> dict[str, object]:
        """Match confirmed external observations without importing report state."""

        with self.database.transaction() as connection:
            card_rows = connection.execute(
                """
                SELECT kc.id, kc.condition_code, kc.version, kc.grade,
                    kc.published_at, kc.evidence_profile_id, kc.patient_visible_body,
                    cc.claim_id, cc.evidence_text AS card_evidence, cc.locator,
                    cl.candidate_text, cl.paper_id, p.title AS paper_title, p.doi
                FROM knowledge_cards kc
                LEFT JOIN card_claims cc ON cc.card_id = kc.id
                LEFT JOIN claims cl ON cl.id = cc.claim_id
                LEFT JOIN papers p ON p.id = cl.paper_id
                WHERE kc.status = 'published'
                ORDER BY kc.published_at DESC, kc.version DESC
                """
            ).fetchall()
            cards: dict[str, dict[str, object]] = {}
            for row in card_rows:
                card = cards.setdefault(
                    row["condition_code"],
                    {
                        "id": row["id"],
                        "condition_code": row["condition_code"],
                        "version": row["version"],
                        "status": "published",
                        "grade": row["grade"],
                        "published_at": row["published_at"],
                        "evidence_profile_id": row["evidence_profile_id"],
                        "patient_visible_body": row["patient_visible_body"],
                        "sources": [],
                    },
                )
                if row["claim_id"]:
                    source = {
                        "claim_id": row["claim_id"],
                        "paper_id": row["paper_id"],
                        "paper_title": row["paper_title"],
                        "doi": row["doi"],
                        "evidence": row["card_evidence"] or row["candidate_text"] or "",
                        "locator": row["locator"] or "",
                    }
                    if source not in card["sources"]:
                        card["sources"].append(source)  # type: ignore[union-attr]

            findings_by_condition: dict[str, dict[str, object]] = {}
            unmatched: list[dict[str, object]] = []
            skipped: list[dict[str, object]] = []
            abnormal_count = 0
            for observation in observations:
                if observation.metric_code not in METRIC_CODES:
                    raise ValueError(f"unknown metric_code: {observation.metric_code}")
                _validate_final_values(
                    observation.evidence_text,
                    observation.value,
                    observation.reference_low,
                    observation.reference_high,
                )
                if observation.reference_low is None and observation.reference_high is None:
                    skipped.append(
                        {
                            "observation_id": observation.observation_id,
                            "reason": "missing_reference_range",
                        }
                    )
                    continue
                if not _is_abnormal_external(observation):
                    skipped.append(
                        {
                            "observation_id": observation.observation_id,
                            "reason": "within_reference_range",
                        }
                    )
                    continue
                abnormal_count += 1
                conditions = CONDITIONS_BY_METRIC.get(observation.metric_code, ())
                missing: list[str] = []
                matched = False
                source_observation = {
                    "observation_id": observation.observation_id,
                    "metric_code": observation.metric_code,
                    "value": observation.value,
                    "unit": observation.unit,
                    "reference_low": observation.reference_low,
                    "reference_high": observation.reference_high,
                    "evidence_text": observation.evidence_text,
                    "source_file_index": observation.source_file_index,
                    "source_page": observation.source_page,
                    "source_id": observation.source_id,
                }
                for condition in conditions:
                    card = cards.get(condition.code)
                    if card is None:
                        missing.append(condition.code)
                        continue
                    matched = True
                    finding = findings_by_condition.setdefault(
                        condition.code,
                        {
                            "condition_code": condition.code,
                            "condition_name": condition.name,
                            "card": card,
                            "source_observation_ids": [],
                            "urgency": "routine",
                            "abnormality_severity": 1,
                            "evidence_strength": card["grade"],
                            "needs_recheck": True,
                            "department": condition.department,
                            "recheck_direction": condition.recheck_direction,
                            "epidemiology_background": "",
                            "source_observations": [],
                        },
                    )
                    finding["source_observation_ids"].append(observation.observation_id)  # type: ignore[union-attr]
                    finding["source_observations"].append(source_observation)  # type: ignore[union-attr]
                # A metric is unmatched only when none of its mapped conditions has
                # a published card. Partial topic coverage is represented by the
                # matched finding instead of a contradictory "no content" warning.
                if missing and not matched:
                    unmatched.append(
                        {
                            "observation_id": observation.observation_id,
                            "condition_codes": missing,
                            "reason": "no_published_knowledge_card",
                        }
                    )

            findings = sorted(
                findings_by_condition.values(),
                key=lambda item: (
                    {"emergency": 0, "urgent": 1, "soon": 2, "routine": 3}[item["urgency"]],
                    -int(item["abnormality_severity"]),
                    EVIDENCE_RANK[item["evidence_strength"]],
                    item["department"],
                ),
            )
            result_findings = []
            for item in findings:
                card = item.pop("card")
                result_findings.append({**item, "card": card})
            result = {
                "schema_version": "1",
                "sorting_version": ASSESSMENT_SORTING_VERSION,
                "correlation_id": correlation_id,
                "findings": result_findings,
                "unmatched": unmatched,
                "skipped": skipped,
                "message": "" if result_findings else "暂无已审核内容",
            }
            for finding in result_findings:
                finding["sorting"] = {
                    "urgency": finding["urgency"],
                    "abnormality_severity": finding["abnormality_severity"],
                    "evidence_strength": finding["evidence_strength"],
                    "needs_recheck": finding["needs_recheck"],
                    "department": finding["department"],
                    "epidemiology_background": finding["epidemiology_background"],
                }
            self._audit(
                connection,
                correlation_id,
                "published_card_match",
                {
                    "observation_count": len(observations),
                    "abnormal_count": abnormal_count,
                    "finding_count": len(result_findings),
                    "unmatched_count": len(unmatched),
                    "skipped_count": len(skipped),
                    "metric_codes": sorted({item.metric_code for item in observations}),
                    "card_ids": sorted(
                        {item["card"]["id"] for item in result_findings}  # type: ignore[index]
                    ),
                },
                actor=actor,
            )
        return result

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


def _is_abnormal(observation) -> bool:
    value = observation["final_value"]
    low = observation["final_reference_low"]
    high = observation["final_reference_high"]
    return (low is not None and value < low) or (high is not None and value > high)


def _is_abnormal_external(observation: EvidenceMatchObservation) -> bool:
    return (
        (observation.reference_low is not None and observation.value < observation.reference_low)
        or (
            observation.reference_high is not None
            and observation.value > observation.reference_high
        )
    )


def _finding_sort_key(item: dict[str, object]) -> tuple[object, ...]:
    urgency_rank = {"emergency": 0, "urgent": 1, "soon": 2, "routine": 3}
    return (
        urgency_rank[item["urgency"]],
        -int(item["severity"]),
        EVIDENCE_RANK[item["card"]["grade"]],  # type: ignore[index]
        not bool(item["needs_recheck"]),
        item["condition"].department,  # type: ignore[union-attr]
        not bool(item["epidemiology"]),
    )


def _sorting_dimensions(item: dict[str, object]) -> dict[str, object]:
    return {
        "urgency": item["urgency"],
        "abnormality_severity": item["severity"],
        "evidence_strength": item["card"]["grade"],  # type: ignore[index]
        "needs_recheck": item["needs_recheck"],
        "department": item["condition"].department,  # type: ignore[union-attr]
        "epidemiology_background": item["epidemiology"],
    }


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
