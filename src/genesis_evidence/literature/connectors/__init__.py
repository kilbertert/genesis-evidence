"""Supported scholarly metadata connectors."""

from .base import LiteratureConnector
from .core import CoreConnector
from .doaj import DoajConnector
from .europe_pmc import EuropePmcConnector

__all__ = [
    "CoreConnector",
    "DoajConnector",
    "EuropePmcConnector",
    "LiteratureConnector",
]
