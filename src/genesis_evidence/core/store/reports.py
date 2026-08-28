"""Persistence and deterministic confirmation for personal health reports."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...products.recommendations import (
    load_published_products,
    recommend,
    recommendation_message,
)
from ...reports.extraction import (
    PendingReportExtraction,
    ReportExtractionError,
    ReportExtractionUnavailable,
    ReportFile,
)
from ..contracts import EvidenceMatchObservation, card_capabilities
from ..matching import (
    ASSESSMENT_SORTING_VERSION,
    CONDITIONS_BY_METRIC,
    EVIDENCE_RANK,
    CardAdapter,
    CardScopeResolver,
    EvidenceMatcher,
    MatchObservation,
    MatchResult,
    ObservationInput,
    _is_v2_unmatched,
    patient_reply_v2,
    project_observation,
    validate_observation,
)
from .database import Database
from .papers import ObjectStore

__all__ = [
    "ConfirmationInput",
    "ReportAccessDenied",
    "ReportHandle",
    "ReportStore",
]


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


def _row_to_input(row) -> ObservationInput:
    return ObservationInput(
        observation_id=str(row["id"]),
        metric_code=str(row["final_metric_code"]),
        value=float(row["final_value"]),
        reference_low=row["final_reference_low"],
        reference_high=row["final_reference_high"],
    )


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
                self._audit(connection, report_id, "extraction_recovered", {}, actor="system")
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
                    values.append((row["id"], "excluded", None, None, None, None, None, now))
                    continue
                value = row["model_value"] if item.decision == "confirmed" else item.value
                unit = row["model_unit"] if item.decision == "confirmed" else item.unit
                low = row["reference_low"] if item.decision == "confirmed" else item.reference_low
                high = (
                    row["reference_high"] if item.decision == "confirmed" else item.reference_high
                )
                validate_observation(
                    ObservationInput(
                        observation_id=item.observation_id,
                        metric_code=str(item.metric_code),
                        value=value,
                        reference_low=low,
                        reference_high=high,
                        evidence_text=row["evidence_text"],
                    )
                )
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
            cards: dict[str, dict[str, object]] = {}
            for row in connection.execute(
                """
                SELECT id, condition_code, version, grade, published_at
                FROM knowledge_cards
                WHERE status = 'published' AND grade IN ('high', 'moderate', 'low')
                ORDER BY published_at DESC, version DESC
                """
            ).fetchall():
                cards.setdefault(
                    row["condition_code"],
                    {**dict(row), **card_capabilities(str(row["grade"]))},
                )

            adapter = CardAdapter(
                condition_codes_for_metric=lambda metric_code: CONDITIONS_BY_METRIC.get(
                    metric_code, ()
                ),
                lookup=lambda condition_code, scope_key: cards.get(condition_code),
            )
            resolver = CardScopeResolver(strict=False)
            entries = [
                MatchObservation(input=_row_to_input(row))
                for row in observations
                if row["final_value"] is not None
            ]

            def _produce_finding(
                findings_by_condition: dict[str, dict[str, object]],
                condition,
                card: dict[str, object],
                entry: MatchObservation,
                scope_key: str,
            ) -> None:
                # ponytail: generic reference-range deviations stay level 1/routine until
                # reviewed metric-specific thresholds are published with the knowledge card.
                finding = findings_by_condition.setdefault(
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
                finding["observation_ids"].append(entry.input.observation_id)

            def _collect_unmatched(entry, missing, matched):
                if not (missing and not matched):
                    return None
                return {
                    "observation_id": entry.input.observation_id,
                    "condition_codes": missing,
                }

            result: MatchResult = EvidenceMatcher.match_published_cards(
                entries,
                adapter=adapter,
                resolver=resolver,
                produce_finding=_produce_finding,
                collect_unmatched=_collect_unmatched,
                validate=None,
            )
            findings = sorted(result.findings.values(), key=_finding_sort_key)
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
                    json.dumps(result.unmatched, ensure_ascii=False),
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
                {"findings": len(findings), "unmatched": len(result.unmatched)},
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
                    kc.patient_visible_body, kc.grade
                FROM assessment_findings af
                JOIN knowledge_cards kc ON kc.id = af.card_id
                    AND kc.status = 'published' AND kc.grade IN ('high', 'moderate', 'low')
                JOIN conditions c ON c.code = af.condition_code
                WHERE af.assessment_id = ? ORDER BY af.sort_position
                """,
                (assessment["id"],),
            ).fetchall()
            published_products = load_published_products(connection)
            subject_age = report["inferred_age"]
        visible = []
        for row in findings:
            item = dict(row)
            item["source_observation_ids"] = json.loads(item.pop("source_observation_ids_json"))
            item["sorting"] = json.loads(item.pop("sorting_json"))
            item["needs_recheck"] = bool(item["needs_recheck"])
            item.update(card_capabilities(str(item["grade"])))
            recommendations = recommend(
                str(item["condition_code"]),
                item["source_observation_ids"],
                products=published_products,
                urgency=str(item["urgency"]),
                abnormality_severity=int(item["abnormality_severity"]),
                subject_age=subject_age,
            )
            item["recommendations"] = [
                recommendation.as_dict() for recommendation in recommendations
            ]
            item["recommendation_message"] = recommendation_message(recommendations)
            item["product_status"] = (
                "available" if recommendations else "not_implemented"
            )
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
                    kc.published_at, kc.evidence_profile_id, ep.scope_key,
                    kc.patient_visible_body,
                    cc.claim_id, cc.evidence_text AS card_evidence, cc.locator,
                    cl.candidate_text, cl.paper_id, p.title AS paper_title, p.doi
                FROM knowledge_cards kc
                JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
                LEFT JOIN card_claims cc ON cc.card_id = kc.id
                LEFT JOIN claims cl ON cl.id = cc.claim_id
                LEFT JOIN papers p ON p.id = cl.paper_id
                WHERE kc.status = 'published' AND kc.grade IN ('high', 'moderate', 'low')
                ORDER BY kc.published_at DESC, kc.version DESC
                """
            ).fetchall()
            cards: dict[tuple[str, str], dict[str, object]] = {}
            for row in card_rows:
                scope_key = str(row["scope_key"] or "").strip()
                # Legacy cards without an explicit outcome scope are deliberately
                # not eligible for external metric matching.
                if not scope_key:
                    continue
                card = cards.setdefault(
                    (row["condition_code"], scope_key),
                    {
                        "id": row["id"],
                        "condition_code": row["condition_code"],
                        "scope_key": scope_key,
                        "version": row["version"],
                        "status": "published",
                        "grade": row["grade"],
                        "published_at": row["published_at"],
                        "evidence_profile_id": row["evidence_profile_id"],
                        "patient_visible_body": row["patient_visible_body"],
                        "sources": [],
                        **card_capabilities(str(row["grade"])),
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

            adapter = CardAdapter(
                condition_codes_for_metric=lambda metric_code: CONDITIONS_BY_METRIC.get(
                    metric_code, ()
                ),
                lookup=lambda condition_code, scope_key: cards.get((condition_code, scope_key)),
            )
            resolver = CardScopeResolver(strict=True)
            entries = [project_observation(observation) for observation in observations]

            def _produce_finding(
                findings_by_condition: dict[str, dict[str, object]],
                condition,
                card: dict[str, object],
                entry: MatchObservation,
                scope_key: str,
            ) -> None:
                inp = entry.input
                source = entry.source
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
                        "content_layer": card["content_layer"],
                        "action_status": card["action_status"],
                        "action_message": card["action_message"],
                        "product_status": card["product_status"],
                    },
                )
                finding["source_observation_ids"].append(inp.observation_id)  # type: ignore[union-attr]
                finding["source_observations"].append(source)  # type: ignore[union-attr]

            result: MatchResult = EvidenceMatcher.match_published_cards(
                entries,
                adapter=adapter,
                resolver=resolver,
                produce_finding=_produce_finding,
                collect_unmatched=_is_v2_unmatched,
                validate=validate_observation,
            )

            findings = sorted(
                result.findings.values(),
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
            result_payload = {
                "schema_version": "2",
                "sorting_version": ASSESSMENT_SORTING_VERSION,
                "correlation_id": correlation_id,
                "findings": result_findings,
                "unmatched": result.unmatched,
                "skipped": result.skipped,
                "message": "" if result_findings else "暂无已审核内容",
            }
            patient_reply = patient_reply_v2(result_findings, result.unmatched)
            for patient_finding, finding in zip(
                patient_reply["findings"], result_findings, strict=True
            ):
                patient_finding["recommendations"] = finding["recommendations"]
                patient_finding["recommendation_message"] = finding[
                    "recommendation_message"
                ]
            result_payload["patient_reply"] = patient_reply
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
                    "abnormal_count": result.abnormal_count,
                    "finding_count": len(result_findings),
                    "unmatched_count": len(result.unmatched),
                    "skipped_count": len(result.skipped),
                    "metric_codes": result.metric_codes,
                    "card_ids": sorted(
                        {item["card"]["id"] for item in result_findings}  # type: ignore[index]
                    ),
                },
                actor=actor,
            )
        return result_payload

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
