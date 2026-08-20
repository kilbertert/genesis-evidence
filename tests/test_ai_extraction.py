from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from genesis_evidence.literature.ai_extraction import (
    ArkPaperAnalyzer,
    ConsistencyReport,
    PaperAnalysisError,
    PaperExtraction,
    _normalize_consistency_payload,
    _streamed_completion,
)
from genesis_evidence.literature.models import PaperRecord, SourceName


def _extraction(*, inference: str = "associational") -> dict[str, object]:
    return {
        "summary": "This cohort study evaluated vitamin D status and frailty.",
        "research_question": "Is vitamin D status associated with frailty?",
        "study_design": "cohort_study",
        "population": ["Adults aged 60 years and older"],
        "countries_and_centers": "Not reported",
        "recruitment_period": "Not reported",
        "registration_ids": [],
        "protocol_status": "Not reported",
        "statistical_analysis_plan_status": "Not reported",
        "ethics": "Not reported",
        "funding": "Not reported",
        "conflicts_of_interest": "Not reported",
        "condition_candidates": [
            {
                "condition_code": "COND_VITAMIN_D_DEFICIENCY",
                "rationale": "The study measures vitamin D status.",
                "evidence": "Serum 25-hydroxyvitamin D was measured.",
                "locator": "Methods",
            }
        ],
        "directly_reported_symptoms": [],
        "studied_approach": ["Vitamin D status"],
        "comparator": ["Higher versus lower vitamin D status"],
        "outcomes": ["Frailty"],
        "limitations": ["Observational design"],
        "claims": [
            {
                "text": "Lower vitamin D status was associated with frailty.",
                "evidence": "Lower 25(OH)D was associated with higher frailty prevalence.",
                "locator": "Results",
                "claim_type": "association",
                "inference": inference,
                "population": "Adults aged 60 years and older",
                "baseline_nutrient_status": "Serum 25-hydroxyvitamin D measured",
                "ingredient_name": "Vitamin D",
                "ingredient_form": "25-hydroxyvitamin D status",
                "dose": "Not applicable",
                "comparator": "Higher versus lower vitamin D status",
                "outcome": "Frailty prevalence",
                "timepoint": "Not reported",
                "effect_estimate": "Higher frailty prevalence",
                "statistical_details": "Not reported",
            }
        ],
    }


def _stream(run_id: str, content: object) -> httpx.Response:
    event = {"id": run_id, "choices": [{"delta": {"content": json.dumps(content)}}]}
    return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")


def test_observational_study_cannot_emit_causal_claim() -> None:
    with pytest.raises(ValidationError, match="cannot produce causal"):
        PaperExtraction.model_validate(_extraction(inference="causal"))


def test_consistency_report_keeps_large_auditable_difference_set() -> None:
    report = ConsistencyReport.model_validate(
        {
            "verdict": "needs_review",
            "issues": [
                {
                    "field": f"claims[{index}]",
                    "severity": "medium",
                    "message": "Independent extraction differs.",
                    "evidence": "Source excerpt retained for audit.",
                }
                for index in range(81)
            ],
        }
    )

    assert len(report.issues) == 81


def test_normalize_consistency_payload_accepts_legacy_issue_field() -> None:
    report = ConsistencyReport.model_validate(
        _normalize_consistency_payload(
            {
                "verdict": "needs_review",
                "issues": [{"field": "claims", "issue": "The two claims differ."}],
            }
        )
    )
    assert report.issues[0].severity == "medium"
    assert report.issues[0].message == "The two claims differ."


