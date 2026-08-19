"""Evidence workflow with autonomous execution and human exception handling."""

from __future__ import annotations

import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.store import PaperStore, ReviewStore
from ..literature.ai_extraction import OBSERVATIONAL_DESIGNS

AUTONOMOUS_REVIEW_POLICY_VERSION = "literature-review-ai/1.2"

StudyDesign = Literal[
    "randomized_controlled_trial",
    "systematic_review_meta_analysis",
    "cohort_study",
    "case_control_study",
    "cross_sectional_study",
    "controlled_feeding_metabolic_study",
    "bioavailability_pharmacokinetic_study",
    "biomarker_validation_study",
    "non_randomized_controlled_study",
    "natural_experiment",
    "ecological_study",
    "animal_study",
    "in_vitro_study",
    "case_series",
    "case_report",
    "guideline",
    "other",
    "uncertain",
]

PublicationRole = Literal[
    "primary",
    "protocol",
    "statistical_analysis_plan",
    "follow_up",
    "subgroup",
    "combined_report",
    "correction",
    "retraction",
    "other",
]


class RiskOfBiasInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal[
        "rob2",
        "robins_i",
        "robis",
        "amstar2",
        "diagnostic_accuracy",
        "exposure_study",
        "safety_signal",
        "other",
    ]
    overall: Literal["low", "some_concerns", "high", "critical", "uncertain"]
    rationale: str = Field(min_length=1, max_length=4000)


class EvidenceProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certainty: Literal["high", "moderate", "low", "very_low"]
    scope_key: str | None = Field(default=None, min_length=1, max_length=160)
    certainty_rationale: str = Field(min_length=1, max_length=5000)
    estimate_target: str = Field(min_length=1, max_length=1000)
    interpretations: dict[
        str, Literal["supports", "does_not_support", "mixed", "uncertain", "not_reported"]
    ]

    @model_validator(mode="after")
    def require_interpretations(self) -> EvidenceProfileInput:
        if not self.interpretations:
            raise ValueError("evidence profile requires result interpretations")
        return self


class ClaimReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    corrected_text: str | None = Field(default=None, max_length=2000)
    corrected_study_design: StudyDesign | None = None
    inference: Literal["causal", "associational", "descriptive"] | None = None
    risk_of_bias: RiskOfBiasInput | None = None
    applicability: str | None = Field(default=None, max_length=4000)
    condition_code: str | None = None
    source_verified: bool = False

    @model_validator(mode="after")
    def validate_decision(self) -> ClaimReviewInput:
        required = (
            self.corrected_text,
            self.corrected_study_design,
            self.inference,
            self.risk_of_bias,
            self.applicability,
            self.condition_code,
        )
        if self.decision == "approved" and any(not value for value in required):
            raise ValueError("approved claims require corrected evidence fields")
        if self.decision == "approved" and not self.source_verified:
            raise ValueError("approved claims require executing-actor source verification")
        if (
            self.decision == "approved"
            and self.corrected_study_design in OBSERVATIONAL_DESIGNS
            and self.inference == "causal"
        ):
            raise ValueError("observational claims cannot be approved as causal")
        if self.decision == "approved" and self.risk_of_bias:
            allowed_tools = {
                "randomized_controlled_trial": {"rob2"},
                "systematic_review_meta_analysis": {"robis", "amstar2"},
                "non_randomized_controlled_study": {"robins_i"},
                "natural_experiment": {"robins_i"},
                "biomarker_validation_study": {"diagnostic_accuracy"},
                "bioavailability_pharmacokinetic_study": {"other"},
                "controlled_feeding_metabolic_study": {"rob2", "other"},
                "cohort_study": {"exposure_study"},
                "case_control_study": {"exposure_study"},
                "cross_sectional_study": {"exposure_study"},
                "ecological_study": {"exposure_study"},
                "case_series": {"safety_signal"},
                "case_report": {"safety_signal"},
                "animal_study": {"other"},
                "in_vitro_study": {"other"},
            }.get(self.corrected_study_design)
            if allowed_tools and self.risk_of_bias.tool not in allowed_tools:
                raise ValueError("risk-of-bias tool does not match the reviewed study design")
        return self


