from __future__ import annotations

from dataclasses import replace

import pytest

from genesis_evidence.core.store import (
    ConfirmationInput,
    Database,
    ObjectStore,
    ReportAccessDenied,
    ReportHandle,
    ReportStore,
)
from genesis_evidence.reports.extraction import (
    PendingObservation,
    PendingReportExtraction,
    ReportExtractionUnavailable,
    ReportFile,
)


def _pending(*observations: PendingObservation) -> PendingReportExtraction:
    return PendingReportExtraction(
        provider="openai_responses",
        model="gpt-5.6-sol",
        run_id="run-1",
        status="pending_confirmation",
        subject_consistency="same",
        files=("page-1.txt", "page-2.txt"),
        observations=observations,
        inferred_age=66,
        inferred_sex="male",
    )


def _observation(
    *,
    source_file_index: int = 1,
    name: str = "空腹血糖",
    value: float = 6.8,
    evidence: str = "空腹血糖 6.8 mmol/L 3.9-6.1 H",
    status: str = "clear",
    default: str = "pending",
    issues: tuple[str, ...] = (),
) -> PendingObservation:
    return PendingObservation(
        source_file_index=source_file_index,
        source_filename=f"page-{source_file_index}.txt",
        source_page=1,
        name=name,
        model_value=value,
        model_unit="mmol/L",
        model_reference_low=3.9,
        model_reference_high=6.1,
        model_flag="high",
        evidence=evidence,
        extraction_status=status,  # type: ignore[arg-type]
        default_decision=default,  # type: ignore[arg-type]
        validation_issues=issues,
    )


def _store(tmp_path) -> tuple[ReportStore, ReportHandle]:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = ReportStore(database, ObjectStore(tmp_path / "objects"))
    handle = store.create(
        (ReportFile(b"first", "page-1.txt"), ReportFile(b"second", "page-2.txt"))
    )
    return store, handle


def test_report_files_and_raw_extraction_are_stored_in_order(tmp_path) -> None:
    store, handle = _store(tmp_path)
    first = _observation()
    second = _observation(source_file_index=2, name="尿酸", value=430, evidence="尿酸 430")
    store.save_extraction(handle.report_id, _pending(first, second))

    report = store.get(handle.report_id, handle.access_token)
    assert report["status"] == "pending_confirmation"
    assert report["extraction_model"] == "gpt-5.6-sol"
    assert [item["original_name"] for item in report["files"]] == [
        "page-1.txt",
        "page-2.txt",
    ]
    assert [item["model_value"] for item in report["observations"]] == [6.8, 430]
    assert "access_token_hash" not in report
    with pytest.raises(ReportAccessDenied):
        store.get(handle.report_id, "wrong-token")


def test_empty_report_file_is_not_persisted(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = ReportStore(database, ObjectStore(tmp_path / "objects"))
    with pytest.raises(ValueError, match="empty"):
        store.create((ReportFile(b"", "empty.txt"),))
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


def test_confirmation_preserves_model_values_and_stores_corrected_values(tmp_path) -> None:
    store, handle = _store(tmp_path)
    store.save_extraction(
        handle.report_id,
        _pending(
            _observation(),
            _observation(
                source_file_index=2,
                name="尿酸",
                value=430,
                evidence="尿酸 420 umol/L 210-420",
                status="ambiguous",
                default="excluded",
                issues=("模型标记为待核对",),
            ),
        ),
    )
    report = store.get(handle.report_id, handle.access_token)
    first, second = report["observations"]
    store.confirm(
        handle.report_id,
        handle.access_token,
        (
            ConfirmationInput(
                observation_id=first["id"],
                decision="confirmed",
                metric_code="fasting_glucose",
            ),
            ConfirmationInput(
                observation_id=second["id"],
                decision="corrected",
                metric_code="uric_acid",
                value=420,
                unit="umol/L",
                reference_low=210,
                reference_high=420,
            ),
        ),
    )

    confirmed = store.get(handle.report_id, handle.access_token)
    assert confirmed["status"] == "confirmed"
    assert confirmed["observations"][1]["model_value"] == 430
    assert confirmed["observations"][1]["final_value"] == 420
    assert confirmed["observations"][1]["final_metric_code"] == "uric_acid"


def test_confirmation_requires_every_row_and_source_evidence(tmp_path) -> None:
    store, handle = _store(tmp_path)
    invalid = _observation(
        evidence="报告中没有数值",
        status="ambiguous",
        default="excluded",
        issues=("指标数值缺少原文佐证",),
    )
    store.save_extraction(handle.report_id, _pending(invalid))
    report = store.get(handle.report_id, handle.access_token)
    observation_id = report["observations"][0]["id"]

    with pytest.raises(ValueError, match="every observation"):
        store.confirm(handle.report_id, handle.access_token, ())
    with pytest.raises(ValueError, match="source evidence"):
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
    store.confirm(
        handle.report_id,
        handle.access_token,
        (ConfirmationInput(observation_id=observation_id, decision="excluded"),),
    )
    assert store.get(handle.report_id, handle.access_token)["status"] == "confirmed"


def test_report_state_and_file_identity_cannot_be_replayed(tmp_path) -> None:
    store, handle = _store(tmp_path)
    pending = _pending(_observation())
    with pytest.raises(ValueError, match="do not match"):
        store.save_extraction(handle.report_id, replace(pending, files=("wrong.txt",)))
    store.save_extraction(handle.report_id, pending)
    with pytest.raises(ValueError, match="only queued"):
        store.save_extraction(handle.report_id, pending)


def test_report_extraction_claim_recovery_and_failure(tmp_path) -> None:
    store, handle = _store(tmp_path)
    assert store.claim_next_extraction() == handle.report_id
    assert store.get(handle.report_id, handle.access_token)["status"] == "extracted"
    assert store.recover_running_extractions() == 1
    assert store.get(handle.report_id, handle.access_token)["status"] == "uploaded"
    assert store.claim_next_extraction() == handle.report_id
    store.fail_extraction(handle.report_id, RuntimeError("provider detail"))
    failed = store.get(handle.report_id, handle.access_token)
    assert failed["status"] == "abandoned"
    assert failed["warnings"] == ["报告智能解读失败，请重新上传或稍后重试。"]
    assert "provider detail" not in str(failed)

    timeout = store.create(
        (ReportFile(b"first", "page-1.txt"), ReportFile(b"second", "page-2.txt"))
    )
    assert store.claim_next_extraction() == timeout.report_id
    store.fail_extraction(
        timeout.report_id,
        ReportExtractionUnavailable("报告解读超时，请稍后重试。"),
    )
    assert store.get(timeout.report_id, timeout.access_token)["warnings"] == [
        "报告解读超时，请稍后重试。"
    ]
