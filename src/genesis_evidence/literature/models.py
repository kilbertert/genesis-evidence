"""Canonical data models shared by literature source connectors."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from html import unescape
from html.parser import HTMLParser
from typing import Any


class SourceName(StrEnum):
    DOAJ = "doaj"
    EUROPE_PMC = "europe_pmc"
    CORE = "core"


class SourceAccess(StrEnum):
    APPROVED_OPEN = "approved_open"
    APPROVED_LICENSED = "approved_licensed"
    METADATA_ONLY = "metadata_only"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"


class RightsStatus(StrEnum):
    REDISTRIBUTABLE = "redistributable"
    INTERNAL_TDM_ONLY = "internal_tdm_only"
    METADATA_ONLY = "metadata_only"
    UNKNOWN = "unknown"


class FullTextFormat(StrEnum):
    JATS_XML = "jats_xml"
    XML = "xml"
    HTML = "html"
    PDF = "pdf"
    UNKNOWN = "unknown"


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi or None


class _TitleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize_title_text(value: str) -> str:
    parser = _TitleTextParser()
    parser.feed(unescape(value))
    parser.close()
    return " ".join("".join(parser.parts).split())


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", normalized, flags=re.UNICODE))


@dataclass(frozen=True, slots=True)
class FullTextCandidate:
    source: SourceName
    source_id: str
    url: str
    format: FullTextFormat
    access: SourceAccess
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    media_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["format"] = self.format.value
        data["access"] = self.access.value
        data["rights_status"] = self.rights_status.value
        return data


@dataclass(slots=True)
class PaperRecord:
    source: SourceName
    source_id: str
    title: str
    abstract: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    journal: str | None = None
    issns: tuple[str, ...] = ()
    publication_year: int | None = None
    authors: tuple[str, ...] = ()
    publication_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    is_open_access: bool | None = None
    full_text_candidates: tuple[FullTextCandidate, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.doi = normalize_doi(self.doi)
        self.pmid = self.pmid.strip() if self.pmid else None
        self.pmcid = self.pmcid.strip().upper() if self.pmcid else None
        self.title = normalize_title_text(self.title)

    @property
    def canonical_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.pmcid:
            return f"pmcid:{self.pmcid}"
        fingerprint = f"{normalize_title(self.title)}|{self.publication_year or ''}"
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        return f"title:{digest}"

    def to_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": self.source.value,
            "source_id": self.source_id,
            "canonical_key": self.canonical_key,
            "title": self.title,
            "abstract": self.abstract,
            "doi": self.doi,
            "pmid": self.pmid,
            "pmcid": self.pmcid,
            "journal": self.journal,
            "issns": list(self.issns),
            "publication_year": self.publication_year,
            "authors": list(self.authors),
            "publication_types": list(self.publication_types),
            "keywords": list(self.keywords),
            "is_open_access": self.is_open_access,
            "full_text_candidates": [
                candidate.to_dict() for candidate in self.full_text_candidates
            ],
        }
        if include_raw:
            data["raw"] = self.raw
        return data


@dataclass(frozen=True, slots=True)
class SearchPage:
    records: tuple[PaperRecord, ...]
    next_cursor: str | None = None
    total: int | None = None
