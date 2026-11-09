from __future__ import annotations

import httpx

from genesis_evidence.literature.connectors import DoajConnector, EuropePmcConnector
from genesis_evidence.literature.http import HostRateLimiter, HttpClient
from genesis_evidence.literature.models import FullTextFormat, SourceAccess
from genesis_evidence.literature.policy import SourcePolicyRegistry


def _http_with_handler(handler: httpx.MockTransport) -> HttpClient:
    client = httpx.Client(transport=handler)
    return HttpClient(
        user_agent="test",
        client=client,
        max_retries=0,
        rate_limiter=HostRateLimiter({}),
    )


def test_doaj_connector_normalizes_metadata_but_does_not_approve_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/api/search/articles/")
        return httpx.Response(
            200,
            json={
                "total": 1,
                "results": [
                    {
                        "id": "doaj-1",
                        "bibjson": {
                            "title": "Nutrition and healthy ageing",
                            "abstract": "An abstract",
                            "year": "2025",
                            "identifier": [
                                {"type": "doi", "id": "10.1000/TEST"},
                                {"type": "eissn", "id": "1234-5678"},
                            ],
                            "journal": {"title": "Nutrition Journal"},
                            "author": [{"name": "Alice Example"}],
                            "link": [
                                {
                                    "type": "fulltext",
                                    "content_type": "PDF",
                                    "url": "https://publisher.example/article.pdf",
                                }
                            ],
                        },
                    }
                ],
            },
        )

    http = _http_with_handler(httpx.MockTransport(handler))
    page = DoajConnector(http, SourcePolicyRegistry()).search("nutrition", limit=1)

    assert page.total == 1
    record = page.records[0]
    assert record.doi == "10.1000/test"
    assert record.issns == ("1234-5678",)
    assert record.full_text_candidates[0].format == FullTextFormat.PDF
    assert record.full_text_candidates[0].access == SourceAccess.MANUAL_REVIEW


def test_europe_pmc_connector_emits_open_jats_candidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["query"].endswith("AND OPEN_ACCESS:Y")
        return httpx.Response(
            200,
            json={
                "hitCount": 1,
                "nextCursorMark": "next-page",
                "resultList": {
                    "result": [
                        {
                            "source": "MED",
                            "id": "123",
                            "pmid": "123",
                            "pmcid": "PMC123",
                            "doi": "10.1000/pmc",
                            "title": "Protein supplementation in older adults",
                            "abstractText": "An abstract",
                            "pubYear": "2024",
                            "journalTitle": "Example Medical Journal",
                            "isOpenAccess": "Y",
                            "inEPMC": "Y",
                            "license": "cc by",
                            "authorList": {"author": [{"fullName": "Alice Example"}]},
                            "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
                        }
                    ]
                },
            },
        )

    http = _http_with_handler(httpx.MockTransport(handler))
    page = EuropePmcConnector(http, SourcePolicyRegistry()).search(
        'TITLE_ABS:"sarcopenia"', limit=1
    )

    assert page.next_cursor == "next-page"
    record = page.records[0]
    assert record.pmcid == "PMC123"
    assert record.publication_types == ("Randomized Controlled Trial",)
    candidate = record.full_text_candidates[0]
    assert candidate.format == FullTextFormat.JATS_XML
    assert candidate.access == SourceAccess.APPROVED_OPEN
    assert candidate.rights_status.value == "redistributable"
    assert candidate.url.endswith("/PMC123/fullTextXML")


def test_request_budget_is_a_wall_clock_deadline_not_per_attempt() -> None:
    """#270 follow-up: the integrity budget must cap the whole call.

    httpx applies a scalar `timeout` per operation per attempt — retries and
    backoff each got a fresh 60s under the first cut of the fix, so one bad
    upstream could stall a paper for minutes. The budget now degrades a
    deadline across attempts, waits, and retries.
    """

    import pytest

    from genesis_evidence.literature.http import HttpRequestError

    now = [0.0]
    slept: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = HttpClient(
        user_agent="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=10,
        max_retry_wait_seconds=60.0,
        sleep=fake_sleep,
        clock=fake_clock,
        rate_limiter=HostRateLimiter({}),
    )

    with pytest.raises(HttpRequestError, match="budget of 5.0s"):
        client.request("GET", "https://example.test/", timeout_seconds=5.0)

    assert now[0] <= 5.0, "retries and backoffs can never overshoot the budget"
