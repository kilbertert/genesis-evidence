"""T9：未覆盖项显式化（PRD #239 的第 12 条 user story）。

本批最本质的一条：报告里**每一项**异常都要有明确归属——要么对应一个健康问题，
要么显式告知「不在当前解读范围内」。缺失后者时，「没被解读」与「被判定为正常」
在患者侧长得一模一样，那是**虚假保证**。
"""

from __future__ import annotations

from genesis_evidence.core.disposition import UNKNOWN_METRIC
from genesis_evidence.core.matching import patient_reply_v3
from genesis_evidence.integrations.health_flow import build_evidence_request

_FINDING = {
    "condition_code": "COND_X",
    "condition_name": "示例健康问题",
    "urgency": "routine",
    "abnormality_severity": 1,
    "evidence_strength": "low",
    "needs_recheck": True,
    "department": "示例科",
    "recheck_direction": "复查",
    "source_observation_ids": ["o1"],
    "source_observations": [],
    "content_layer": "context_only",
    "action_status": "not_available",
    "action_message": "",
    "evidence_items": [],
}

_UNMATCHED = [
    {
        "observation_id": "m1",
        "metric_code": "triglycerides",
        "metric_label": "甘油三酯",
        "condition_codes": ["COND_DYSLIPIDEMIA"],
        "condition_names": ["血脂异常"],
        "reason": "no_published_knowledge_card",
    }
]


def _uncovered(n: int) -> list[dict[str, str]]:
    return [{"observation_id": f"u{i}", "reason": UNKNOWN_METRIC} for i in range(n)]


def test_unreadable_rows_are_reported_alongside_findings() -> None:
    """有 finding 时更要报——否则患者把「其余没提」读成「其余都正常」。

    `unmatched`（知道归属、只差卡）与「读不懂」在同一句里各报各的，不合并。
    """

    reply = patient_reply_v3([_FINDING], _UNMATCHED, _uncovered(40))

    assert "1 条指标与健康问题关联暂无已审核知识卡" in reply["summary"]
    assert "40 项" in reply["summary"]
    assert "不在当前解读范围内" in reply["summary"]


def test_unreadable_rows_survive_a_report_with_nothing_readable() -> None:
    reply = patient_reply_v3([], [], _uncovered(7))

    assert "7 项" in reply["summary"]
    assert "不在当前解读范围内" in reply["summary"]


def test_clean_report_says_nothing_about_unreadable_rows() -> None:
    """全是参考范围内的报告不该出现「不在解读范围内」的提示——那会平白吓人。"""

    reply = patient_reply_v3(
        [], [], [{"observation_id": "a", "reason": "within_reference_range"}]
    )

    assert "不在当前解读范围内" not in reply["summary"]


def test_summary_leaks_no_internal_identifiers() -> None:
    """提示文案不得泄露内部标识：metric_code、别名表键、表名。"""

    summary = patient_reply_v3([_FINDING], _UNMATCHED, _uncovered(3))["summary"]

    for leaked in (
        "unknown_metric_code",
        "metric_code",
        "no_published_knowledge_card",
        "condition_code",
        "knowledge_cards",
        "skipped",
        "unmatched",
        "uncovered",
    ):
        assert leaked not in summary, f"summary leaks internal identifier: {leaked}"


def test_an_unreadable_item_is_counted_but_never_becomes_a_finding() -> None:
    """端到端：读不懂的项目名既不产生 finding，也不归属任何 condition。

    适配器把它记为 `UNKNOWN_METRIC`（显式，不静默），请求里没有它的观测；
    患者侧得到的是条数，而不是某项被硬塞进某个健康问题。文案不点名——具体是
    哪几项由患者对着手上的报告看，服务侧不下断言。
    """

    result = build_evidence_request(
        [
            {
                "metric_name": "C-Reactive Protein",
                "metric_value": "12",
                "unit": "mg/L",
                "reference_range": "0-5",
                "evidence_text": "C-Reactive Protein 12 mg/L 参考范围 0-5 H",
                "page_number": 1,
            }
        ],
        confirmed=True,
    )

    assert result.skipped[0]["reason"] == UNKNOWN_METRIC
    assert list(result.request.observations) == []
    assert "condition" not in str(result.skipped[0])

    reply = patient_reply_v3([], [], [{"reason": UNKNOWN_METRIC}])

    assert "1 项" in reply["summary"]
    assert "不在当前解读范围内" in reply["summary"]