def test_ark_analyzer_runs_extraction_then_consistency_check() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert request.url.path == "/api/v3/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert body["model"] == "deepseek-v4-flash-ga-260731"
        assert body["max_tokens"] == (16_384 if calls < 3 else 8_192)
        assert body["temperature"] == 0
        assert body["thinking"] == {"type": "disabled"}
        if calls == 1:
            source = json.loads(body["messages"][1]["content"])
            assert len(source["condition_catalog"]) == 12
        assert body["stream"] is True
        content = _extraction() if calls < 3 else {"verdict": "consistent", "issues": []}
        encoded = json.dumps(content)
        return httpx.Response(
            200,
            text=(
                f'data: {{"id":"run-{calls}","choices":[{{"delta":'
                f'{{"content":{json.dumps(encoded[:20])}}}}}]}}\n\n'
                f'data: {{"id":"run-{calls}","choices":[{{"delta":'
                f'{{"content":{json.dumps(encoded[20:])}}}}}]}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    analyzer = ArkPaperAnalyzer(
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    result = analyzer.analyze(
        PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
        {
            "abstract": "Serum 25-hydroxyvitamin D was measured.",
            "sections": [
                {
                    "title": "Results",
                    "text": "Lower 25(OH)D was associated with higher frailty prevalence.",
                }
            ],
        },
    )
    assert calls == 3
    assert result.extraction.study_design == "cohort_study"
    assert result.consistency.verdict == "consistent"
    assert result.extraction_run_id == "run-1"
    assert result.second_run_id == "run-2"
    assert result.check_run_id == "run-3"


def test_ark_analyzer_requires_server_key() -> None:
    with pytest.raises(PaperAnalysisError, match="paper analysis API key"):
        ArkPaperAnalyzer(api_key="").analyze(
            PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Title"),
            {"abstract": "Text"},
        )


def test_differing_independent_extractions_cannot_be_marked_consistent() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = _extraction()
        if calls == 2:
            content["summary"] = "The independent pass found a different summary."
        if calls == 3:
            content = {"verdict": "consistent", "issues": []}
        return _stream(f"run-{calls}", content)

    result = ArkPaperAnalyzer(api_key="secret", transport=httpx.MockTransport(handler)).analyze(
        PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
        {
            "abstract": "Serum 25-hydroxyvitamin D was measured.",
            "sections": [
                {
                    "title": "Results",
                    "text": "Lower 25(OH)D was associated with higher frailty prevalence.",
                }
            ],
        },
    )
    assert result.consistency.verdict == "needs_review"
    assert result.consistency.issues[0].field == "dual_extraction"


def test_invalid_source_evidence_gets_one_channel_local_correction() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            content = _extraction()
            content["claims"][0]["evidence"] = "A sentence that is not in the paper."
        elif calls == 2:
            assert "不可信数据" in body["messages"][0]["content"]
            correction = json.loads(body["messages"][1]["content"])
            assert "invalid_output" in correction
            assert "claims[0].evidence" in correction["validation_error"]
            content = _extraction()
        elif calls == 3:
            content = _extraction()
        else:
            content = {"verdict": "consistent", "issues": []}
        return _stream(f"run-{calls}", content)

    result = ArkPaperAnalyzer(api_key="secret", transport=httpx.MockTransport(handler)).analyze(
        PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
        {
            "abstract": "Serum 25-hydroxyvitamin D was measured.",
            "sections": [
                {
                    "title": "Results",
                    "text": "Lower 25(OH)D was associated with higher frailty prevalence.",
                }
            ],
        },
    )
    assert calls == 4
    assert result.extraction_run_id == "run-2"
    assert result.second_run_id == "run-3"


def test_oversized_extraction_uses_bounded_correction() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        del request
        if calls == 1:
            content = _extraction()
            content["claims"] = [*content["claims"], *content["claims"] * 50]
        elif calls in {2, 3}:
            content = _extraction()
        else:
            content = {"verdict": "consistent", "issues": []}
        return _stream(f"run-{calls}", content)

    result = ArkPaperAnalyzer(
        api_key="secret", transport=httpx.MockTransport(handler)
    ).analyze(
        PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
        {
            "abstract": "Serum 25-hydroxyvitamin D was measured.",
            "sections": [
                {
                    "title": "Results",
                    "text": "Lower 25(OH)D was associated with higher frailty prevalence.",
                }
            ],
        },
    )

    assert calls == 4
    assert result.extraction_run_id == "run-2"
    assert len(result.extraction.claims) == 1


def test_ark_analyzer_rejects_evidence_missing_from_full_text_with_field_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _stream("extract-1", _extraction())

    with pytest.raises(
        PaperAnalysisError,
        match=r'claims\[0\]\.evidence="Lower 25\(OH\)D was associated',
    ):
        ArkPaperAnalyzer(api_key="secret", transport=httpx.MockTransport(handler)).analyze(
            PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
            {"abstract": "This text contains none of the cited excerpts."},
        )


def test_streamed_completion_skips_chunks_without_choices() -> None:
    body = (
        'data: {"id":"run-1","choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
        'data: {"id":"run-1","choices":[]}\n\n'
        'data: {"id":"run-1","choices":[{"delta":{"content":"{\\"answer\\":1}"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    content, run_id = _streamed_completion(httpx.Response(200, text=body))
    assert run_id == "run-1"
    assert json.loads(content) == {"answer": 1}


def test_streamed_completion_honors_deadline() -> None:
    with pytest.raises(TimeoutError, match="deadline"):
        _streamed_completion(httpx.Response(200, text="data: {}\n\n"), deadline=0)


def test_invalid_consistency_report_preserves_validation_reason() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        del request
        content = _extraction() if calls < 3 else {"verdict": "consistent"}
        return _stream(f"run-{calls}", content)

    with pytest.raises(PaperAnalysisError, match=r"issues\s+Field required"):
        ArkPaperAnalyzer(api_key="secret", transport=httpx.MockTransport(handler)).analyze(
            PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
            {
                "abstract": "Serum 25-hydroxyvitamin D was measured.",
                "sections": [
                    {
                        "title": "Results",
                        "text": (
                            "Lower 25(OH)D was associated with higher frailty prevalence."
                        ),
                    }
                ],
            },
        )


def test_invalid_consistency_report_uses_bounded_correction() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        del request
        if calls < 3:
            content = _extraction()
        elif calls == 3:
            content = {"verdict": "needs_review"}
        else:
            content = {
                "verdict": "needs_review",
                "issues": [
                    {
                        "field": "claims[0].effect_estimate",
                        "severity": "medium",
                        "message": "Effect estimates differ.",
                        "evidence": "Lower 25(OH)D was associated with higher frailty prevalence.",
                    }
                ],
            }
        return _stream(f"run-{calls}", content)

    result = ArkPaperAnalyzer(
        api_key="secret", transport=httpx.MockTransport(handler)
    ).analyze(
        PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
        {
            "abstract": "Serum 25-hydroxyvitamin D was measured.",
            "sections": [
                {
                    "title": "Results",
                    "text": "Lower 25(OH)D was associated with higher frailty prevalence.",
                }
            ],
        },
    )

    assert calls == 4
    assert result.check_run_id == "run-4"
    assert result.consistency.verdict == "needs_review"


def test_from_env_honors_ark_max_tokens(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MAX_TOKENS", "32000")
    assert ArkPaperAnalyzer.from_env()._max_tokens == 32000


def test_from_env_honors_provider_max_tokens(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_AI_API_KEY", "k")
    monkeypatch.setenv("PAPER_AI_MAX_TOKENS", "32000")
    monkeypatch.setenv("PAPER_AI_TIMEOUT_SECONDS", "420")
    monkeypatch.delenv("ARK_MAX_TOKENS", raising=False)
    analyzer = ArkPaperAnalyzer.from_env()
    assert analyzer._max_tokens == 32000
    assert analyzer._timeout == 420


def test_from_env_reads_openai_compatible_provider_key_from_csv(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "provider.csv"
    key_file.write_text(
        "id,example\napiKey,csv-secret\nopenAiCompatible,https://provider.invalid/v1\n",
        encoding="utf-8",
    )
    for name in (
        "PAPER_AI_API_KEY",
        "PAPER_AI_BASE_URL",
        "PAPER_AI_MODEL",
        "ARK_API_KEY",
        "ARK_BASE_URL",
        "ARK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_AI_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("PAPER_AI_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("PAPER_AI_MODEL", "deepseek-v4-flash-0731")

    analyzer = ArkPaperAnalyzer.from_env()

    assert analyzer.api_key_configured
    assert analyzer._api_key == "csv-secret"
    assert analyzer._endpoint == "https://provider.invalid/v1/chat/completions"
    assert analyzer.model == "deepseek-v4-flash-0731"


def test_complete_endpoint_keeps_full_chat_completions_url() -> None:
    analyzer = ArkPaperAnalyzer(
        api_key="secret",
        endpoint="https://provider.invalid/v1/chat/completions",
    )
    assert analyzer._endpoint == "https://provider.invalid/v1/chat/completions"
