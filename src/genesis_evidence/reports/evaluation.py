"""Deterministic metrics for de-identified report extraction evaluations."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extraction import evidence_contains_value


class EvaluationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    name: str = Field(min_length=1)
    value: float
    unit: str
    reference_low: float | None = None
    reference_high: float | None = None
    flag: Literal["high", "low", "normal", "unknown"] = "unknown"


class PredictionObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    name: str = Field(min_length=1)
    model_value: float
    model_unit: str
    model_reference_low: float | None = None
    model_reference_high: float | None = None
    evidence: str
    extraction_status: Literal["clear", "ambiguous"]
    default_decision: Literal["pending", "excluded"]
    validation_issues: list[str] | tuple[str, ...] = ()


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    files: list[str] = Field(min_length=1)
    subject_consistency: Literal["same", "uncertain"]
    observations: list[EvaluationObservation]

    @model_validator(mode="after")
    def require_unique_rows(self) -> EvaluationCase:
        _require_unique(self.observations)
        return self


class PredictionCase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(min_length=1)
    files: list[str]
    subject_consistency: Literal["same", "uncertain"]
    observations: list[PredictionObservation]

    @model_validator(mode="after")
    def require_unique_rows(self) -> PredictionCase:
        _require_unique(self.observations)
        return self


def evaluate(
    gold_cases: list[EvaluationCase], prediction_cases: list[PredictionCase]
) -> dict[str, object]:
    gold_by_id = _unique_cases(gold_cases)
    prediction_by_id = _unique_cases(prediction_cases)
    totals = {
        "gold_rows": 0,
        "predicted_rows": 0,
        "matched_rows": 0,
        "value_matches": 0,
        "unit_matches": 0,
        "reference_matches": 0,
        "evidence_matches": 0,
        "gold_abnormal_rows": 0,
        "matched_abnormal_rows": 0,
        "unsafe_candidates": 0,
        "unsafe_inclusions": 0,
        "file_order_matches": 0,
        "subject_matches": 0,
    }
    for case_id, gold in gold_by_id.items():
        prediction = prediction_by_id.get(case_id)
        totals["gold_rows"] += len(gold.observations)
        if prediction is None:
            totals["gold_abnormal_rows"] += sum(_is_abnormal(item) for item in gold.observations)
            continue
        totals["predicted_rows"] += len(prediction.observations)
        totals["file_order_matches"] += prediction.files == gold.files
        totals["subject_matches"] += prediction.subject_consistency == gold.subject_consistency
        gold_rows = {_row_key(item): item for item in gold.observations}
        predicted_rows = {_row_key(item): item for item in prediction.observations}
        for key, expected in gold_rows.items():
            abnormal = _is_abnormal(expected)
            totals["gold_abnormal_rows"] += abnormal
            actual = predicted_rows.get(key)
            if actual is None:
                continue
            totals["matched_rows"] += 1
            totals["matched_abnormal_rows"] += abnormal
            totals["value_matches"] += _same_number(expected.value, actual.model_value)
            totals["unit_matches"] += _normalized_unit(expected.unit) == _normalized_unit(
                actual.model_unit
            )
            totals["reference_matches"] += _same_optional_number(
                expected.reference_low, actual.model_reference_low
            ) and _same_optional_number(expected.reference_high, actual.model_reference_high)
            evidence_values = [expected.value]
            evidence_values.extend(
                bound
                for bound in (expected.reference_low, expected.reference_high)
                if bound is not None
            )
            totals["evidence_matches"] += all(
                evidence_contains_value(actual.evidence, value) for value in evidence_values
            )
        for actual in prediction.observations:
            unsafe = actual.extraction_status == "ambiguous" or bool(actual.validation_issues)
            totals["unsafe_candidates"] += unsafe
            totals["unsafe_inclusions"] += unsafe and actual.default_decision != "excluded"

    for case_id in set(prediction_by_id) - set(gold_by_id):
        prediction = prediction_by_id[case_id]
        totals["predicted_rows"] += len(prediction.observations)
        for actual in prediction.observations:
            unsafe = actual.extraction_status == "ambiguous" or bool(actual.validation_issues)
            totals["unsafe_candidates"] += unsafe
            totals["unsafe_inclusions"] += unsafe and actual.default_decision != "excluded"

    cases = len(gold_by_id)
    matched = totals["matched_rows"]
    return {
        "case_count": cases,
        "missing_predictions": sorted(set(gold_by_id) - set(prediction_by_id)),
        "unexpected_predictions": sorted(set(prediction_by_id) - set(gold_by_id)),
        "counts": totals,
        "metrics": {
            "row_precision": _rate(matched, totals["predicted_rows"]),
            "row_recall": _rate(matched, totals["gold_rows"]),
            "row_f1": _f1(matched, totals["predicted_rows"], totals["gold_rows"]),
            "value_accuracy": _rate(totals["value_matches"], matched),
            "unit_accuracy": _rate(totals["unit_matches"], matched),
            "reference_accuracy": _rate(totals["reference_matches"], matched),
            "evidence_coverage": _rate(totals["evidence_matches"], matched),
            "abnormal_row_recall": _rate(
                totals["matched_abnormal_rows"], totals["gold_abnormal_rows"]
            ),
            "unsafe_inclusion_rate": _rate(
                totals["unsafe_inclusions"], totals["unsafe_candidates"]
            ),
            "file_order_accuracy": _rate(totals["file_order_matches"], cases),
            "subject_consistency_accuracy": _rate(totals["subject_matches"], cases),
        },
    }


def load_jsonl(path: Path, model_type):
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model_type.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid {path} line {line_number}: {exc}") from exc
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate health-report extraction predictions")
    parser.add_argument("gold", type=Path)
    parser.add_argument("predictions", type=Path)
    args = parser.parse_args()
    result = evaluate(
        load_jsonl(args.gold, EvaluationCase),
        load_jsonl(args.predictions, PredictionCase),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _unique_cases(cases):
    by_id = {case.case_id: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("case_id values must be unique")
    return by_id


def _require_unique(observations) -> None:
    keys = [_row_key(item) for item in observations]
    if len(set(keys)) != len(keys):
        raise ValueError("rows must be unique by source file, page, and name")


def _row_key(item) -> tuple[int, int, str]:
    return item.source_file_index, item.source_page, re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+", "", item.name.casefold()
    )


def _is_abnormal(item: EvaluationObservation) -> bool:
    return (
        item.flag in {"high", "low"}
        or (item.reference_low is not None and item.value < item.reference_low)
        or (item.reference_high is not None and item.value > item.reference_high)
    )


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _same_optional_number(left: float | None, right: float | None) -> bool:
    return left is right if left is None or right is None else _same_number(left, right)


def _normalized_unit(value: str) -> str:
    return "".join(value.casefold().split())


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _f1(matched: int, predicted: int, expected: int) -> float | None:
    return _rate(2 * matched, predicted + expected)


if __name__ == "__main__":
    main()
