from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from genesis_evidence.reports.extraction import (
    HealthReportExtractor,
    ModelReportExtraction,
    ReportExtractionError,
    ReportExtractionUnavailable,
    ReportFile,
    ReportProviderResult,
)


def _observation(
    *,
    source_file_index: int = 1,
    source_page: int = 1,
    name: str = "空腹血糖",
    value: float = 6.8,
    unit: str = "mmol/L",
    reference_low: float | None = 3.9,
    reference_high: float | None = 6.1,
    flag: str = "high",
    evidence: str = "空腹血糖 6.80 mmol/L 3.9-6.1 H",
    extraction_status: str = "clear",
) -> dict[str, object]:
    return {
        "source_file_index": source_file_index,
        "source_page": source_page,
        "name": name,
        "value": value,
        "unit": unit,
        "reference_low": reference_low,
        "reference_high": reference_high,
        "flag": flag,
        "evidence": evidence,
        "extraction_status": extraction_status,
    }


def _report(
    observations: list[dict[str, object]],
    *,
    subject_consistency: str = "same",
    abnormality_audit: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "subject_consistency": subject_consistency,
        "inferred_age": 66,
        "inferred_sex": "male",
        "observations": observations,
        "abnormality_audit": abnormality_audit or [],
    }


def _response(report: dict[str, object], request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "resp-report-123",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(report)}],
                }
            ],
        },
    )


def test_responses_request_preserves_file_order_and_stops_at_confirmation() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured.update(json.loads(request.content))
        return _response(
            _report(
                [
                    _observation(),
                    _observation(
                        source_file_index=2,
                        name="血小板计数",
                        value=210,
                        unit="10^9/L",
                        reference_low=100,
                        reference_high=300,
                        flag="normal",
                        evidence="血小板计数 210 10^9/L 100-300 normal",
                    ),
                ]
            ),
            request,
        )

    extracted = asyncio.run(
        HealthReportExtractor(
            api_key="test-key",
            base_url="https://proxy.example/v1",
            max_bytes=1024,
            transport=httpx.MockTransport(handler),
        ).extract_files(
            (
                ReportFile(b"\xff\xd8\xffpage-one", "page-1.jpg"),
                ReportFile(b"\x89PNG\r\n\x1a\npage-two", "page-2.png"),
            )
        )
    )

    assert captured["url"] == "https://proxy.example/v1/responses"
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["store"] is False
    assert "diagnose" in captured["instructions"]
    output_format = captured["text"]["format"]  # type: ignore[index]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    content = captured["input"][0]["content"]  # type: ignore[index]
    images = [item for item in content if item["type"] == "input_image"]
    assert [item["detail"] for item in images] == ["original", "original"]
    assert "page-1.jpg" not in json.dumps(captured)
    assert "page-2.png" not in json.dumps(captured)
    assert extracted.status == "pending_confirmation"
    assert extracted.files == ("page-1.jpg", "page-2.png")
    assert [item.source_filename for item in extracted.observations] == [
        "page-1.jpg",
        "page-2.png",
    ]
    assert set(extracted.to_dict()).isdisjoint({"assessment", "findings", "diagnosis"})


def test_ambiguous_and_unverified_rows_remain_visible_but_default_to_excluded() -> None:
    class Provider:
        async def understand(self, files):
            del files
            return ReportProviderResult(
                provider="replacement",
                model="test-model",
                run_id="run-1",
                extraction=ModelReportExtraction.model_validate(
                    _report(
                        [
                            _observation(),
                            _observation(name="模糊指标", extraction_status="ambiguous"),
                            _observation(name="缺少佐证", evidence="报告没有对应数值"),
                            _observation(name="范围颠倒", reference_low=9, reference_high=2),
                        ]
                    )
                ),
            )

    extracted = asyncio.run(
        HealthReportExtractor(max_bytes=1024, provider=Provider()).extract_bytes(
            b"plain report", filename="report.txt"
        )
    )

    assert [item.name for item in extracted.observations] == [
        "空腹血糖",
        "模糊指标",
        "缺少佐证",
        "范围颠倒",
    ]
    assert [item.default_decision for item in extracted.observations] == [
        "pending",
        "excluded",
        "excluded",
        "excluded",
    ]
    assert extracted.observations[1].model_value == 6.8
    assert "模型标记为待核对" in extracted.observations[1].validation_issues
    assert "指标数值缺少原文佐证" in extracted.observations[2].validation_issues
    assert "指标参考范围无效" in extracted.observations[3].validation_issues


def test_deduplication_keeps_identical_values_from_different_files_and_pages() -> None:
    duplicate = _observation()
    extraction = ModelReportExtraction.model_validate(
        _report(
            [
                duplicate,
                _observation(source_page=2),
                _observation(source_file_index=2),
            ],
            abnormality_audit=[duplicate],
        )
    )

    class Provider:
        async def understand(self, files):
            del files
            return ReportProviderResult("replacement", "test-model", "run-1", extraction)

    result = asyncio.run(
        HealthReportExtractor(max_bytes=1024, provider=Provider()).extract_files(
            (ReportFile(b"one", "one.txt"), ReportFile(b"two", "two.txt"))
        )
    )

    assert [(item.source_file_index, item.source_page) for item in result.observations] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]


def test_different_subjects_are_rejected_and_uncertain_subject_warns() -> None:
    class Provider:
        def __init__(self, consistency: str) -> None:
            self.consistency = consistency

        async def understand(self, files):
            del files
            return ReportProviderResult(
                "replacement",
                "test-model",
                "run-1",
                ModelReportExtraction.model_validate(
                    _report([_observation()], subject_consistency=self.consistency)
                ),
            )

    with pytest.raises(ReportExtractionError, match="不属于同一人"):
        asyncio.run(
            HealthReportExtractor(max_bytes=1024, provider=Provider("different")).extract_bytes(
                b"plain", filename="report.txt"
            )
        )
    uncertain = asyncio.run(
        HealthReportExtractor(max_bytes=1024, provider=Provider("uncertain")).extract_bytes(
            b"plain", filename="report.txt"
        )
    )
    assert any("无法确认" in warning for warning in uncertain.warnings)


def test_extractor_rejects_bad_files_and_reports_provider_failures() -> None:
    extractor = HealthReportExtractor(api_key="test-key", max_bytes=1024, max_files=1)
    with pytest.raises(ReportExtractionError, match="为空"):
        asyncio.run(extractor.extract_bytes(b"", filename="report.txt"))
    with pytest.raises(ReportExtractionError, match="内容无效"):
        asyncio.run(extractor.extract_bytes(b"binary", filename="report.png"))
    with pytest.raises(ReportExtractionError, match="最多上传 1 个"):
        asyncio.run(
            extractor.extract_files(
                (ReportFile(b"first", "first.txt"), ReportFile(b"second", "second.txt"))
            )
        )

    without_key = HealthReportExtractor(api_key="", max_bytes=1024)
    with pytest.raises(ReportExtractionUnavailable, match="尚未配置"):
        asyncio.run(without_key.extract_bytes(b"plain", filename="report.txt"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    unavailable = HealthReportExtractor(
        api_key="test-key", max_bytes=1024, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ReportExtractionUnavailable, match="繁忙"):
        asyncio.run(unavailable.extract_bytes(b"plain", filename="report.txt"))
