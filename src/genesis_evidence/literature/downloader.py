"""Policy-aware full-text acquisition."""

from __future__ import annotations

from dataclasses import dataclass

from .http import DownloadedResponse, HttpClient
from .models import FullTextCandidate, FullTextFormat
from .policy import SourcePolicyRegistry


class DownloadValidationError(RuntimeError):
    """Raised when a downloaded artifact does not match its declared format."""


@dataclass(frozen=True, slots=True)
class DownloadedArtifact:
    candidate: FullTextCandidate
    content: bytes
    media_type: str
    final_url: str


class FullTextDownloader:
    def __init__(
        self,
        http: HttpClient,
        policy: SourcePolicyRegistry,
        *,
        max_download_bytes: int,
    ) -> None:
        self._http = http
        self._policy = policy
        self._max_download_bytes = max_download_bytes

    def download(self, candidate: FullTextCandidate) -> DownloadedArtifact:
        self._policy.require_download_allowed(candidate)
        response = self._http.get_bytes(
            candidate.url,
            max_bytes=self._max_download_bytes,
            headers={"Accept": candidate.media_type or "application/octet-stream"},
        )
        if self._policy.is_blocked_url(response.final_url):
            raise DownloadValidationError("Download redirected to a blocked source URL.")
        self._validate(candidate, response)
        return DownloadedArtifact(
            candidate=candidate,
            content=response.content,
            media_type=response.media_type,
            final_url=response.final_url,
        )

    @staticmethod
    def _validate(candidate: FullTextCandidate, response: DownloadedResponse) -> None:
        content = response.content.lstrip()
        if not content:
            raise DownloadValidationError("Downloaded artifact is empty.")
        if candidate.format == FullTextFormat.PDF and not content.startswith(b"%PDF-"):
            raise DownloadValidationError("Expected PDF magic bytes were not found.")
        if (
            candidate.format in {FullTextFormat.JATS_XML, FullTextFormat.XML}
            and b"<article" not in content[:4096]
        ):
            raise DownloadValidationError("Expected XML content was not found.")