class EvidenceReviewService:
    def __init__(self, store: ReviewStore, papers_store: PaperStore | None = None) -> None:
        self.store = store
        self.papers_store = papers_store

    def auto_review_paper(self, paper_id: str, *, requested_by: str) -> dict[str, object]:
        requester = _reviewer(requested_by)
        item = self.store.get_review_item(paper_id)
        if item is None:
            raise ValueError("paper not found")
        if self.papers_store is None:
            raise RuntimeError("autonomous review requires PaperStore")
        terminal_exclusion = bool(item["collections"]) and all(
            collection.get("title_abstract_decision") == "excluded"
            or collection.get("full_text_decision") == "excluded"
            for collection in item["collections"]
        )
        if terminal_exclusion:
            admission = item.get("admission") or {}
            self.reject_paper(
                paper_id,
                reviewer=str(admission.get("reviewer") or "ai:screening-ledger"),
            )
            result = {"status": "completed", "decision": "excluded", "cards": []}
            self.store.record_event(
                "paper",
                paper_id,
                "autonomous_review_completed",
                actor="ai:screening-ledger",
                detail={
                    "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
                    "requested_by": requester,
                    "screening_records": [
                        {
                            key: collection.get(key)
                            for key in (
                                "topic_id",
                                "run_id",
                                "title_abstract_decision",
                                "full_text_decision",
                                "primary_exclusion_reason",
                            )
                        }
                        for collection in item["collections"]
                    ],
                    **result,
                },
            )
            return result
        trace = item.get("extraction_trace") or {}
        if not trace:
            guidance = item.get("review_guidance") or {}
            if guidance.get("terminal_decision") == "not_retrieved":
                actor = "ai:retrieval-ledger"
                result = {
                    "status": "completed",
                    "decision": "not_retrieved",
                    "reason": str(guidance.get("next_action") or "full text was not retrieved"),
                    "cards": [],
                }
                self.store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_review_completed",
                    actor=actor,
                    detail={
                        "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
                        "requested_by": requester,
                        "retrieval_records": [
                            {
                                key: collection.get(key)
                                for key in (
                                    "topic_id",
                                    "run_id",
                                    "full_text_retrieval_status",
                                    "full_text_retrieval_reason",
                                    "full_text_retrieval_reviewer",
                                    "full_text_retrieval_recorded_at",
                                )
                            }
                            for collection in item["collections"]
                            if collection.get("full_text_retrieval_status") == "not_retrieved"
                        ],
                        **result,
                    },
                )
                return result
            job = item.get("extraction_job") or {}
            if job.get("full_text_available"):
                if job.get("status") == "failed":
                    self.papers_store.retry_extraction(
                        str(job["id"]), reviewer="ai:extraction-worker"
                    )
                    status = "queued"
                elif job.get("status") in {"queued", "running"}:
                    status = str(job["status"])
                else:
                    run_id = next((str(row["run_id"]) for row in item["collections"]), None)
                    self.papers_store.enqueue_extraction(paper_id, collection_run_id=run_id)
                    status = "queued"
                result = {
                    "status": status,
                    "stage": "extraction",
                    "reason": (
                        "two independent extraction runs must complete before autonomous "
                        "review resumes"
                    ),
                }
                self.store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_extraction_queued",
                    actor="ai:extraction-worker",
                    detail={
                        "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
                        "requested_by": requester,
                        **result,
                    },
                )
                return result
            return self._automation_attention(
                paper_id,
                actor="ai:unavailable",
                requested_by=requester,
                stage="extraction",
                reason="paper has no stored full text for independent extraction",
                trace={},
            )
        actor = f"ai:{trace.get('check_model') or trace.get('model')}"
        context = {
            "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
            "requested_by": requester,
            "extraction_trace": trace,
        }
        self.store.record_event(
            "paper", paper_id, "autonomous_review_started", actor=actor, detail=context
        )
        admission = item.get("admission") or {}
        reopened_screening_rejection = (
            admission.get("status") == "rejected"
            and admission.get("reviewer") == "ai:screening-ledger"
            and any(
                collection.get("title_abstract_decision") != "excluded"
                and collection.get("full_text_decision") != "excluded"
                for collection in item["collections"]
            )
        )
        if (
            admission.get("status") == "rejected"
            and admission.get("reviewer") != "ai:screening-ledger"
        ):
            return self._automation_attention(
                paper_id,
                actor=actor,
                requested_by=requester,
                stage="admission",
                reason="a named actor has already rejected this paper",
                trace=trace,
            )

        screening_actions: list[dict[str, object]] = []
        while True:
            item = self.store.get_review_item(paper_id)
            assert item is not None
            suggestion = next(
                (
                    (collection, collection.get("screening_suggestion") or {})
                    for collection in item["collections"]
                    if (collection.get("screening_suggestion") or {}).get("stage")
                ),
                None,
            )
            if suggestion is None:
                break
            collection, decision = suggestion
            if decision.get("decision") not in {"included", "excluded"}:
                return self._automation_attention(
                    paper_id,
                    actor=actor,
                    requested_by=requester,
                    stage="screening",
                    reason=str(decision.get("reason") or "screening decision is ambiguous"),
                    trace=trace,
                )
            self.papers_store.screen_collection_paper(
                str(collection["run_id"]),
                paper_id,
                stage=str(decision["stage"]),
                decision=str(decision["decision"]),
                exclusion_reason=decision.get("primary_exclusion_reason"),
                reviewer=actor,
            )
            screening_actions.append(
                {
                    "topic_id": collection["topic_id"],
                    "run_id": collection["run_id"],
                    "stage": decision["stage"],
                    "decision": decision["decision"],
                    "primary_exclusion_reason": decision.get("primary_exclusion_reason"),
                }
            )
        self.store.record_event(
            "paper",
            paper_id,
            "autonomous_screening_completed",
            actor=actor,
            detail={**context, "decisions": screening_actions},
        )

        item = self.store.get_review_item(paper_id)
        assert item is not None
        if not item["collections"]:
            return self._automation_attention(
                paper_id,
                actor=actor,
                requested_by=requester,
                stage="topic_governance",
                reason="paper is not linked to a completed collection for a locked topic",
                trace=trace,
            )
        included = [
            collection
            for collection in item["collections"]
            if collection.get("full_text_decision") == "included"
        ]
        if not included:
            self.reject_paper(paper_id, reviewer="ai:screening-ledger")
            result = {"status": "completed", "decision": "excluded", "cards": []}
            self.store.record_event(
                "paper",
                paper_id,
                "autonomous_review_completed",
                actor=actor,
                detail={**context, **result},
            )
            return result

        guidance = item["review_guidance"]
        if reopened_screening_rejection:
            execution_check = next(
                check for check in guidance["checks"] if check["id"] == "executing_actor"
            )
            guidance = {
                **guidance,
                "blockers": [
                    blocker
                    for blocker in guidance["blockers"]
                    if blocker != execution_check["detail"]
                ],
            }
            self.store.record_event(
                "paper",
                paper_id,
                "autonomous_rejection_reopened",
                actor=actor,
                detail={
                    **context,
                    "previous_reviewer": admission.get("reviewer"),
                    "reason": "a non-excluded collection exists for a new screening evaluation",
                },
            )
        consistency_resolution = (item.get("admission") or {}).get("consistency_resolution")
        automatically_adjudicated = False
        if (item.get("consistency") or {}).get("verdict") == "needs_review":
            automatically_adjudicated = not bool(consistency_resolution)
            consistency_resolution = consistency_resolution or _automatic_resolution(
                guidance["issues"]
            )
            if automatically_adjudicated:
                self.store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_consistency_adjudicated",
                    actor=actor,
                    detail={
                        **context,
                        "issues": guidance["issues"],
                        "consistency_resolution": consistency_resolution,
                    },
                )
        blockers = list(guidance["blockers"])
        if automatically_adjudicated:
            dual_ai_blockers = {
                str(check["detail"])
                for check in guidance["checks"]
                if check["id"] == "dual_ai"
            }
            blockers = [blocker for blocker in blockers if blocker not in dual_ai_blockers]
        if blockers:
            return self._automation_attention(
                paper_id,
                actor=actor,
                requested_by=requester,
                stage="evidence_gate",
                reason=str(blockers[0]),
                trace=trace,
            )
        suggested = guidance["admission_suggestion"]
        study_design = str(suggested["study_design"])
        if study_design == "uncertain":
            return self._automation_attention(
                paper_id,
                actor=actor,
                requested_by=requester,
                stage="study_identity",
                reason="study design remains uncertain after independent extraction review",
                trace=trace,
            )
        if (item.get("admission") or {}).get("status") != "internally_admitted":
            self.admit_paper(
                paper_id,
                reviewer=actor,
                condition_codes=list(suggested["condition_codes"]),
                consistency_resolution=consistency_resolution,
                differences_confirmed=True,
                study_design=study_design,
                publication_role=suggested["publication_role"],
                identity_confirmed=True,
            )
        self.store.record_event(
            "paper",
            paper_id,
            "autonomous_admission_completed",
            actor=actor,
            detail={
                **context,
                "condition_codes": suggested["condition_codes"],
                "study_design": study_design,
                "publication_role": suggested["publication_role"],
                "consistency_resolution": consistency_resolution,
            },
        )

        item = self.store.get_review_item(paper_id)
        assert item is not None
        admitted_conditions = list((item.get("admission") or {}).get("condition_codes") or [])
        reviewed_claims: list[str] = []
        for claim in item["claims"]:
            ai_rejection_reopened = (
                claim.get("status") == "rejected"
                and str(claim.get("reviewer") or "").startswith("ai:")
                and (claim.get("review_suggestion") or {}).get("decision") == "approved"
            )
            ai_approval_recheck = (
                claim.get("status") == "reviewed"
                and claim.get("decision") == "approved"
                and str(claim.get("reviewer") or "").startswith("ai:")
                and (claim.get("review_suggestion") or {}).get("decision") == "approved"
            )
            if (
                claim.get("status") != "candidate"
                and not ai_rejection_reopened
                and not ai_approval_recheck
            ):
                continue
            suggestion = claim.get("review_suggestion") or {}
            source_verification = None
            if suggestion.get("decision") == "approved":
                source_verification = _automatic_source_verification(
                    claim,
                    extraction=item.get("extraction"),
                    trace=trace,
                )
                self.store.record_event(
                    "claim",
                    str(claim["id"]),
                    "autonomous_claim_source_verification",
                    actor=actor,
                    detail={**context, **source_verification},
                )
                if not source_verification["verified"]:
                    return self._automation_attention(
                        paper_id,
                        actor=actor,
                        requested_by=requester,
                        stage="claim_source_verification",
                        reason=(
                            f"Claim {claim['id']} cannot be verified: "
                            f"{'; '.join(source_verification['failures'])}"
                        ),
                        trace=trace,
                    )
            review = _automatic_claim_review(
                claim,
                issues=guidance["issues"],
                admitted_conditions=admitted_conditions,
                source_verified=bool(source_verification and source_verification["verified"]),
            )
            if review is None:
                return self._automation_attention(
                    paper_id,
                    actor=actor,
                    requested_by=requester,
                    stage="claim_review",
                    reason=f"Claim {claim['id']} has ambiguous condition or source evidence",
                    trace=trace,
                )
            if ai_approval_recheck and not _claim_review_changed(claim, review):
                continue
            self.review_claim(str(claim["id"]), reviewer=actor, review=review)
            reviewed_claims.append(str(claim["id"]))
        self.store.record_event(
            "paper",
            paper_id,
            "autonomous_claim_review_completed",
            actor=actor,
            detail={**context, "reviewed_claim_ids": reviewed_claims},
        )

        cards = self._build_ready_profiles(paper_id, actor=actor, context=context)
        result = {
            "status": "completed",
            "decision": "internally_admitted",
            "reviewed_claims": len(reviewed_claims),
            "cards": cards,
        }
        self.store.record_event(
            "paper",
            paper_id,
            "autonomous_review_completed",
            actor=actor,
            detail={**context, **result},
        )
        return result

    def _build_ready_profiles(
        self, paper_id: str, *, actor: str, context: dict[str, object]
    ) -> list[dict[str, object]]:
        existing = {
            (str(card["condition_code"]), str(card["version"])): card
            for card in self.store.list_cards()
        }
        results: list[dict[str, object]] = []
        for candidate in self.store.list_profile_candidates(paper_id):
            if candidate["status"] != "ready":
                results.append(
                    {
                        "topic_id": candidate["id"],
                        "status": "waiting_for_complete_evidence_body",
                        "reason": candidate["reason"],
                    }
                )
                continue
            groups = candidate["groups"]
            if not groups:
                results.append(
                    {
                        "topic_id": candidate["id"],
                        "status": "no_matching_picots_result_scope",
                        "reason": "approved results do not match the locked topic PICOTS scope",
                    }
                )
                continue
            for group in groups:
                scope_key = str(group["scope_key"])
                profile, patient_body, grade_domains = _automatic_profile(candidate, group)
                claim_ids = [str(claim["id"]) for claim in group["claims"]]
                card = next(
                    (
                        existing_card
                        for existing_card in existing.values()
                        if str(existing_card["topic_id"]) == str(candidate["id"])
                        and str(existing_card["scope_key"]) == scope_key
                        and set(existing_card["claim_ids"]) == set(claim_ids)
                        and str(existing_card["grade"]) == profile.certainty
                        and existing_card["status"] not in {"rejected", "stale"}
                    ),
                    None,
                )
                if card:
                    version = str(card["version"])
                else:
                    version = _next_profile_version(
                        str(candidate["version"]),
                        {
                            value
                            for condition, value in existing
                            if condition == str(candidate["condition_code"])
                        },
                    )
                key = (str(candidate["condition_code"]), version)
                card_id = str(card["id"]) if card else ""
                card_status = str(card["status"]) if card else "draft"
                try:
                    if not card:
                        card_id = self.create_card_draft(
                            topic_id=str(candidate["id"]),
                            condition_code=str(candidate["condition_code"]),
                            version=version,
                            claim_ids=claim_ids,
                            reviewer=actor,
                            patient_body=patient_body,
                            profile=profile,
                        )
                    if card_status == "draft":
                        self.transition_card(card_id, reviewer=actor, target="in_review")
                        card_status = "in_review"
                    if card_status == "in_review":
                        self.transition_card(card_id, reviewer=actor, target="approved")
                        card_status = "approved"
                except (ValueError, sqlite3.IntegrityError) as exc:
                    detail = {
                        "topic_id": candidate["id"],
                        "version": version,
                        "status": "attention_required",
                        "reason": str(exc),
                        "grade_domains": grade_domains,
                    }
                    self.store.record_event(
                        "paper",
                        paper_id,
                        "autonomous_profile_attention_required",
                        actor=actor,
                        detail={**context, **detail},
                    )
                    results.append(detail)
                    continue
                status = card_status
                if card_status == "approved" and profile.certainty in {"high", "moderate"}:
                    try:
                        self.transition_card(card_id, reviewer=actor, target="published")
                        status = "published"
                    except ValueError as exc:
                        status = "approved_evidence_limited"
                        publication_reason = str(exc)
                detail = {
                    "card_id": card_id,
                    "topic_id": candidate["id"],
                    "version": version,
                    "status": status,
                    "certainty": profile.certainty,
                    "grade_domains": grade_domains,
                }
                if status == "approved_evidence_limited":
                    detail["reason"] = publication_reason
                self.store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_profile_completed",
                    actor=actor,
                    detail={**context, **detail},
                )
                results.append(detail)
                existing[key] = {
                    "id": card_id,
                    "topic_id": candidate["id"],
                    "scope_key": scope_key,
                    "claim_ids": claim_ids,
                    "grade": profile.certainty,
                    "status": status,
                }
        return results

    def _automation_attention(
        self,
        paper_id: str,
        *,
        actor: str,
        requested_by: str,
        stage: str,
        reason: str,
        trace: dict[str, object],
    ) -> dict[str, object]:
        result = {"status": "attention_required", "stage": stage, "reason": reason}
        self.store.record_event(
            "paper",
            paper_id,
            "autonomous_review_attention_required",
            actor=actor,
            detail={
                "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
                "requested_by": requested_by,
                "extraction_trace": trace,
                **result,
            },
        )
        return result

    def admit_paper(
        self,
        paper_id: str,
        *,
        reviewer: str,
        condition_codes: list[str],
        consistency_resolution: str | None = None,
        differences_confirmed: bool,
        study_design: StudyDesign,
        publication_role: PublicationRole,
        identity_confirmed: bool,
    ) -> None:
        self.store.admit_paper(
            paper_id,
            reviewer=_reviewer(reviewer),
            condition_codes=tuple(
                dict.fromkeys(code.strip() for code in condition_codes if code.strip())
            ),
            consistency_resolution=(consistency_resolution or "").strip() or None,
            differences_confirmed=differences_confirmed,
            study_design=study_design,
            publication_role=publication_role,
            identity_confirmed=identity_confirmed,
        )

    def reject_paper(self, paper_id: str, *, reviewer: str) -> None:
        self.store.reject_paper(paper_id, reviewer=_reviewer(reviewer))

    def review_claim(self, claim_id: str, *, reviewer: str, review: ClaimReviewInput) -> None:
        values = review.model_dump()
        if review.decision == "rejected":
            values.update(
                corrected_text=None,
                corrected_study_design=None,
                inference=None,
                risk_of_bias=None,
                applicability=None,
                condition_code=None,
            )
        risk_of_bias = values.pop("risk_of_bias")
        values["risk_of_bias"] = risk_of_bias
        self.store.review_claim(claim_id, reviewer=_reviewer(reviewer), **values)

    def create_card_draft(
        self,
        *,
        topic_id: str,
        condition_code: str,
        version: str,
        claim_ids: list[str],
        reviewer: str,
        patient_body: str,
        profile: EvidenceProfileInput,
    ) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("knowledge card version must use semantic versioning")
        profile = EvidenceProfileInput.model_validate(profile)
        return self.store.create_card(
            condition_code=condition_code,
            topic_id=topic_id,
            version=version,
            claim_ids=tuple(dict.fromkeys(claim_ids)),
            reviewer=_reviewer(reviewer),
            patient_body=patient_body,
            profile=profile.model_dump(mode="json"),
        )

    def transition_card(self, card_id: str, *, reviewer: str, target: str) -> None:
        self.store.transition_card(card_id, reviewer=_reviewer(reviewer), target=target)


