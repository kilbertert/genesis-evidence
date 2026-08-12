from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from genesis_evidence.literature.ai_extraction import (
    ArkPaperAnalyzer,
    PaperAnalysisError,
    PaperExtraction,
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


def test_observational_study_cannot_emit_causal_claim() -> None:
    with pytest.raises(ValidationError, match="cannot produce causal"):
        PaperExtraction.model_validate(_extraction(inference="causal"))


def test_ark_analyzer_runs_extraction_then_consistency_check() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert request.url.path == "/api/v3/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert body["model"] == "deepseek-v4-flash-ga-260731"
        if calls == 1:
            source = json.loads(body["messages"][1]["content"])
            assert len(source["condition_catalog"]) == 12
        content = _extraction() if calls < 3 else {"verdict": "consistent", "issues": []}
        return httpx.Response(
            200,
            json={
                "id": f"run-{calls}",
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
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
    with pytest.raises(PaperAnalysisError, match="ARK_API_KEY"):
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
        return httpx.Response(
            200,
            json={
                "id": f"run-{calls}",
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
        )

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
    assert result.consistency.verdict == "needs_review"
    assert result.consistency.issues[0].field == "dual_extraction"


def test_ark_analyzer_rejects_evidence_missing_from_full_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "id": "extract-1",
                "choices": [{"message": {"content": json.dumps(_extraction())}}],
            },
        )

    with pytest.raises(PaperAnalysisError, match="not present"):
        ArkPaperAnalyzer(
            api_key="secret", transport=httpx.MockTransport(handler)
        ).analyze(
            PaperRecord(SourceName.EUROPE_PMC, "MED:1", "Vitamin D and frailty"),
            {"abstract": "This text contains none of the cited excerpts."},
        )
