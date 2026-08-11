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