def _automatic_resolution(issues: list[dict[str, object]]) -> str:
    lines = [
        f"AI consistency adjudication ({AUTONOMOUS_REVIEW_POLICY_VERSION}): "
        "the primary extraction remains the structured source of record; each checker issue "
        "is retained below for audit and raises downstream risk-of-bias when material."
    ]
    lines.extend(
        f"[{issue.get('severity', 'unknown')}] {issue.get('field', 'difference')}: "
        f"{issue.get('message', '')} Evidence: {issue.get('evidence', '')}"
        for issue in issues
    )
    return "\n".join(lines)[:5000]


def _automatic_claim_review(
    claim: dict[str, object],
    *,
    issues: list[dict[str, object]],
    admitted_conditions: list[str],
    source_verified: bool,
) -> ClaimReviewInput | None:
    suggestion = claim.get("review_suggestion") or {}
    suggested_decision = str(suggestion.get("decision") or "")
    if suggested_decision == "rejected":
        return ClaimReviewInput(decision="rejected")
    if suggested_decision != "approved":
        return None
    condition_code = str(suggestion.get("condition_code") or "")
    if not condition_code and len(admitted_conditions) == 1:
        condition_code = admitted_conditions[0]
    required_source = (
        "result_id",
        "evidence_text",
        "locator",
        "population",
        "ingredient_name",
        "ingredient_form",
        "dose",
        "comparator",
        "outcome",
        "timepoint",
        "effect_estimate",
        "statistical_details",
    )
    if condition_code not in admitted_conditions or any(
        not str(claim.get(field) or "").strip() for field in required_source
    ):
        return None
    design = str(suggestion.get("corrected_study_design") or "uncertain")
    if design == "uncertain":
        return None
    inference = str(suggestion.get("inference") or "descriptive")
    if design in OBSERVATIONAL_DESIGNS and inference == "causal":
        inference = "associational"
    risk = dict(suggestion.get("risk_of_bias") or {})
    material = [
        issue
        for issue in issues
        if issue.get("priority") == "must_resolve" and _issue_applies_to_claim(issue, claim)
    ]
    reported_limitations = str(risk.get("rationale") or "").casefold()
    reported_high_risk = any(
        token in reported_limitations
        for token in (
            "high risk of bias",
            "critical risk of bias",
            "very low certainty",
            "very-low certainty",
            "高偏倚风险",
            "证据确定性极低",
        )
    )
    risk["overall"] = "high" if material or reported_high_risk else "some_concerns"
    risk["rationale"] = (
        str(risk.get("rationale") or "AI review found no extracted limitations.")
        + (
            " Material independent-extraction differences were retained in the audit trail, so "
            "this result "
            "is conservatively rated high risk."
            if material
            else (
                " Independent extraction source checks found no unresolved material issue for "
                "this result."
            )
        )
    )[:4000]
    return ClaimReviewInput(
        decision="approved",
        corrected_text=str(suggestion.get("corrected_text") or claim.get("candidate_text") or ""),
        corrected_study_design=design,
        inference=inference,
        risk_of_bias=risk,
        applicability=str(suggestion.get("applicability") or "AI applicability review completed."),
        condition_code=condition_code,
        source_verified=source_verified,
    )


