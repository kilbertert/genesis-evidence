"""Respectful HTTP client with rate limiting and bounded retries."""

from __future__ import annotations

import email.utils
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx


class HttpRequestError(RuntimeError):
    """Raised when an upstream request cannot be completed safely."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ResponseTooLargeError(HttpRequestError):
    """Raised before an upstream payload can exceed the configured limit."""


@dataclass(frozen=True, slots=True)
class DownloadedResponse:
    content: bytes
    media_type: str
    final_url: str
    headers: dict[str, str]


class HostRateLimiter:
    def __init__(
        self,
        intervals: Mapping[str, float] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._intervals = dict(intervals or {})
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = (urlparse(url).hostname or "").casefold()
        interval = self._intervals.get(host, 0.0)
        if interval <= 0:
            return
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed.get(host, now) - now)
            self._next_allowed[host] = max(now, self._next_allowed.get(host, now)) + interval
        if delay:
            self._sleep(delay)


class HttpClient:
    """Small wrapper around httpx used by every source connector."""

    DEFAULT_INTERVALS = {
        "doaj.org": 0.5,
        "www.ebi.ac.uk": 0.1,
        "api.crossref.org": 0.1,
        "api.core.ac.uk": 0.25,
    }

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        max_retry_wait_seconds: float = 30.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rate_limiter: HostRateLimiter | None = None,
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._max_retries = max_retries
        self._max_retry_wait_seconds = max_retry_wait_seconds
        self._sleep = sleep
        self._rate_limiter = rate_limiter or HostRateLimiter(self.DEFAULT_INTERVALS, sleep=sleep)

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            self._rate_limiter.wait(url)
            try:
                response = self._client.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise HttpRequestError(f"Request failed for {url}: {exc}") from exc
                self._sleep(min(2**attempt, self._max_retry_wait_seconds))
                continue

            last_response = response
            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise HttpRequestError(
                        f"Upstream returned HTTP {response.status_code} for {url}",
                        status_code=response.status_code,
                    ) from exc
                return response

            if attempt >= self._max_retries:
                break
            wait_seconds = self._retry_delay(response, attempt)
            if wait_seconds > self._max_retry_wait_seconds:
                break
            self._sleep(wait_seconds)

        status = last_response.status_code if last_response is not None else "unknown"
        retry_after = ""
        if last_response is not None:
            retry_after = (
                last_response.headers.get("x-ratelimit-retry-after")
                or last_response.headers.get("retry-after")
                or ""
            )
        suffix = f"; retry after {retry_after}" if retry_after else ""
        raise HttpRequestError(
            f"Upstream returned HTTP {status} for {url}{suffix}",
            status_code=last_response.status_code if last_response is not None else None,
        )

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self.request("GET", url, params=params, headers=headers)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HttpRequestError(f"Invalid JSON response from {url}") from exc
        if not isinstance(payload, dict):
            raise HttpRequestError(f"Unexpected JSON document from {url}")
        return payload

    def get_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> DownloadedResponse:
        last_status: int | None = None
        last_headers: dict[str, str] = {}
        for attempt in range(self._max_retries + 1):
            self._rate_limiter.wait(url)
            try:
                with self._client.stream("GET", url, headers=headers) as response:
                    last_status = response.status_code
                    last_headers = dict(response.headers)
                    if response.status_code in {429, 500, 502, 503, 504}:
                        if attempt >= self._max_retries:
                            break
                        wait_seconds = self._retry_delay(response, attempt)
                        if wait_seconds > self._max_retry_wait_seconds:
                            break
                        self._sleep(wait_seconds)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise HttpRequestError(
                            f"Upstream returned HTTP {response.status_code} for {url}",
                            status_code=response.status_code,
                        ) from exc

                    content_length = response.headers.get("content-length", "")
                    if content_length.isdigit() and int(content_length) > max_bytes:
                        raise ResponseTooLargeError(f"Response exceeds {max_bytes} bytes: {url}")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise ResponseTooLargeError(
                                f"Response exceeds {max_bytes} bytes: {url}"
                            )
                    return DownloadedResponse(
                        content=bytes(content),
                        media_type=response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .casefold(),
                        final_url=str(response.url),
                        headers=dict(response.headers),
                    )
            except ResponseTooLargeError:
                raise
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise HttpRequestError(f"Request failed for {url}: {exc}") from exc
                self._sleep(min(2**attempt, self._max_retry_wait_seconds))

        retry_after = last_headers.get("x-ratelimit-retry-after") or last_headers.get(
            "retry-after", ""
        )
        suffix = f"; retry after {retry_after}" if retry_after else ""
        raise HttpRequestError(
            f"Upstream returned HTTP {last_status or 'unknown'} for {url}{suffix}",
            status_code=last_status,
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after", "").strip()
        if raw.isdigit():
            return max(0.0, float(raw))
        if raw:
            try:
                retry_at = email.utils.parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError):
                pass
        return min(float(2**attempt), 30.0)
