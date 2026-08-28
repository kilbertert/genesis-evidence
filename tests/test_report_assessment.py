from __future__ import annotations

import json
from dataclasses import replace

import pytest

from genesis_evidence.core.contracts import EvidenceMatchObservation
from genesis_evidence.core.store import Database, ObjectStore
from genesis_evidence.core.store.reports import ConfirmationInput, ReportHandle, ReportStore
from genesis_evidence.reports.extraction import (
    PendingObservation,
    PendingReportExtraction,
    ReportFile,
)


def _store(tmp_path) -> tuple[Database, ReportStore, ReportHandle]:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = ReportStore(database, ObjectStore(tmp_path / "objects"))
    handle = store.create((ReportFile(b"report", "report.txt"),))
    return database, store, handle


def _extraction(value: float, *, low: float = 3.9, high: float = 6.1):
    return PendingReportExtraction(
        provider="test",
        model="test",
        run_id="run-1",
        status="pending_confirmation",
        subject_consistency="same",
        files=("report.txt",),
        observations=(
            PendingObservation(
                source_file_index=1,
                source_filename="report.txt",
                source_page=1,
                name="空腹血糖",
                model_value=value,
                model_unit="mmol/L",
                model_reference_low=low,
                model_reference_high=high,
                model_flag="high" if value > high else "normal",
                evidence=f"空腹血糖 {value} mmol/L {low}-{high}",
                extraction_status="clear",
                default_decision="pending",
            ),
        ),
    )


def _confirm(store, handle, *, value: float) -> str:
    store.save_extraction(handle.report_id, _extraction(value))
    observation_id = store.get(handle.report_id, handle.access_token)["observations"][0]["id"]
    store.confirm(
        handle.report_id,
        handle.access_token,
        (
            ConfirmationInput(
                observation_id=observation_id,
                decision="confirmed",
                metric_code="fasting_glucose",
            ),
        ),
    )
    return observation_id


def _publish_card(database: Database, condition_code: str, *, grade: str, version: str = "1.0.0"):
    card_id = f"card-{condition_code}-{version}"
    profile_id = f"profile-{condition_code}-{version}"
    topic_id = f"topic-{condition_code}-{version}"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES (?, ?, ?, ?, 'locked', 'Test question', '{}', '[]', '[]', '[]', '[]',
                '2026-08-11', 'reviewer', '2026-08-11T00:00:00Z', 'reviewer',
                '2026-08-11T00:00:00Z')
            """,
            (topic_id, topic_id, version, condition_code),
        )
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, scope_key, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome,
                timepoint, estimate_target, evidence_body_complete, certainty,
                certainty_rationale, evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES (?, ?, ?, 'metric:fasting_glucose', ?, 'Test ingredient',
                'Test form', 'Adults 40+',
                'Not reported', 'Test dose', 'Comparator', 'Outcome', 'Timepoint',
                'Test target', 1, ?, 'Test-only reviewed profile', '2026-08-11',
                'reviewer', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z')
            """,
            (profile_id, topic_id, condition_code, version, grade),
        )
        connection.execute(
            """
            INSERT INTO knowledge_cards(
                id, condition_code, version, status, grade, evidence_profile_id,
                reviewer, reviewed_at,
                published_at, patient_visible_body, created_at
            ) VALUES (?, ?, ?, 'published', ?, ?, 'reviewer', '2026-08-11T00:00:00Z',
                '2026-08-11T00:00:00Z', '这是经过审核的营养健康知识。',
                '2026-08-11T00:00:00Z')
            """,
            (card_id, condition_code, version, grade, profile_id),
        )
    return card_id


def test_only_confirmed_abnormal_observations_match_published_cards(tmp_path) -> None:
    database, store, handle = _store(tmp_path)
    observation_id = _confirm(store, handle, value=6.8)
    card_id = _publish_card(database, "COND_PREDIABETES", grade="moderate")

    result = store.assess(handle.report_id, handle.access_token)

    assert result["status"] == "assessed"
    assert result["message"] == ""
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["condition_code"] == "COND_PREDIABETES"
    assert finding["card_id"] == card_id
    assert finding["source_observation_ids"] == [observation_id]
    assert finding["urgency"] == "routine"
    assert finding["abnormality_severity"] == 1
    assert finding["needs_recheck"] is True
    assert finding["department"] == "内分泌科"
    assert finding["recheck_direction"]
    assert finding["patient_visible_body"] == "这是经过审核的营养健康知识。"