def _automatic_source_verification(
    claim: dict[str, object],
    *,
    extraction: object,
    trace: dict[str, object],
) -> dict[str, object]:
    """Verify the persisted source chain before an autonomous Claim approval."""

    failures: list[str] = []
    result_id = str(claim.get("result_id") or "").strip()
    evidence = _source_text(claim.get("evidence_text"))
    locator = _source_text(claim.get("locator"))
    if not result_id:
        failures.append("missing structured Result")
    if not evidence or not locator:
        failures.append("missing Claim evidence or locator")
    if evidence != _source_text(claim.get("result_evidence_text")) or locator != _source_text(
        claim.get("result_locator")
    ):
        failures.append("Claim does not match its structured Result source")
    extraction_id = str(trace.get("id") or "").strip()
    if extraction_id != str(claim.get("extraction_id") or "").strip() or extraction_id != str(
        claim.get("result_extraction_id") or ""
    ).strip():
        failures.append("Claim and Result are not linked to the current extraction")
    required_runs = (
        "model",
        "extraction_run_id",
        "second_model",
        "second_run_id",
        "check_model",
        "check_run_id",
    )
    if any(not str(trace.get(name) or "").strip() for name in required_runs):
        failures.append("missing persisted extraction provider run")
    extracted_claims = extraction.get("claims", []) if isinstance(extraction, dict) else []
    if not any(
        isinstance(extracted, dict)
        and evidence in _source_text(extracted.get("evidence"))
        and locator == _source_text(extracted.get("locator"))
        for extracted in extracted_claims
    ):
        failures.append("evidence excerpt is absent from the primary stored extraction")
    return {
        "verified": not failures,
        "verification_policy": AUTONOMOUS_REVIEW_POLICY_VERSION,
        "result_id": result_id,
        "evidence_source": "paper_extractions.extraction_json",
        "failures": failures,
    }


