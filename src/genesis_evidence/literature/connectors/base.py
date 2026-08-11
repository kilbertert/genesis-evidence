"""Connector interface for literature discovery services."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import SearchPage, SourceName


class LiteratureConnector(ABC):
    source: SourceName

    @abstractmethod
    def search(self, query: str, *, limit: int = 25, cursor: str | None = None) -> SearchPage:
        """Return one page of canonical records."""
