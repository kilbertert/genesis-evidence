from __future__ import annotations

import pytest

from genesis_evidence.literature.models import (
    FullTextCandidate,
    FullTextFormat,
    PaperRecord,
    RightsStatus,
    SourceAccess,
    SourceName,
)
from genesis_evidence.literature.policy import SourcePolicyError, SourcePolicyRegistry


def test_sources_are_limited_to_the_active_acquisition_connectors() -> None:
    assert {source.value for source in SourceName} == {"doaj", "europe_pmc", "core"}


def test_paper_record_normalizes_doi_and_uses_it_for_deduplication() -> None:
    record = PaperRecord(
        source=SourceName.DOAJ,
        source_id="article-1",
        title="  Protein   and Sarcopenia  ",
        doi="https://doi.org/10.1000/Example",
    )

    assert record.title == "Protein and Sarcopenia"
    assert record.doi == "10.1000/example"
    assert record.canonical_key == "doi:10.1000/example"


def test_policy_blocks_sci_hub_even_if_candidate_claims_open_access() -> None:
    candidate = FullTextCandidate(
        source=SourceName.EUROPE_PMC,
        source_id="unsafe",
        url="https://sci-hub.mobi/paper.pdf",
        format=FullTextFormat.PDF,
        access=SourceAccess.APPROVED_OPEN,
        rights_status=RightsStatus.REDISTRIBUTABLE,
    )

    with pytest.raises(SourcePolicyError, match="Blocked source URL"):
        SourcePolicyRegistry().require_download_allowed(candidate)


def test_core_is_disabled_until_licence_is_confirmed() -> None:
    with pytest.raises(SourcePolicyError, match="Organisation-level CORE licence"):
        SourcePolicyRegistry(core_license_confirmed=False).require_search_allowed(SourceName.CORE)