def test_external_match_uses_matcher_result_and_preserves_audit_metrics(tmp_path) -> None:
    database, store, _ = _store(tmp_path)
    card_id = _publish_card(database, "COND_PREDIABETES", grade="moderate")
    observations = (
        EvidenceMatchObservation(
            observation_id="glucose-high",
            confirmation_status="confirmed",
            metric_code="fasting_glucose",
            value=6.8,
            unit="mmol/L",
            reference_low=3.9,
            reference_high=6.1,
            evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1 H",
            source_file_index=1,
            source_page=1,
        ),
        EvidenceMatchObservation(
            observation_id="uric-normal",
            confirmation_status="confirmed",
            metric_code="uric_acid",
            value=300,
            unit="umol/L",
            reference_low=200,
            reference_high=420,
            evidence_text="尿酸 300 umol/L 200-420 N",
            source_file_index=1,
            source_page=1,
        ),
    )

    result = store.match_published_cards(observations, correlation_id="match-1")

    assert result["findings"][0]["card"]["id"] == card_id
    assert result["skipped"] == [
        {"observation_id": "uric-normal", "reason": "within_reference_range"}
    ]
    with database.connect() as connection:
        detail = json.loads(
            connection.execute(
                "SELECT detail_json FROM audit_events WHERE entity_id = 'match-1'"
            ).fetchone()["detail_json"]
        )
    assert detail["metric_codes"] == ["fasting_glucose", "uric_acid"]
    assert detail["card_ids"] == [card_id]


def test_low_card_is_visible_as_context_only_in_report_assessment(tmp_path) -> None:
    database, store, handle = _store(tmp_path)
    _confirm(store, handle, value=6.8)
    _publish_card(database, "COND_PREDIABETES", grade="low")

    result = store.assess(handle.report_id, handle.access_token)

    finding = result["findings"][0]
    assert finding["content_layer"] == "context_only"
    assert finding["action_status"] == "not_available"
    assert finding["product_status"] == "not_implemented"


def test_abnormal_value_without_published_card_returns_no_reviewed_content(tmp_path) -> None:
    _, store, handle = _store(tmp_path)
    observation_id = _confirm(store, handle, value=6.8)

    result = store.assess(handle.report_id, handle.access_token)

    assert result["findings"] == []
    assert result["message"] == "暂无已审核内容"
    assert result["unmatched"][0]["observation_id"] == observation_id


def test_unconfirmed_report_cannot_be_assessed(tmp_path) -> None:
    _, store, handle = _store(tmp_path)
    store.save_extraction(handle.report_id, _extraction(6.8))
    with pytest.raises(ValueError, match="only confirmed"):
        store.assess(handle.report_id, handle.access_token)


def test_evidence_strength_precedes_department_and_epidemiology_is_last(tmp_path) -> None:
    database, store, handle = _store(tmp_path)
    _confirm(store, handle, value=6.8)
    _publish_card(database, "COND_PREDIABETES", grade="low")
    _publish_card(database, "COND_MASLD_RISK", grade="high")

    result = store.assess(handle.report_id, handle.access_token)

    assert [item["condition_code"] for item in result["findings"]] == [
        "COND_MASLD_RISK",
        "COND_PREDIABETES",
    ]
    assert list(result["findings"][0]["sorting"]) == [
        "urgency",
        "abnormality_severity",
        "evidence_strength",
        "needs_recheck",
        "department",
        "epidemiology_background",
    ]
    assert result["findings"][0]["epidemiology_background"] == ""


def test_multiple_abnormal_rows_for_one_condition_form_one_finding(tmp_path) -> None:
    database, store, handle = _store(tmp_path)
    first = _extraction(6.8).observations[0]
    second = replace(
        first,
        source_page=2,
        name="空腹血糖复测",
        model_value=7.0,
        evidence="空腹血糖复测 7.0 mmol/L 3.9-6.1",
    )
    store.save_extraction(
        handle.report_id,
        replace(_extraction(6.8), observations=(first, second)),
    )
    report = store.get(handle.report_id, handle.access_token)
    store.confirm(
        handle.report_id,
        handle.access_token,
        tuple(
            ConfirmationInput(
                observation_id=item["id"],
                decision="confirmed",
                metric_code="fasting_glucose",
            )
            for item in report["observations"]
        ),
    )
    _publish_card(database, "COND_PREDIABETES", grade="moderate")

    findings = store.assess(handle.report_id, handle.access_token)["findings"]
    assert len(findings) == 1
    assert len(findings[0]["source_observation_ids"]) == 2


def test_stale_card_becomes_invisible_without_reusing_old_patient_content(tmp_path) -> None:
    database, store, handle = _store(tmp_path)
    _confirm(store, handle, value=6.8)
    card_id = _publish_card(database, "COND_PREDIABETES", grade="moderate")
    assert store.assess(handle.report_id, handle.access_token)["findings"]
    with database.transaction() as connection:
        connection.execute("UPDATE knowledge_cards SET status = 'stale' WHERE id = ?", (card_id,))

    result = store.get_assessment(handle.report_id, handle.access_token)
    assert result["findings"] == []
    assert result["message"] == "暂无已审核内容"