def _source_text(value: object) -> str:
    return re.sub(r"\\s+", " ", str(value or "")).strip().casefold()


def _next_profile_version(topic_version: str, used_versions: set[str]) -> str:
    parts = [int(value) for value in re.findall(r"\d+", topic_version)[:3]]
    major, minor, patch = (*parts, 0, 0, 0)[:3]
    used_patches = [
        int(match.group(1))
        for version in used_versions
        if (match := re.fullmatch(rf"{major}\.{minor}\.(\d+)", version))
    ]
    if used_patches:
        patch = max(patch, max(used_patches) + 1)
    return f"{major}.{minor}.{patch}"


def _claim_review_changed(claim: dict[str, object], review: ClaimReviewInput) -> bool:
    risk = review.risk_of_bias.model_dump() if review.risk_of_bias else None
    return any(
        (
            claim.get("decision") != review.decision,
            claim.get("corrected_text") != review.corrected_text,
            claim.get("corrected_study_design") != review.corrected_study_design,
            claim.get("inference") != review.inference,
            claim.get("risk_of_bias") != risk,
            claim.get("applicability") != review.applicability,
            claim.get("condition_code") != review.condition_code,
        )
    )


def _automatic_profile(
    candidate: dict[str, object], group: dict[str, object]
) -> tuple[EvidenceProfileInput, str, dict[str, str]]:
    claims = group["claims"]
    dimensions = group["dimensions"]
    designs = {str(claim["corrected_study_design"]) for claim in claims}
    randomized = {
        "randomized_controlled_trial",
        "systematic_review_meta_analysis",
        "controlled_feeding_metabolic_study",
    }
    score = 3 if designs <= randomized else 1
    risk_overall = {str(claim["risk_of_bias"]["overall"]) for claim in claims}
    risk_domain = (
        "very_serious"
        if risk_overall & {"high", "critical"}
        else ("serious" if risk_overall & {"some_concerns", "uncertain"} else "not_serious")
    )
    picots = candidate.get("picots") or {}
    missing_scope = any(
        str(picots.get(topic_field) or "").strip() and _not_reported(str(dimensions[result_field]))
        for topic_field, result_field in (
            ("population", "population"),
            ("intervention_or_exposure", "ingredient_name"),
            ("comparator", "comparator"),
            ("outcomes", "outcome"),
            ("timing", "timepoint"),
        )
    )
    indirectness = "serious" if missing_scope else "not_serious"
    precise = all(
        _has_precision(f"{claim['effect_estimate']} {claim['statistical_details']}")
        for claim in claims
    )
    imprecision = "not_serious" if precise else "serious"
    paper_count = len({str(claim["paper_id"]) for claim in claims})
    study_count = len({str(claim["study_id"]) for claim in claims})
    interpretations = {str(claim["id"]): _interpretation(claim) for claim in claims}
    synthesis = set(interpretations.values())
    if synthesis & {"mixed", "uncertain", "not_reported"}:
        inconsistency = "serious"
    elif paper_count == 1:
        inconsistency = "not_assessable"
    elif synthesis in ({"supports"}, {"does_not_support"}):
        inconsistency = "not_serious"
    else:
        # A missing or contradictory direction is not safe to treat as consistent.
        inconsistency = "serious"
    domains = {
        "risk_of_bias": risk_domain,
        "inconsistency": inconsistency,
        "indirectness": indirectness,
        "imprecision": imprecision,
        "publication_bias": "not_assessable",
    }
    downgrade = {"not_serious": 0, "serious": 1, "very_serious": 2}
    score = max(
        0,
        score
        - sum(
            downgrade[value]
            for value in (risk_domain, inconsistency, indirectness, imprecision)
            if value in downgrade
        ),
    )
    single_primary_study = study_count == 1 and "systematic_review_meta_analysis" not in designs
    score = min(score, 1 if single_primary_study else 2)
    certainty = {0: "very_low", 1: "low", 2: "moderate"}[score]
    target = (
        f"{dimensions['population']}; {dimensions['ingredient_name']} "
        f"({dimensions['ingredient_form']}, {dimensions['dose']}) versus "
        f"{dimensions['comparator']}; {dimensions['outcome']} at {dimensions['timepoint']}"
    )
    rationale = (
        f"AI GRADE assessment ({AUTONOMOUS_REVIEW_POLICY_VERSION}): "
        + "; ".join(f"{key}={value}" for key, value in domains.items())
        + (
            "; single-study evidence is capped at low certainty."
            if single_primary_study
            else "; AI-only synthesis is capped at moderate certainty."
        )
    )
    certainty_label = {"moderate": "中等", "low": "低", "very_low": "极低"}[certainty]
    conclusion = (
        "结果整体支持上述研究关系。"
        if synthesis == {"supports"}
        else (
            "结果未支持上述研究关系。"
            if synthesis == {"does_not_support"}
            else "不同结果对上述研究关系的支持并不一致。"
        )
    )
    patient_body = (
        f"关于{candidate['condition_name']}，截至{candidate['evidence_cutoff_date']}的已审核研究"
        f"在{dimensions['population']}中评估了{dimensions['ingredient_name']}"
        f"（{dimensions['ingredient_form']}，{dimensions['dose']}）与"
        f"{dimensions['outcome']}的关系，比较条件为{dimensions['comparator']}，"
        f"观察时间为{dimensions['timepoint']}。{conclusion}当前证据确定性为{certainty_label}，"
        "适用范围以所列研究人群和条件为限。"
    )
    profile = EvidenceProfileInput(
        scope_key=str(group["scope_key"]),
        certainty=certainty,
        certainty_rationale=rationale,
        estimate_target=target,
        interpretations=interpretations,
    )
    return profile, patient_body, domains


