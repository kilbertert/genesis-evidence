"""Safe-enough first-pass JATS parsing and licence classification."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

from .models import RightsStatus


class JatsParseError(RuntimeError):
    """Raised when an XML payload is unsafe or is not a JATS article."""


@dataclass(frozen=True, slots=True)
class LicenseInfo:
    code: str | None
    text: str
    url: str | None
    rights_status: RightsStatus
    commercial_use_allowed: bool | None
    derivatives_allowed: bool | None


@dataclass(frozen=True, slots=True)
class JatsSection:
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class JatsDocument:
    title: str
    abstract: str
    sections: tuple[JatsSection, ...]
    statements: tuple[JatsSection, ...]
    license: LicenseInfo

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["license"]["rights_status"] = self.license.rights_status.value
        return data


class JatsParser:
    def parse(self, payload: bytes) -> JatsDocument:
        upper_prefix = payload[:4096].upper()
        if b"<!ENTITY" in upper_prefix:
            raise JatsParseError("Entity declarations are not accepted.")
        payload = _strip_external_doctype(payload)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise JatsParseError(f"Invalid XML: {exc}") from exc
        if _local_name(root.tag) != "article":
            raise JatsParseError("XML root is not a JATS article.")

        title = _text_of_first(root, "article-title")
        abstract = _text_of_first(root, "abstract")
        sections: list[JatsSection] = []
        body = _find_first(root, "body")
        if body is not None:
            for section in [element for element in body if _local_name(element.tag) == "sec"]:
                section_title = _text_of_direct_child(section, "title")
                section_text = _normalized_text(section)
                if section_title and section_text.startswith(section_title):
                    section_text = section_text[len(section_title) :].strip()
                sections.append(JatsSection(title=section_title, text=section_text))

        statements = _review_statements(root)
        license_element = _find_first(root, "license")
        license_text = _normalized_text(license_element) if license_element is not None else ""
        license_url = _extract_license_url(license_element)
        license_info = classify_license(license_text, license_url)
        return JatsDocument(
            title=title,
            abstract=abstract,
            sections=tuple(sections),
            statements=statements,
            license=license_info,
        )


def classify_license(text: str, url: str | None = None) -> LicenseInfo:
    haystack = f"{text} {url or ''}".casefold()
    compact = re.sub(r"[\s_/]+", "-", haystack)
    code: str | None = None
    commercial: bool | None = None
    derivatives: bool | None = None
    rights = RightsStatus.UNKNOWN

    if "creativecommons.org/publicdomain/zero" in haystack or "cc0" in compact:
        code, commercial, derivatives = "CC0-1.0", True, True
        rights = RightsStatus.REDISTRIBUTABLE
    elif "by-nc-nd" in compact:
        code, commercial, derivatives = "CC-BY-NC-ND", False, False
        rights = RightsStatus.METADATA_ONLY
    elif "by-nc-sa" in compact:
        code, commercial, derivatives = "CC-BY-NC-SA", False, True
        rights = RightsStatus.METADATA_ONLY
    elif "by-nc" in compact:
        code, commercial, derivatives = "CC-BY-NC", False, True
        rights = RightsStatus.METADATA_ONLY
    elif "by-nd" in compact:
        code, commercial, derivatives = "CC-BY-ND", True, False
        rights = RightsStatus.METADATA_ONLY
    elif "by-sa" in compact:
        code, commercial, derivatives = "CC-BY-SA", True, True
        rights = RightsStatus.INTERNAL_TDM_ONLY
    elif "creativecommons.org/licenses/by" in haystack or re.search(r"\bcc[- ]?by\b", haystack):
        code, commercial, derivatives = "CC-BY", True, True
        rights = RightsStatus.REDISTRIBUTABLE

    return LicenseInfo(
        code=code,
        text=text,
        url=url,
        rights_status=rights,
        commercial_use_allowed=commercial,
        derivatives_allowed=derivatives,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_first(root: ET.Element, local_name: str) -> ET.Element | None:
    return next(
        (element for element in root.iter() if _local_name(element.tag) == local_name),
        None,
    )


def _text_of_first(root: ET.Element, local_name: str) -> str:
    element = _find_first(root, local_name)
    return _normalized_text(element) if element is not None else ""


def _text_of_direct_child(root: ET.Element, local_name: str) -> str:
    element = next((child for child in root if _local_name(child.tag) == local_name), None)
    return _normalized_text(element) if element is not None else ""


def _review_statements(root: ET.Element) -> tuple[JatsSection, ...]:
    # JATS 1.3 places funding in article-meta and reviewer declarations in
    # author-notes or the back-matter fn-group. Table footnotes are excluded.
    statements: list[JatsSection] = []
    article_meta = _find_first(root, "article-meta")
    if article_meta is not None:
        funding_groups = [
            element
            for element in article_meta.iter()
            if _local_name(element.tag) == "funding-group"
        ]
        funding_elements = funding_groups or [
            element
            for element in article_meta.iter()
            if _local_name(element.tag) == "funding-statement"
        ]
        statements.extend(_statement("Funding", element) for element in funding_elements)
        author_notes = next(
            (child for child in article_meta if _local_name(child.tag) == "author-notes"),
            None,
        )
        if author_notes is not None:
            statements.extend(
                _statement(_footnote_title(note), note)
                for note in author_notes
                if _local_name(note.tag) == "fn"
            )

    back = _find_first(root, "back")
    if back is not None:
        statements.extend(
            _statement("Acknowledgements", child)
            for child in back
            if _local_name(child.tag) == "ack"
        )
        for group in [child for child in back if _local_name(child.tag) == "fn-group"]:
            statements.extend(
                _statement(_footnote_title(note), note)
                for note in group
                if _local_name(note.tag) == "fn"
            )

    return tuple(dict.fromkeys(statements))


def _statement(title: str, element: ET.Element) -> JatsSection:
    return JatsSection(title=title, text=_normalized_text(element))


def _footnote_title(note: ET.Element) -> str:
    label = _text_of_first(note, "bold").rstrip(":").strip()
    if label:
        return label
    return note.attrib.get("fn-type", "Article statement").replace("-", " ").strip()


def _normalized_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _extract_license_url(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    for key, value in element.attrib.items():
        if key.endswith("href") and value:
            return value
    for child in element.iter():
        for key, value in child.attrib.items():
            if key.endswith("href") and value:
                return value
    return None


def _strip_external_doctype(payload: bytes) -> bytes:
    upper = payload.upper()
    start = upper.find(b"<!DOCTYPE")
    if start < 0:
        return payload
    end = payload.find(b">", start)
    if end < 0:
        raise JatsParseError("Unterminated DOCTYPE declaration.")
    declaration = payload[start : end + 1]
    if b"[" in declaration or b"]" in declaration:
        raise JatsParseError("Internal DTD subsets are not accepted.")
    return payload[:start] + payload[end + 1 :]
