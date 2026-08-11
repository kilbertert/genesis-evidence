from __future__ import annotations

from typing import Any

import pytest

from genesis_evidence.literature.http import HttpRequestError
from genesis_evidence.literature.integrity import (
    CrossrefIntegrityProvider,
    EuropePmcIntegrityProvider,
    IntegrityStatus,
    PublicationIntegrityChecker,
)


class FakeHttp:
    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del headers
        if "crossref" in url:
            return {
                "message": {
                    "update-to": [
                        {
                            "type": "correction",
                            "DOI": "10.1000/correction",
                            "label": "Correction",
                        }
                    ]
                }
            }
        assert params and params["query"] == "EXT_ID:123 AND SRC:MED"
        return {
            "resultList": {
                "result": [
                    {
                        "pmid": "123",
                        "pubTypeList": {"pubType": ["Journal Article", "Retracted Publication"]},
                    }
                ]
            }
        }


def test_integrity_checker_uses_most_severe_cross_source_status() -> None:
    http = FakeHttp()
    checker = PublicationIntegrityChecker(
        (CrossrefIntegrityProvider(http), EuropePmcIntegrityProvider(http))  # type: ignore[arg-type]
    )

    result = checker.check({"doi": "10.1000/original", "pmid": "123"})

    assert result.status == IntegrityStatus.RETRACTED
    assert result.checked_sources == ("crossref", "europe_pmc")
    assert {finding.kind for finding in result.findings} == {"correction", "retraction"}


def test_integrity_checker_is_unknown_without_supported_identifiers() -> None:
    http = FakeHttp()
    result = PublicationIntegrityChecker(
        (CrossrefIntegrityProvider(http), EuropePmcIntegrityProvider(http))  # type: ignore[arg-type]
    ).check({"title": "No identifiers"})

    assert result.status == IntegrityStatus.UNKNOWN
    assert result.checked_sources == ()


def test_crossref_not_found_falls_back_to_europe_pmc() -> None:
    class CrossrefNotFoundHttp(FakeHttp):
        def get_json(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            if "crossref" in url:
                raise HttpRequestError("Crossref DOI not found", status_code=404)
            del headers
            assert params and params["query"] == "EXT_ID:42032996 AND SRC:MED"
            return {
                "resultList": {
                    "result": [
                        {
                            "pmid": "42032996",
                            "pubTypeList": {"pubType": ["Journal Article"]},
                        }
                    ]
                }
            }

    result = PublicationIntegrityChecker(
        (
            CrossrefIntegrityProvider(CrossrefNotFoundHttp()),  # type: ignore[arg-type]
            EuropePmcIntegrityProvider(CrossrefNotFoundHttp()),  # type: ignore[arg-type]
        )
    ).check(
        {
            "doi": "10.11817/j.issn.1672-7347.2025.250184",
            "pmid": "42032996",
        }
    )

    assert result.status == IntegrityStatus.CLEAR
    assert result.checked_sources == ("europe_pmc",)


def test_crossref_transient_failure_is_not_suppressed() -> None:
    class CrossrefUnavailableHttp(FakeHttp):
        def get_json(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            del url, params, headers
            raise HttpRequestError("Crossref unavailable", status_code=503)

    with pytest.raises(HttpRequestError, match="unavailable"):
        CrossrefIntegrityProvider(CrossrefUnavailableHttp()).check({"doi": "10.1000/example"})