def _interpretation(claim: dict[str, object]) -> str:
    text = " ".join(
        str(claim.get(field) or "")
        for field in ("candidate_text", "effect_estimate", "statistical_details")
    ).casefold()
    if any(
        phrase in text
        for phrase in (
            "did not significantly",
            "not significantly",
            "no significant",
            "no association",
            "not associated",
            "null effect",
            "无显著",
            "未见显著",
        )
    ):
        return "does_not_support"
    return "mixed" if "mixed" in text or "不一致" in text else "supports"


def _issue_applies_to_claim(issue: dict[str, object], claim: dict[str, object]) -> bool:
    field = str(issue.get("field") or "").casefold()
    if not re.match(r"^claims?(?:$|[.\[])", field):
        return True
    text = " ".join((field, *(str(issue.get(key) or "") for key in ("message", "evidence"))))
    index = claim.get("extraction_claim_index")
    if not index:
        return True
    bracketed = re.search(r"claims?\[(\d+)\]", text.casefold())
    if bracketed:
        return int(index) == int(bracketed.group(1)) + 1
    matches = re.findall(r"\bclaims?\s*(\d+)(?:\s*[-–]\s*(\d+))?", text.casefold())
    if not matches:
        additional_claim_only = any(
            phrase in text.casefold()
            for phrase in (
                "not present in extraction",
                "does not include",
                "not included in extraction",
            )
        )
        return not additional_claim_only
    claim_index = int(index)
    return any(int(start) <= claim_index <= int(end or start) for start, end in matches)


def _not_reported(value: str) -> bool:
    normalized = value.strip().casefold()
    return not normalized or normalized in {"未报告", "not reported", "not applicable", "n/a"}


def _has_precision(value: str) -> bool:
    normalized = value.casefold().replace(" ", "")
    return any(token in normalized for token in ("95%ci", "confidenceinterval", "p<", "p=", "p≤"))


def _reviewer(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("reviewer identity is required")
    return normalized
