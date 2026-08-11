"""DOAJ v4 public search connector."""

from __future__ import annotations

from urllib.parse import quote

from ..http import HttpClient
from ..models import (
    FullTextCandidate,
    FullTextFormat,
    PaperRecord,
    RightsStatus,
    SearchPage,
    SourceAccess,
    SourceName,
)
from ..policy import SourcePolicyRegistry
from .base import LiteratureConnector


def _full_text_format(content_type: object) -> FullTextFormat:
    value = str(content_type or "").casefold()
    if "pdf" in value:
        return FullTextFormat.PDF
    if "xml" in value:
        return FullTextFormat.XML
    if "html" in value:
        return FullTextFormat.HTML
    return FullTextFormat.UNKNOWN


class DoajConnector(LiteratureConnector):
    source = SourceName.DOAJ
    base_url = "https://doaj.org/api/search/articles"

    def __init__(self, http: HttpClient, policy: SourcePolicyRegistry) -> None:
        self._http = http
        self._policy = policy

    def search(self, query: str, *, limit: int = 25, cursor: str | None = None) -> SearchPage:
        self._policy.require_search_allowed(self.source)
        page = max(1, int(cursor or "1"))
        page_size = max(1, min(limit, 100))
        # DOAJ's query-string-in-path API expects field separators and DOI
        # slashes to remain readable; percent-encoding the slash can trigger
        # a WAF rejection even though other query characters are accepted.
        encoded_query = quote(query, safe=':/()"')
        url = f"{self.base_url}/{encoded_query}"
        payload = self._http.get_json(url, params={"page": page, "pageSize": page_size})
        records = tuple(self._parse_record(item) for item in payload.get("results", []))
        next_cursor = str(page + 1) if payload.get("next") else None
        return SearchPage(
            records=records,
            next_cursor=next_cursor,
            total=_as_int(payload.get("total")),
        )

    def _parse_record(self, item: dict[str, object]) -> PaperRecord:
        bibjson = item.get("bibjson")
        bib = bibjson if isinstance(bibjson, dict) else {}
        identifiers = bib.get("identifier") if isinstance(bib.get("identifier"), list) else []
        doi = None
        issns: list[str] = []
        for identifier in identifiers:
            if not isinstance(identifier, dict):
                continue
            identifier_type = str(identifier.get("type", "")).casefold()
            value = str(identifier.get("id", "")).strip()
            if identifier_type == "doi":
                doi = value
            elif identifier_type in {"issn", "eissn", "pissn"} and value:
                issns.append(value)

        journal_value = bib.get("journal")
        journal = journal_value if isinstance(journal_value, dict) else {}
        for value in journal.get("issns", []) if isinstance(journal.get("issns"), list) else []:
            if value and str(value) not in issns:
                issns.append(str(value))

        authors: list[str] = []
        for author in bib.get("author", []) if isinstance(bib.get("author"), list) else []:
            if isinstance(author, dict) and author.get("name"):
                name = str(author["name"]).strip()
                if name and name not in authors:
                    authors.append(name)

        candidates: list[FullTextCandidate] = []
        for index, link in enumerate(
            bib.get("link", []) if isinstance(bib.get("link"), list) else []
        ):
            if not isinstance(link, dict) or link.get("type") != "fulltext":
                continue
            target = str(link.get("url", "")).strip()
            if not target:
                continue
            candidates.append(
                FullTextCandidate(
                    source=self.source,
                    source_id=f"{item.get('id', '')}:fulltext:{index}",
                    url=target,
                    format=_full_text_format(link.get("content_type")),
                    access=SourceAccess.MANUAL_REVIEW,
                    rights_status=RightsStatus.UNKNOWN,
                    media_type=(
                        str(link.get("content_type"))
                        if link.get("content_type") is not None
                        else None
                    ),
                )
            )

        keywords = (
            tuple(str(value).strip() for value in bib.get("keywords", []) if str(value).strip())
            if isinstance(bib.get("keywords"), list)
            else ()
        )

        return PaperRecord(
            source=self.source,
            source_id=str(item.get("id", "")),
            title=str(bib.get("title", "")).strip(),
            abstract=_optional_text(bib.get("abstract")),
            doi=doi,
            journal=_optional_text(journal.get("title")),
            issns=tuple(issns),
            publication_year=_as_int(bib.get("year")),
            authors=tuple(authors),
            keywords=keywords,
            is_open_access=True,
            full_text_candidates=tuple(candidates),
            raw=item,
        )


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
