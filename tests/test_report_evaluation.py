from __future__ import annotations

from genesis_evidence.reports.evaluation import (
    EvaluationCase,
    PredictionCase,
    evaluate,
)


def _gold() -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": "case-1",
            "files": ["page-1.png", "page-2.png"],
            "subject_consistency": "same",
            "observations": [
                {
                    "source_file_index": 1,
                    "source_page": 1,
                    "name": "空腹血糖",
                    "value": 6.8,
                    "unit": "mmol/L",
                    "reference_low": 3.9,
                    "reference_high": 6.1,
                    "flag": "high",
                },
                {
                    "source_file_index": 2,
                    "source_page": 1,
                    "name": "白蛋白",
                    "value": 43,
                    "unit": "g/L",
                    "reference_low": 35,
                    "reference_high": 50,
                    "flag": "normal",
                },
            ],
        }
    )


def _prediction(*, omit_normal: bool = False, unsafe: bool = False) -> PredictionCase:
    observations = [
        {
            "source_file_index": 1,
            "source_page": 1,
            "name": "空腹血糖",
            "model_value": 6.8,
            "model_unit": "mmol/L",
            "model_reference_low": 3.9,
            "model_reference_high": 6.1,
            "evidence": "空腹血糖 6.8 mmol/L 3.9-6.1 H",
            "extraction_status": "ambiguous" if unsafe else "clear",
            "default_decision": "pending" if unsafe else "pending",
            "validation_issues": ["待核对"] if unsafe else [],
        },
        {
            "source_file_index": 2,
            "source_page": 1,
            "name": "白蛋白",
            "model_value": 43,
            "model_unit": "g/L",
            "model_reference_low": 35,
            "model_reference_high": 50,
            "evidence": "白蛋白 43 g/L 35-50",
            "extraction_status": "clear",
            "default_decision": "pending",
            "validation_issues": [],
        },
    ]
    return PredictionCase.model_validate(
        {
            "case_id": "case-1",
            "files": ["page-1.png", "page-2.png"],
            "subject_consistency": "same",
            "observations": observations[:1] if omit_normal else observations,
        }
    )


def test_perfect_prediction_reports_quantitative_metrics() -> None:
    result = evaluate([_gold()], [_prediction()])
    assert result["metrics"] == {
        "row_precision": 1.0,
        "row_recall": 1.0,
        "row_f1": 1.0,
        "value_accuracy": 1.0,
        "unit_accuracy": 1.0,
        "reference_accuracy": 1.0,
        "evidence_coverage": 1.0,
        "abnormal_row_recall": 1.0,
        "unsafe_inclusion_rate": None,
        "file_order_accuracy": 1.0,
        "subject_consistency_accuracy": 1.0,
    }


def test_missing_rows_and_unsafe_inclusion_are_visible() -> None:
    result = evaluate([_gold()], [_prediction(omit_normal=True, unsafe=True)])
    assert result["metrics"]["row_recall"] == 0.5
    assert result["metrics"]["abnormal_row_recall"] == 1.0
    assert result["metrics"]["unsafe_inclusion_rate"] == 1.0


def test_unexpected_prediction_cases_count_as_false_positive_rows() -> None:
    unexpected = _prediction().model_copy(update={"case_id": "unexpected"})
    result = evaluate([_gold()], [_prediction(), unexpected])
    assert result["unexpected_predictions"] == ["unexpected"]
    assert result["metrics"]["row_precision"] == 0.5
