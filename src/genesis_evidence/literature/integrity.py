"""Retraction, correction, and publication-integrity status checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

from .http import HttpClient, HttpRequestError


class IntegrityStatus(StrEnum):
    CLEAR = "clear"
    UPDATED = "updated"
    CORRECTED = "corrected"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    RETRACTED = "retracted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntegrityFinding:
    kind: str
    source: str
    relation_type: str
    related_identifier: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProviderIntegrityResult:
    source: str
    checked: bool
    findings: tuple[IntegrityFinding, ...]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IntegrityAssessment:
    status: IntegrityStatus
    findings: tuple[IntegrityFinding, ...]
    checked_sources: tuple[str, ...]
    raw_by_source: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "findings": [asdict(finding) for finding in self.findings],
            "checked_sources": list(self.checked_sources),
            "raw_by_source": self.raw_by_source,
        }


class IntegrityProvider(Protocol):
    source: str

    def check(self, paper: dict[str, Any]) -> ProviderIntegrityResult: ...


class CrossrefIntegrityProvider:
    source = "crossref"
    base_url = "https://api.crossref.org/works"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def check(self, paper: dict[str, Any]) -> ProviderIntegrityResult:
        doi = str(paper.get("doi") or "").strip()
        if not doi:
            return ProviderIntegrityResult(self.source, False, (), {})
        try:
            payload = self._http.get_json(f"{self.base_url}/{quote(doi, safe='')}")
        except HttpRequestError as exc:
            if exc.status_code == 404:
                return ProviderIntegrityResult(
                    self.source,
                    False,
                    (),
                    {"status": "not_found", "doi": doi},
                )
            raise
        message = payload.get("message")
        raw = message if isinstance(message, dict) else {}
        findings = _crossref_findings(raw)
        return ProviderIntegrityResult(self.source, True, tuple(findings), raw)


class EuropePmcIntegrityProvider:
    source = "europe_pmc"
    search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def check(self, paper: dict[str, Any]) -> ProviderIntegrityResult:
        query = _europe_pmc_query(paper)
        if not query:
            return ProviderIntegrityResult(self.source, False, (), {})
        payload = self._http.get_json(
            self.search_url,
            params={"query": query, "resultType": "core", "format": "json", "pageSize": 3},
        )
        result_list = payload.get("resultList")
        results = result_list.get("result", []) if isinstance(result_list, dict) else []
        record = next((item for item in results if isinstance(item, dict)), None)
        if record is None:
            return ProviderIntegrityResult(self.source, True, (), {"result": None})
        findings = _europe_pmc_findings(record)
        return ProviderIntegrityResult(self.source, True, tuple(findings), record)


class PublicationIntegrityChecker:
    def __init__(self, providers: tuple[IntegrityProvider, ...]) -> None:
        self._providers = providers

    def check(self, paper: dict[str, Any]) -> IntegrityAssessment:
        results = [provider.check(paper) for provider in self._providers]
        checked = tuple(result.source for result in results if result.checked)
        findings = _deduplicate_findings(
            finding for result in results for finding in result.findings
        )
        raw = {result.source: result.raw for result in results if result.checked}
        status = _overall_status(findings, any(result.checked for result in results))
        return IntegrityAssessment(
            status=status,
            findings=tuple(findings),
            checked_sources=checked,
            raw_by_source=raw,
        )


def _crossref_findings(message: dict[str, Any]) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    updates = message.get("update-to")
    if isinstance(updates, list):
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_type = str(update.get("type") or update.get("label") or "update").casefold()
            kind = _kind_from_relation(update_type)
            findings.append(
                IntegrityFinding(
                    kind=kind,
                    source="crossref",
                    relation_type=f"update-to:{update_type}",
                    related_identifier=_identifier(update),
                    note=str(update.get("label") or ""),
                )
            )
    relation = message.get("relation")
    if isinstance(relation, dict):
        for relation_type, values in relation.items():
            normalized = str(relation_type).casefold()
            if normalized not in {
                "is-retracted-by",
                "is-corrected-by",
                "is-updated-by",
                "has-expression-of-concern",
                "is-expression-of-concern-by",
            }:
                continue
            items = values if isinstance(values, list) else [values]
            for item in items:
                raw_item = item if isinstance(item, dict) else {"id": item}
                findings.append(
                    IntegrityFinding(
                        kind=_kind_from_relation(normalized),
                        source="crossref",
                        relation_type=normalized,
                        related_identifier=_identifier(raw_item),
                    )
                )
    return findings


def _europe_pmc_findings(record: dict[str, Any]) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    if str(record.get("isWithdrawn") or "").casefold() in {"y", "yes", "true", "1"}:
        findings.append(
            IntegrityFinding(
                kind="retraction",
                source="europe_pmc",
                relation_type="isWithdrawn",
                note="Europe PMC marks the article as withdrawn.",
            )
        )
    publication_types = record.get("pubTypeList")
    values = publication_types.get("pubType", []) if isinstance(publication_types, dict) else []
    if isinstance(values, str):
        values = [values]
    normalized_types = {str(value).casefold() for value in values}
    if "retracted publication" in normalized_types:
        findings.append(
            IntegrityFinding(
                kind="retraction",
                source="europe_pmc",
                relation_type="pubType:Retracted Publication",
            )
        )
    findings.extend(_europe_pmc_comments(record.get("commentOnList")))
    return findings


def _europe_pmc_comments(value: object) -> list[IntegrityFinding]:
    if not isinstance(value, dict):
        return []
    comments = value.get("commentOn", [])
    if isinstance(comments, dict):
        comments = [comments]
    findings: list[IntegrityFinding] = []
    for comment in comments if isinstance(comments, list) else []:
        if not isinstance(comment, dict):
            continue
        relation = str(comment.get("type") or comment.get("relationship") or "").casefold()
        if not any(token in relation for token in ("retract", "erratum", "correct", "concern")):
            continue
        findings.append(
            IntegrityFinding(
                kind=_kind_from_relation(relation),
                source="europe_pmc",
                relation_type=relation,
                related_identifier=str(
                    comment.get("id") or comment.get("pmid") or comment.get("doi") or ""
                )
                or None,
            )
        )
    return findings


def _overall_status(
    findings: list[IntegrityFinding], any_provider_checked: bool
) -> IntegrityStatus:
    kinds = {finding.kind for finding in findings}
    for kind, status in (
        ("retraction", IntegrityStatus.RETRACTED),
        ("expression_of_concern", IntegrityStatus.EXPRESSION_OF_CONCERN),
        ("correction", IntegrityStatus.CORRECTED),
        ("update", IntegrityStatus.UPDATED),
    ):
        if kind in kinds:
            return status
    return IntegrityStatus.CLEAR if any_provider_checked else IntegrityStatus.UNKNOWN


def _kind_from_relation(value: str) -> str:
    normalized = value.casefold()
    if "retract" in normalized or "withdraw" in normalized:
        return "retraction"
    if "concern" in normalized:
        return "expression_of_concern"
    if "correct" in normalized or "erratum" in normalized:
        return "correction"
    return "update"


def _identifier(value: dict[str, Any]) -> str | None:
    identifier = value.get("DOI") or value.get("doi") or value.get("id")
    return str(identifier).strip() if identifier else None


def _europe_pmc_query(paper: dict[str, Any]) -> str | None:
    if paper.get("pmid"):
        return f"EXT_ID:{paper['pmid']} AND SRC:MED"
    if paper.get("pmcid"):
        return f"PMCID:{paper['pmcid']}"
    if paper.get("doi"):
        return f'DOI:"{paper["doi"]}"'
    return None


def _deduplicate_findings(findings: Any) -> list[IntegrityFinding]:
    unique: list[IntegrityFinding] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for finding in findings:
        key = (
            finding.kind,
            finding.source,
            finding.relation_type,
            finding.related_identifier,
        )
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique
