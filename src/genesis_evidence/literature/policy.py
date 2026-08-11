"""Source allow-list and download policy enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .models import FullTextCandidate, RightsStatus, SourceAccess, SourceName


class SourcePolicyError(RuntimeError):
    """Raised when a source or download violates the configured policy."""


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    source: SourceName
    access: SourceAccess
    search_allowed: bool
    download_allowed: bool
    note: str


class SourcePolicyRegistry:
    """Central policy gate for every connector and artifact download."""

    def __init__(self, *, core_license_confirmed: bool = False) -> None:
        core_access = (
            SourceAccess.APPROVED_LICENSED if core_license_confirmed else SourceAccess.METADATA_ONLY
        )
        self._policies = {
            SourceName.DOAJ: SourcePolicy(
                source=SourceName.DOAJ,
                access=SourceAccess.APPROVED_OPEN,
                search_allowed=True,
                download_allowed=False,
                note="DOAJ metadata is open; publisher full-text links require rights review.",
            ),
            SourceName.EUROPE_PMC: SourcePolicy(
                source=SourceName.EUROPE_PMC,
                access=SourceAccess.APPROVED_OPEN,
                search_allowed=True,
                download_allowed=True,
                note="Only explicit open-access full-text endpoints are downloadable.",
            ),
            SourceName.CORE: SourcePolicy(
                source=SourceName.CORE,
                access=core_access,
                search_allowed=core_license_confirmed,
                download_allowed=core_license_confirmed,
                note="Organisation-level CORE licence confirmation is required.",
            ),
        }

    @staticmethod
    def is_blocked_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").casefold()
        return "sci-hub" in host or "scihub" in host

    def policy_for(self, source: SourceName) -> SourcePolicy:
        return self._policies[source]

    def require_search_allowed(self, source: SourceName) -> None:
        policy = self.policy_for(source)
        if not policy.search_allowed:
            raise SourcePolicyError(policy.note)

    def require_download_allowed(self, candidate: FullTextCandidate) -> None:
        if self.is_blocked_url(candidate.url):
            raise SourcePolicyError("Blocked source URL: paywall-bypass sources are forbidden.")
        policy = self.policy_for(candidate.source)
        if not policy.download_allowed:
            raise SourcePolicyError(policy.note)
        if candidate.access not in {
            SourceAccess.APPROVED_OPEN,
            SourceAccess.APPROVED_LICENSED,
        }:
            raise SourcePolicyError(
                f"Candidate access status does not permit download: {candidate.access.value}"
            )
        if candidate.rights_status in {
            RightsStatus.METADATA_ONLY,
        }:
            raise SourcePolicyError("Candidate rights permit metadata storage only.")
