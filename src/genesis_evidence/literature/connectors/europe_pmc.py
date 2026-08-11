"""Europe PMC REST connector with open-access JATS candidates."""

from __future__ import annotations

from ..http import HttpClient
from ..jats import classify_license
from ..models import (
    FullTextCandidate,
    FullTextFormat,
    PaperRecord,
    SearchPage,
    SourceAccess,
    SourceName,
)
from ..policy import SourcePolicyRegistry
from .base import LiteratureConnector


class EuropePmcConnector(LiteratureConnector):
    source = SourceName.EUROPE_PMC
    search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    full_text_base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(
        self,
        http: HttpClient,
        policy: SourcePolicyRegistry,
        *,
        only_open_access: bool = True,
    ) -> None:
        self._http = http
        self._policy = policy
        self._only_open_access = only_open_access

    def search(self, query: str, *, limit: int = 25, cursor: str | None = None) -> SearchPage:
        self._policy.require_search_allowed(self.source)
        effective_query = query
        if self._only_open_access and "OPEN_ACCESS:" not in query.upper():
            effective_query = f"({query}) AND OPEN_ACCESS:Y"
        params = {
            "query": effective_query,
            "resultType": "core",
            "format": "json",
            "pageSize": max(1, min(limit, 1000)),
        }
        if cursor:
            params["cursorMark"] = cursor
        payload = self._http.get_json(self.search_url, params=params)
        result_list = payload.get("resultList")
        results = result_list.get("result", []) if isinstance(result_list, dict) else []
        records = tuple(self._parse_record(item) for item in results if isinstance(item, dict))
        next_cursor = payload.get("nextCursorMark")
        return SearchPage(
            records=records,
            next_cursor=str(next_cursor) if next_cursor else None,
            total=_as_int(payload.get("hitCount")),
        )

    def _parse_record(self, item: dict[str, object]) -> PaperRecord:
        pmcid = _optional_text(item.get("pmcid"))
        is_oa = str(item.get("isOpenAccess", "")).upper() == "Y"
        in_epmc = str(item.get("inEPMC", "")).upper() == "Y"
        licence_hint = classify_license(str(item.get("license", "")))
        candidates: tuple[FullTextCandidate, ...] = ()
        if pmcid and is_oa and in_epmc:
            candidates = (
                FullTextCandidate(
                    source=self.source,
                    source_id=pmcid,
                    url=f"{self.full_text_base_url}/{pmcid}/fullTextXML",
                    format=FullTextFormat.JATS_XML,
                    access=SourceAccess.APPROVED_OPEN,
                    rights_status=licence_hint.rights_status,
                    media_type="application/xml",
                ),
            )

        author_list = item.get("authorList")
        authors_raw = author_list.get("author", []) if isinstance(author_list, dict) else []
        authors = tuple(
            str(author.get("fullName") or author.get("lastName") or "").strip()
            for author in authors_raw
            if isinstance(author, dict) and (author.get("fullName") or author.get("lastName"))
        )

        publication_types_value = item.get("pubTypeList")
        publication_types_raw = (
            publication_types_value.get("pubType", [])
            if isinstance(publication_types_value, dict)
            else []
        )
        if isinstance(publication_types_raw, str):
            publication_types_raw = [publication_types_raw]

        keywords_value = item.get("keywordList")
        keywords_raw = keywords_value.get("keyword", []) if isinstance(keywords_value, dict) else []
        if isinstance(keywords_raw, str):
            keywords_raw = [keywords_raw]

        source_id = f"{item.get('source', '')}:{item.get('id') or item.get('extId') or pmcid or ''}"
        return PaperRecord(
            source=self.source,
            source_id=source_id,
            title=str(item.get("title", "")).strip(),
            abstract=_optional_text(item.get("abstractText")),
            doi=_optional_text(item.get("doi")),
            pmid=_optional_text(item.get("pmid")),
            pmcid=pmcid,
            journal=_optional_text(item.get("journalTitle")),
            issns=_collect_issns(item),
            publication_year=_as_int(item.get("pubYear")),
            authors=authors,
            publication_types=tuple(str(value) for value in publication_types_raw),
            keywords=tuple(str(value) for value in keywords_raw),
            is_open_access=is_oa,
            full_text_candidates=candidates,
            raw=item,
        )


def _collect_issns(item: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    journal_info = item.get("journalInfo")
    if isinstance(journal_info, dict):
        journal = journal_info.get("journal")
        if isinstance(journal, dict):
            for key in ("issn", "essn"):
                value = _optional_text(journal.get(key))
                if value and value not in values:
                    values.append(value)
    return tuple(values)


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
