"""Deterministic adapter from Health-Flow metric rows to Evidence API v1."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.contracts import EvidenceMatchObservation, EvidenceMatchRequest
from ..core.metrics import METRIC_ALIASES, normalize_metric_name
from ..reports.extraction import evidence_contains_value

_NUMBER = r"-?\d+(?:\.\d+)?"
_NUMBER_RE = re.compile(rf"(?<![\d.]){_NUMBER}(?![\d.])")
_RANGE_RE = re.compile(rf"(?P<low>{_NUMBER})\s*(?:-|~|至|到)\s*(?P<high>{_NUMBER})")
_UPPER_RE = re.compile(rf"(?:<|<=|≤)\s*(?P<high>{_NUMBER})")
_LOWER_RE = re.compile(rf"(?:>|>=|≥)\s*(?P<low>{_NUMBER})")


@dataclass(frozen=True, slots=True)
class HealthFlowAdapterResult:
    request: EvidenceMatchRequest
    skipped: tuple[dict[str, str], ...]


def build_evidence_request(
    records: Sequence[Mapping[str, Any]],
    *,
    confirmed: bool,
    aliases: Mapping[str, str] = METRIC_ALIASES,
) -> HealthFlowAdapterResult:
    """Convert confirmed Health-Flow rows without trusting model flags.

    Health-Flow currently stores one file per report. A multi-file adapter can
    add ``source_file_index`` to each row; absent values default to file 1.
    """

    if not confirmed:
        return HealthFlowAdapterResult(
            request=EvidenceMatchRequest(schema_version="1", observations=[]),
            skipped=tuple(
                _skip(position, "confirmation_required") for position, _ in enumerate(records, 1)
            ),
        )

    normalized_aliases = {normalize_metric_name(key): value for key, value in aliases.items()}
    observations: list[EvidenceMatchObservation] = []
    skipped: list[dict[str, str]] = []
    for position, record in enumerate(records, start=1):
        name = _text(record.get("metric_name"))
        code = normalized_aliases.get(normalize_metric_name(name)) if name else None
        if not code:
            skipped.append(_skip(position, "unknown_metric"))
            continue

        page = _positive_int(record.get("page_number"))
        if page is None:
            skipped.append(_skip(position, "missing_source_page"))
            continue
        unit = _text(record.get("unit"))
        if not unit:
            skipped.append(_skip(position, "missing_unit"))
            continue
        evidence = _text(record.get("evidence_text"))
        value = _single_number(record.get("metric_value"))
        if value is None or not evidence or not evidence_contains_value(evidence, value):
            skipped.append(_skip(position, "missing_source_value"))
            continue

        reference_low, reference_high = _parse_reference_range(record.get("reference_range"))
        if reference_low is not None and not evidence_contains_value(evidence, reference_low):
            skipped.append(_skip(position, "missing_source_reference"))
            continue
        if reference_high is not None and not evidence_contains_value(evidence, reference_high):
            skipped.append(_skip(position, "missing_source_reference"))
            continue

        raw_file_index = record.get("source_file_index")
        file_index = 1 if raw_file_index is None else _positive_int(raw_file_index)
        if file_index is None:
            skipped.append(_skip(position, "invalid_source_file_index"))
            continue
        raw_bbox = record.get("bbox_normalized")
        bbox = _bbox(raw_bbox)
        if raw_bbox is not None and bbox is None:
            skipped.append(_skip(position, "invalid_bbox"))
            continue
        observations.append(
            EvidenceMatchObservation(
                observation_id=f"hf-observation-{position}",
                confirmation_status="confirmed",
                metric_code=code,
                value=value,
                unit=unit,
                reference_low=reference_low,
                reference_high=reference_high,
                evidence_text=evidence,
                source_file_index=file_index,
                source_page=page,
                source_id=_text(record.get("source_id")) or None,
                bbox_normalized=bbox,
            )
        )
    return HealthFlowAdapterResult(
        request=EvidenceMatchRequest(schema_version="1", observations=observations),
        skipped=tuple(skipped),
    )


def _parse_reference_range(value: object) -> tuple[float | None, float | None]:
    text = _text(value)
    if not text:
        return None, None
    match = _RANGE_RE.search(text)
    if match:
        low, high = float(match["low"]), float(match["high"])
        return (low, high) if low <= high else (None, None)
    match = _UPPER_RE.search(text)
    if match:
        return None, float(match["high"])
    match = _LOWER_RE.search(text)
    if match:
        return float(match["low"]), None
    return None, None


def _single_number(value: object) -> float | None:
    matches = _NUMBER_RE.findall(_text(value))
    if len(matches) != 1:
        return None
    number = float(matches[0])
    return number if math.isfinite(number) else None


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _bbox(value: object) -> list[float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        coordinates = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(item) or not 0 <= item <= 1000 for item in coordinates):
        return None
    if coordinates[0] > coordinates[2] or coordinates[1] > coordinates[3]:
        return None
    return coordinates


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _skip(position: int, reason: str) -> dict[str, str]:
    return {"record_index": str(position), "reason": reason}
