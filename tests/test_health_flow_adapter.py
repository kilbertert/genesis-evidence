from genesis_evidence.integrations.health_flow import build_evidence_request


def _record(**overrides):
    value = {
        "metric_name": "空腹血糖",
        "metric_value": "6.8",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "page_number": 2,
        "source_file_index": 1,
        "evidence_text": "空腹血糖 6.8 mmol/L 参考范围 3.9-6.1 H",
        "source_id": "report-1/page-2",
        "bbox_normalized": [10, 20, 100, 120],
        "abnormal_flag": "normal",
    }
    value.update(overrides)
    return value


def test_adapter_requires_confirmation_before_crossing_boundary() -> None:
    result = build_evidence_request([_record()], confirmed=False)

    assert result.request.observations == []
    assert result.skipped == ({"record_index": "1", "reason": "confirmation_required"},)


def test_adapter_preserves_sources_and_recomputes_without_model_flag() -> None:
    result = build_evidence_request(
        [
            _record(),
            _record(
                metric_name="尿酸",
                metric_value="500",
                unit="umol/L",
                reference_range="200-420",
                page_number=3,
                source_file_index=2,
                evidence_text="尿酸 500 umol/L 参考范围 200-420 H",
                source_id="report-2/page-3",
            ),
        ],
        confirmed=True,
    )

    assert result.skipped == ()
    assert [item.metric_code for item in result.request.observations] == [
        "fasting_glucose",
        "uric_acid",
    ]
    assert result.request.observations[0].confirmation_status == "confirmed"
    assert result.request.observations[0].source_file_index == 1
    assert result.request.observations[0].source_page == 2
    assert result.request.observations[0].bbox_normalized == [10, 20, 100, 120]
    assert result.request.observations[1].source_file_index == 2
    assert result.request.observations[1].source_id == "report-2/page-3"


def test_adapter_drops_unknown_or_unverifiable_rows() -> None:
    result = build_evidence_request(
        [
            _record(metric_name="未知指标"),
            _record(
                metric_name="尿酸",
                metric_value="500",
                evidence_text="尿酸 500 umol/L",
            ),
        ],
        confirmed=True,
    )

    assert result.request.observations == []
    assert result.skipped == (
        {"record_index": "1", "reason": "unknown_metric"},
        {"record_index": "2", "reason": "missing_source_reference"},
    )


def test_adapter_does_not_reassign_invalid_file_index_to_file_one() -> None:
    result = build_evidence_request(
        [_record(source_file_index=0)],
        confirmed=True,
    )

    assert result.request.observations == []
    assert result.skipped == ({"record_index": "1", "reason": "invalid_source_file_index"},)


def test_adapter_skips_invalid_bbox_without_losing_other_rows() -> None:
    result = build_evidence_request(
        [
            _record(bbox_normalized=[0, 0, 1001, 10]),
            _record(
                metric_name="尿酸",
                metric_value="430",
                evidence_text="尿酸 430 mmol/L 参考范围 3.9-6.1 H",
            ),
        ],
        confirmed=True,
    )

    assert result.skipped == ({"record_index": "1", "reason": "invalid_bbox"},)
    assert [item.metric_code for item in result.request.observations] == ["uric_acid"]
