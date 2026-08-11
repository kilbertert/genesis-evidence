"""CORE v3 connector, gated behind organisation-level licence confirmation."""

from __future__ import annotations

from ..http import HttpClient
from ..models import PaperRecord, SearchPage, SourceName
from ..policy import SourcePolicyRegistry
from .base import LiteratureConnector


class CoreConnector(LiteratureConnector):
    source = SourceName.CORE
    search_url = "https://api.core.ac.uk/v3/search/works"

    def __init__(
        self,
        http: HttpClient,
        policy: SourcePolicyRegistry,
        *,
        api_key: str,
    ) -> None:
        self._http = http
        self._policy = policy
        self._api_key = api_key.strip()

    def search(self, query: str, *, limit: int = 25, cursor: str | None = None) -> SearchPage:
        self._policy.require_search_allowed(self.source)
        if not self._api_key:
            raise RuntimeError("CORE_API_KEY is required for the CORE connector.")
        params: dict[str, object] = {"q": query, "limit": max(1, min(limit, 100))}
        if cursor:
            params["scrollId"] = cursor
        payload = self._http.get_json(
            self.search_url,
            params=params,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        results = payload.get("results", [])
        records = tuple(self._parse_record(item) for item in results if isinstance(item, dict))
        next_cursor = payload.get("scrollId")
        total_hits = payload.get("totalHits")
        return SearchPage(
            records=records,
            next_cursor=str(next_cursor) if next_cursor else None,
            total=_as_int(total_hits),
        )

    def _parse_record(self, item: dict[str, object]) -> PaperRecord:
        authors = (
            tuple(
                str(author.get("name", "")).strip()
                for author in item.get("authors", [])
                if isinstance(author, dict) and author.get("name")
            )
            if isinstance(item.get("authors"), list)
            else ()
        )
        journals = item.get("journals", [])
        first_journal = journals[0] if isinstance(journals, list) and journals else {}
        journal = first_journal if isinstance(first_journal, dict) else {}
        identifiers = item.get("identifiers", [])
        pmid = None
        pmcid = None
        if isinstance(identifiers, list):
            for identifier in identifiers:
                if not isinstance(identifier, dict):
                    continue
                identifier_type = str(identifier.get("type", "")).casefold()
                value = str(identifier.get("identifier", "")).strip()
                if identifier_type == "pmid":
                    pmid = value
                elif identifier_type == "pmcid":
                    pmcid = value

        document_types = item.get("documentType", [])
        if isinstance(document_types, str):
            document_types = [document_types]
        return PaperRecord(
            source=self.source,
            source_id=str(item.get("id", "")),
            title=str(item.get("title", "")).strip(),
            abstract=_optional_text(item.get("abstract")),
            doi=_optional_text(item.get("doi")),
            pmid=pmid,
            pmcid=pmcid,
            journal=_optional_text(journal.get("title")),
            issns=tuple(str(value) for value in journal.get("identifiers", []) if value)
            if isinstance(journal.get("identifiers"), list)
            else (),
            publication_year=_as_int(item.get("yearPublished")),
            authors=authors,
            publication_types=tuple(str(value) for value in document_types),
            is_open_access=True if item.get("fullText") or item.get("downloadUrl") else None,
            # CORE full-text candidates are deliberately not emitted until an
            # article-level rights resolver has approved the underlying work.
            full_text_candidates=(),
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
