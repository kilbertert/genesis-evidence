"""GROBID client and safe TEI-to-article normalization."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

import httpx


class GrobidError(RuntimeError):
    """Raised when GROBID cannot produce a safe, usable TEI document."""


@dataclass(frozen=True, slots=True)
class StructuredSection:
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class StructuredTable:
    label: str
    caption: str
    text: str


@dataclass(frozen=True, slots=True)
class StructuredReference:
    title: str
    citation: str
    doi: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredArticle:
    title: str
    abstract: str
    sections: tuple[StructuredSection, ...]
    tables: tuple[StructuredTable, ...]
    references: tuple[StructuredReference, ...]
    identifiers: dict[str, str]
    source_format: str = "grobid_tei"
    parser: str = "genesis-grobid-tei-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GrobidClient:
    """Small client for a separately deployed GROBID service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8070",
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = 20 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_response_bytes = max_response_bytes
        self._owned_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def __enter__(self) -> GrobidClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def process_pdf(self, content: bytes, *, filename: str = "article.pdf") -> bytes:
        if not content.lstrip().startswith(b"%PDF-"):
            raise GrobidError("GROBID input is not a PDF.")
        url = f"{self._base_url}/api/processFulltextDocument"
        try:
            response = self._client.post(
                url,
                files={"input": (filename, content, "application/pdf")},
                data={
                    "consolidateHeader": "1",
                    "consolidateCitations": "0",
                    "includeRawCitations": "1",
                },
                headers={"Accept": "application/xml"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GrobidError(f"GROBID request failed: {exc}") from exc
        payload = response.content
        if len(payload) > self._max_response_bytes:
            raise GrobidError(f"GROBID response exceeds {self._max_response_bytes} bytes.")
        if not payload.strip():
            raise GrobidError("GROBID returned an empty response.")
        return payload


class GrobidTeiParser:
    def parse(self, payload: bytes) -> StructuredArticle:
        if b"<!ENTITY" in payload[:8192].upper():
            raise GrobidError("Entity declarations are not accepted in TEI XML.")
        payload = _strip_doctype(payload)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise GrobidError(f"Invalid GROBID TEI XML: {exc}") from exc
        if _local_name(root.tag).casefold() != "tei":
            raise GrobidError("GROBID response root is not TEI.")

        title = _main_title(root)
        abstract = _text_of_first(root, "abstract")
        sections = _sections(root)
        tables = _tables(root)
        references = _references(root)
        identifiers = _identifiers(root)
        if not title and not abstract and not sections:
            raise GrobidError("GROBID TEI contains no usable article text.")
        return StructuredArticle(
            title=title,
            abstract=abstract,
            sections=tuple(sections),
            tables=tuple(tables),
            references=tuple(references),
            identifiers=identifiers,
        )


def _main_title(root: ET.Element) -> str:
    candidates = [
        element
        for element in root.iter()
        if _local_name(element.tag) == "title" and _has_ancestor_path(root, element, "titleStmt")
    ]
    preferred = next(
        (
            element
            for element in candidates
            if element.attrib.get("type", "").casefold() == "main"
            or element.attrib.get("level", "").casefold() == "a"
        ),
        None,
    )
    selected = preferred if preferred is not None else (candidates[0] if candidates else None)
    return _normalized_text(selected)


def _sections(root: ET.Element) -> list[StructuredSection]:
    body = _find_first(root, "body")
    if body is None:
        return []
    sections: list[StructuredSection] = []
    for element in body.iter():
        if _local_name(element.tag) != "div":
            continue
        title = _text_of_direct_child(element, "head")
        paragraphs = [
            _normalized_text(child)
            for child in element
            if _local_name(child.tag) in {"p", "quote", "formula"}
        ]
        text = " ".join(value for value in paragraphs if value)
        if title or text:
            sections.append(StructuredSection(title=title, text=text))
    if not sections:
        text = _normalized_text(body)
        if text:
            sections.append(StructuredSection(title="Body", text=text))
    return sections


def _tables(root: ET.Element) -> list[StructuredTable]:
    tables: list[StructuredTable] = []
    for figure in root.iter():
        if _local_name(figure.tag) != "figure":
            continue
        if figure.attrib.get("type", "").casefold() != "table":
            continue
        tables.append(
            StructuredTable(
                label=_text_of_direct_child(figure, "label"),
                caption=_text_of_direct_child(figure, "head")
                or _text_of_direct_child(figure, "figDesc"),
                text=_text_of_first(figure, "table"),
            )
        )
    return tables


def _references(root: ET.Element) -> list[StructuredReference]:
    references: list[StructuredReference] = []
    bibliographies = [element for element in root.iter() if _local_name(element.tag) == "listBibl"]
    for bibliography in bibliographies:
        for entry in bibliography:
            if _local_name(entry.tag) not in {"biblStruct", "bibl"}:
                continue
            title = _text_of_first(entry, "title")
            doi = next(
                (
                    _normalized_text(element)
                    for element in entry.iter()
                    if _local_name(element.tag) == "idno"
                    and element.attrib.get("type", "").casefold() == "doi"
                ),
                None,
            )
            references.append(
                StructuredReference(
                    title=title,
                    citation=_normalized_text(entry),
                    doi=doi or None,
                )
            )
    return references


def _identifiers(root: ET.Element) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for element in root.iter():
        if _local_name(element.tag) != "idno":
            continue
        kind = element.attrib.get("type", "").strip().casefold()
        value = _normalized_text(element)
        if kind and value and kind not in identifiers:
            identifiers[kind] = value
    return identifiers


def _has_ancestor_path(root: ET.Element, target: ET.Element, ancestor_name: str) -> bool:
    for parent in root.iter():
        if _local_name(parent.tag) != ancestor_name:
            continue
        if any(descendant is target for descendant in parent.iter()):
            return True
    return False


def _find_first(root: ET.Element, local_name: str) -> ET.Element | None:
    return next(
        (element for element in root.iter() if _local_name(element.tag) == local_name),
        None,
    )


def _text_of_first(root: ET.Element, local_name: str) -> str:
    return _normalized_text(_find_first(root, local_name))


def _text_of_direct_child(root: ET.Element, local_name: str) -> str:
    child = next(
        (element for element in root if _local_name(element.tag) == local_name),
        None,
    )
    return _normalized_text(child)


def _normalized_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_doctype(payload: bytes) -> bytes:
    upper = payload.upper()
    start = upper.find(b"<!DOCTYPE")
    if start < 0:
        return payload
    match = re.search(rb"<!DOCTYPE[^>]*>", payload[start:], flags=re.IGNORECASE)
    if match is None:
        raise GrobidError("Unterminated TEI DOCTYPE declaration.")
    declaration = match.group(0)
    if b"[" in declaration or b"]" in declaration:
        raise GrobidError("Internal TEI DTD subsets are not accepted.")
    end = start + match.end()
    return payload[:start] + payload[end:]
