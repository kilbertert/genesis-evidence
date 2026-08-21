from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.core.store.review import _picots_text_matches, _profile_scopes
from genesis_evidence.review.service import (
    ClaimReviewInput,
    EvidenceProfileInput,
    EvidenceReviewService,
    RiskOfBiasInput,
    _next_profile_version,
)


def _review_case(
    database: Database,
    *,
    integrity: str = "clear",
    consistency: str = "consistent",
    publication_status: str = "formal",
    screened: bool = True,
) -> tuple[str, str]:
    paper_id = str(uuid.uuid4())
    extraction_id = str(uuid.uuid4())
    study_id = str(uuid.uuid4())
    result_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    extraction_payload = json.dumps(
        {
            "study_design": "cohort_study",
            "population": ["Adults aged 60 years and older"],
            "studied_approach": ["Measured serum 25(OH)D"],
            "outcomes": ["Frailty prevalence"],
            "condition_candidates": [{"condition_code": "COND_VITAMIN_D_DEFICIENCY"}],
            "claims": [
                {
                    "text": "Lower vitamin D was associated with frailty.",
                    "evidence": ("Lower 25(OH)D was associated with higher frailty prevalence."),
                    "locator": "Results",
                    "inference": "associational",
                    "ingredient_name": "Vitamin D",
                    "ingredient_form": "25(OH)D status",
                    "comparator": "Higher versus lower status",
                    "outcome": "Frailty prevalence",
                    "timepoint": "Baseline",
                }
            ],
            "limitations": [],
        }
    )
    consistency_payload = json.dumps(
        {
            "verdict": consistency,
            "issues": []
            if consistency == "consistent"
            else [
                {
                    "field": "summary.wording",
                    "severity": "medium",
                    "message": "Independent summaries use different wording.",
                    "evidence": "Results",
                }
            ],
        }
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(
                id, title, doi, publication_status, integrity_status, created_at
            ) VALUES (?, 'Vitamin D and frailty', ?, ?, ?, '2026-08-11T00:00:00Z')
            """,
            (paper_id, f"10.1000/test-vitamin-d-{paper_id}", publication_status, integrity),
        )
        connection.execute(
            """
            INSERT INTO paper_sources(paper_id, source, source_id, source_url)
            VALUES (?, 'europe_pmc', ?, 'https://example.test/paper')
            """,
            (paper_id, paper_id),
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES (?, ?, 'model-a', 'extract-run-a', ?, 'model-b',
                'extract-run-b', ?, 'checker', 'check-run', ?, ?,
                '2026-08-11T00:00:00Z')
            """,
            (
                extraction_id,
                paper_id,
                extraction_payload,
                extraction_payload,
                consistency,
                consistency_payload,
            ),
        )
        connection.execute(
            """
            INSERT INTO studies(id, study_design, created_at)
            VALUES (?, 'cohort_study', '2026-08-11T00:00:00Z')
            """,
            (study_id,),
        )
        connection.execute(
            "INSERT INTO study_publications(study_id, paper_id) VALUES (?, ?)",
            (study_id, paper_id),
        )
        connection.execute(
            """
            INSERT INTO results(
                id, study_id, paper_id, extraction_id, population,
                baseline_nutrient_status, ingredient_name, ingredient_form,
                dose, comparator, outcome, timepoint, effect_estimate,
                statistical_details, evidence_text, locator, created_at
            ) VALUES (?, ?, ?, ?, 'Adults aged 60 years and older',
                'Measured serum 25(OH)D', 'Vitamin D', '25(OH)D status',
                'Not applicable', 'Higher versus lower status', 'Frailty prevalence',
                'Baseline', 'Higher prevalence', 'Adjusted association reported',
                'Lower 25(OH)D was associated with higher frailty prevalence.',
                'Results', '2026-08-11T00:00:00Z')
            """,
            (result_id, study_id, paper_id, extraction_id),
        )
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, result_id, candidate_text,
                evidence_text, locator, candidate_claim_type, candidate_study_design, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Results', 'intervention_effect', 'cohort_study',
                '2026-08-11T00:00:00Z')
            """,
            (
                claim_id,
                paper_id,
                extraction_id,
                result_id,
                "Lower vitamin D was associated with frailty.",
                "Lower 25(OH)D was associated with higher frailty prevalence.",
            ),
        )
        connection.execute(
            "INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')",
            (paper_id,),
        )
    if screened:
        _complete_topic(database, paper_id)
    return paper_id, claim_id


def _approved_review(condition_code: str = "COND_VITAMIN_D_DEFICIENCY") -> ClaimReviewInput:
    return ClaimReviewInput(
        decision="approved",
        corrected_text="Lower vitamin D status was associated with frailty.",
        corrected_study_design="cohort_study",
        inference="associational",
        risk_of_bias=RiskOfBiasInput(
            tool="exposure_study",
            overall="some_concerns",
            rationale="Residual confounding remains possible.",
        ),
        applicability="Applies to older adults with measured serum 25(OH)D.",
        condition_code=condition_code,
        source_verified=True,
    )


def _admit(
    service: EvidenceReviewService,
    paper_id: str,
    *,
    study_design: str = "cohort_study",
    consistency_resolution: str | None = None,
) -> None:
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        consistency_resolution=consistency_resolution,
        differences_confirmed=consistency_resolution is not None,
        study_design=study_design,
        publication_role="primary",
        identity_confirmed=True,
    )


def _profile(claim_id: str, *, certainty: str = "moderate") -> EvidenceProfileInput:
    return EvidenceProfileInput(
        certainty=certainty,
        certainty_rationale="The complete eligible evidence body was reviewed for this outcome.",
        estimate_target="Association between baseline 25(OH)D status and frailty prevalence",
        interpretations={claim_id: "supports"},
    )


def _complete_topic(
    database: Database,
    *paper_ids: str,
    eligible_study_designs: tuple[str, ...] = ("cohort_study",),
) -> str:
    store = PaperStore(database)
    topic_id = store.create_topic(
        code=f"vitamin-d-frailty-{uuid.uuid4()}",
        version="1",
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        review_question="Is vitamin D status associated with frailty in older adults?",
        picots={
            "population": "Adults aged 60 years and older",
            "intervention_or_exposure": "Measured serum 25(OH)D",
            "comparator": "Higher versus lower status",
            "outcomes": "Frailty prevalence",
            "timing": "Baseline",
            "setting": "Any human setting",
        },
        eligible_study_designs=eligible_study_designs,
        inclusion_criteria=("Older adults with measured serum 25(OH)D",),
        exclusion_reasons=("wrong_population", "wrong_exposure", "wrong_outcome"),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-12",
        reviewer="reviewer-1",
    )
    store.lock_topic(topic_id, reviewer="reviewer-1")
    run_id = store.start_collection(
        topic_id=topic_id,
        source="test",
        query="vitamin D AND frailty",
    )
    for position, paper_id in enumerate(paper_ids, 1):
        store.add_to_collection(run_id, paper_id, position=position)
    store.finish_collection(run_id, status="completed", detail={})
    for paper_id in paper_ids:
        store.screen_collection_paper(
            run_id,
            paper_id,
            stage="title_abstract",
            decision="included",
            exclusion_reason=None,
            reviewer="reviewer-1",
        )
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO full_texts(
                    paper_id, object_key, sha256, media_type, rights_status, processed_at
                ) VALUES (?, ?, ?, 'application/xml', 'redistributable', '2026-08-11T00:00:00Z')
                ON CONFLICT(paper_id) DO NOTHING
                """,
                (paper_id, f"test/{paper_id}.xml", "0" * 64),
            )
        store.screen_collection_paper(
            run_id,
            paper_id,
            stage="full_text",
            decision="included",
            exclusion_reason=None,
            reviewer="reviewer-1",
        )
    return topic_id


def _service(tmp_path) -> tuple[Database, EvidenceReviewService]:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    return database, EvidenceReviewService(ReviewStore(database), PaperStore(database))


def test_paper_requires_clear_integrity_before_internal_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, integrity="unknown")
    with pytest.raises(ValueError, match="integrity"):
        _admit(service, paper_id)


def test_paper_requires_completed_full_text_screening_before_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, screened=False)
    with pytest.raises(ValueError, match="full-text screening"):
        _admit(service, paper_id)


def test_rejected_paper_remains_blocked_in_review_guidance(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.reject_paper(paper_id, reviewer="reviewer-1")
    item = ReviewStore(database).get_review_item(paper_id)
    assert item is not None
    assert item["review_guidance"]["state"] == "blocked"
    assert "已被具名执行者拒绝" in item["review_guidance"]["next_action"]
    with database.connect() as connection:
        claim = connection.execute(
            "SELECT c.status, cr.decision, cr.reviewer FROM claims c "
            "JOIN claim_reviews cr ON cr.claim_id = c.id WHERE c.id = ?",
            (claim_id,),
        ).fetchone()
        audit = connection.execute(
            "SELECT action, actor FROM audit_events WHERE entity_type = 'claim' "
            "AND entity_id = ? ORDER BY id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
    assert tuple(claim) == ("rejected", "rejected", "reviewer-1")
    assert tuple(audit) == ("claim_rejected_by_paper", "reviewer-1")


def test_ai_screening_rejection_is_reopened_for_a_new_topic_version(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE collection_papers SET title_abstract_decision = 'excluded', "
            "full_text_decision = NULL WHERE paper_id = ?",
            (paper_id,),
        )
    service.reject_paper(paper_id, reviewer="ai:screening-ledger")
    new_topic_id = _complete_topic(database, paper_id)
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE collection_papers SET title_abstract_decision = NULL,
                title_abstract_reviewer = NULL, title_abstract_reviewed_at = NULL,
                full_text_decision = NULL, full_text_reviewer = NULL,
                full_text_reviewed_at = NULL, primary_exclusion_reason = NULL
            WHERE paper_id = ? AND run_id IN (
                SELECT id FROM collection_runs WHERE topic_id = ?
            )
            """,
            (paper_id, new_topic_id),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed", result
    assert result["decision"] == "internally_admitted"
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, reviewer FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()
    assert tuple(admission) == ("internally_admitted", "ai:checker")


def test_ai_closes_legacy_candidate_claims_for_an_already_rejected_paper(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE collection_papers SET title_abstract_decision = 'excluded', "
            "full_text_decision = NULL WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE paper_admissions SET status = 'rejected', reviewer = 'reviewer-1' "
            "WHERE paper_id = ?",
            (paper_id,),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["decision"] == "excluded"
    with database.connect() as connection:
        claim = connection.execute(
            "SELECT c.status, cr.decision, cr.reviewer FROM claims c "
            "JOIN claim_reviews cr ON cr.claim_id = c.id WHERE c.id = ?",
            (claim_id,),
        ).fetchone()
        admission = connection.execute(
            "SELECT status, reviewer FROM paper_admissions WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
    assert tuple(claim) == ("rejected", "rejected", "reviewer-1")
    assert tuple(admission) == ("rejected", "reviewer-1")


def test_ai_does_not_reopen_a_named_rejection(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    service.reject_paper(paper_id, reviewer="reviewer-1")

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "attention_required"
    assert result["stage"] == "admission"


def test_ai_reopens_its_own_screening_rejection_after_picots_update(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE collection_papers SET title_abstract_decision = 'included', "
            "full_text_decision = 'excluded', primary_exclusion_reason = 'wrong_population', "
            "title_abstract_reviewer = 'ai:old-model', full_text_reviewer = 'ai:old-model' "
            "WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE paper_admissions SET status = 'rejected', reviewer = 'ai:old-model' "
            "WHERE paper_id = ?",
            (paper_id,),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["decision"] == "internally_admitted"
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, reviewer FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()
    assert tuple(admission) == ("internally_admitted", "ai:checker")


def test_ai_review_guidance_drives_screening_and_downgrades_material_differences(tmp_path) -> None:
    database, _ = _service(tmp_path)
    paper_id, claim_id = _review_case(database, consistency="needs_review")
    with database.transaction() as connection:
        run_id = connection.execute(
            "SELECT run_id FROM collection_papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE collection_papers SET title_abstract_decision = NULL,
                title_abstract_reviewer = NULL, title_abstract_reviewed_at = NULL,
                full_text_decision = NULL, full_text_reviewer = NULL,
                full_text_reviewed_at = NULL, primary_exclusion_reason = NULL
            WHERE run_id = ? AND paper_id = ?
            """,
            (run_id, paper_id),
        )
        connection.execute(
            """
            UPDATE paper_extractions SET extraction_json = ?, consistency_json = ?
            WHERE paper_id = ?
            """,
            (
                json.dumps(
                    {
                        "study_design": "cohort_study",
                        "population": ["Adults aged 60 years and older"],
                        "studied_approach": ["Measured serum 25(OH)D"],
                        "outcomes": ["Frailty prevalence"],
                        "condition_candidates": [{"condition_code": "COND_VITAMIN_D_DEFICIENCY"}],
                        "limitations": ["Residual confounding remains possible."],
                        "claims": [
                            {
                                "text": "Lower vitamin D was associated with frailty.",
                                "evidence": (
                                    "Lower 25(OH)D was associated with higher frailty prevalence."
                                ),
                                "locator": "Results",
                                "inference": "associational",
                                "ingredient_name": "Vitamin D",
                                "ingredient_form": "25(OH)D status",
                                "comparator": "Higher versus lower status",
                                "outcome": "Frailty prevalence",
                                "timepoint": "Baseline",
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "verdict": "needs_review",
                        "issues": [
                            {
                                "field": "claims[0].effect_estimate",
                                "severity": "high",
                                "message": (
                                    "The effect estimate differs between extraction A and B."
                                ),
                                "evidence": "Results table 2",
                            }
                        ],
                    }
                ),
                paper_id,
            ),
        )

    store = ReviewStore(database)
    item = store.get_review_item(paper_id)
    assert item is not None
    assert item["review_guidance"]["state"] == "ready_for_automation"
    assert item["collections"][0]["screening_suggestion"]["stage"] == "title_abstract"
    suggestion = item["claims"][0]["review_suggestion"]
    assert suggestion["inference"] == "associational"
    assert suggestion["risk_of_bias"]["tool"] == "exposure_study"
    assert suggestion["condition_code"] == "COND_VITAMIN_D_DEFICIENCY"
    assert item["claims"][0]["id"] == claim_id

    paper_store = PaperStore(database)
    paper_store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    item = store.get_review_item(paper_id)
    assert item["collections"][0]["screening_suggestion"]["stage"] == "full_text"
    assert item["collections"][0]["screening_suggestion"]["decision"] == "included"

    paper_store.screen_collection_paper(
        run_id,
        paper_id,
        stage="full_text",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    item = store.get_review_item(paper_id)
    assert item["review_guidance"]["state"] == "ready_for_automation"
    assert item["review_guidance"]["issues"][0]["priority"] == "must_resolve"
    assert (
        "effect estimate differs"
        in item["review_guidance"]["admission_suggestion"]["consistency_resolution"]
    )


def test_ai_full_text_screening_applies_locked_picots(tmp_path) -> None:
    database, _ = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE collection_papers SET full_text_decision = NULL,
                full_text_reviewer = NULL, full_text_reviewed_at = NULL
            WHERE paper_id = ?
            """,
            (paper_id,),
        )
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ? WHERE paper_id = ?",
            (
                json.dumps(
                    {
                        "study_design": "cohort_study",
                        "population": ["Adults aged 60 years and older"],
                        "studied_approach": ["Dietary sodium reduction"],
                        "outcomes": ["Frailty prevalence"],
                        "claims": [],
                        "limitations": [],
                    }
                ),
                paper_id,
            ),
        )

    item = ReviewStore(database).get_review_item(paper_id)

    assert item is not None
    suggestion = item["collections"][0]["screening_suggestion"]
    assert suggestion["stage"] == "full_text"
    assert suggestion["decision"] == "excluded"
    assert suggestion["primary_exclusion_reason"] == "wrong_exposure"


def test_ai_full_text_screening_stops_when_picots_evidence_is_missing(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE collection_papers SET full_text_decision = NULL WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ? WHERE paper_id = ?",
            (json.dumps({"study_design": "cohort_study", "claims": []}), paper_id),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "attention_required"
    assert result["stage"] == "screening"
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT full_text_decision FROM collection_papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            is None
        )


def test_ai_full_text_screening_enforces_topic_minimum_age(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        extraction = json.loads(
            connection.execute(
                "SELECT extraction_json FROM paper_extractions WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
        )
        extraction["population"] = ["Adult males aged 18-65 years"]
        connection.execute(
            "UPDATE collection_papers SET full_text_decision = NULL WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ? WHERE paper_id = ?",
            (json.dumps(extraction), paper_id),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    assert result["decision"] == "excluded"
    with database.connect() as connection:
        screening = connection.execute(
            "SELECT full_text_decision, primary_exclusion_reason FROM collection_papers "
            "WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        reviewer = connection.execute(
            "SELECT reviewer FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0]
    assert tuple(screening) == ("excluded", "wrong_population")
    assert reviewer == "ai:screening-ledger"


def test_picots_adult_scope_accepts_a_systematic_review_of_adults() -> None:
    population = "Adults (mean age >=18 years) from randomized trials"

    assert _picots_text_matches("Adults aged 18 and older", population)
    assert not _picots_text_matches("Adults aged 40 and older", population)


def test_picots_duration_normalizes_days_and_weeks() -> None:
    assert _picots_text_matches("At least 3 weeks", "After 30 days of intervention")
    assert not _picots_text_matches("At least 3 weeks", "After 1 week of intervention")


def test_picots_matches_common_chinese_population_terms() -> None:
    topic = "Adults aged 40 and older or postmenopausal adults"
    assert _picots_text_matches(topic, "绝经后女性")
    assert not _picots_text_matches(topic, "儿童")
    assert not _picots_text_matches("Adults aged 60 and older", "年龄 > 50 岁")


def test_picots_matches_collagen_peptides_as_a_protein_intervention() -> None:
    topic = "Calcium, vitamin D, protein, or dietary pattern intervention/exposure"

    assert _picots_text_matches(topic, "每日口服5 g特定生物活性胶原蛋白肽（SCP）")
    assert _picots_text_matches(topic, "5 g specific collagen peptides daily")


def test_picots_accepts_regular_milk_as_an_alternative_dietary_comparator() -> None:
    topic = "Placebo, no intervention, usual diet, or alternative dietary intervention"

    assert _picots_text_matches(topic, "普通鲜奶，每日400 mL")
    assert _picots_text_matches(topic, "regular milk, 400 mL daily")


def test_claim_review_matches_combination_intervention_details(tmp_path) -> None:
    database, _ = _service(tmp_path)
    paper_id, _ = _review_case(database)
    picots = {
        "population": "Adults aged 40 and older or postmenopausal adults",
        "intervention_or_exposure": (
            "Calcium, vitamin D, protein, or dietary pattern intervention/exposure"
        ),
        "outcomes": "Bone mineral density, T-score, calcium, or alkaline phosphatase",
        "timing": "At least 6 months or longer follow-up",
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE evidence_topics SET condition_code = ?, picots_json = ?",
            ("COND_OSTEOPOROSIS_RISK", json.dumps(picots)),
        )
        connection.execute(
            """
            UPDATE results SET population = 'postmenopausal women with osteopenia',
                ingredient_name = 'CalGo®',
                ingredient_form = 'salmon bone complex in a collagen-rich matrix',
                dose = '380 mg elemental calcium, 500 mg type II collagen, 40 µg vitamin D3',
                outcome = '24-month change in femoral-neck BMD', timepoint = '24 months'
            WHERE paper_id = ?
            """,
            (paper_id,),
        )

    item = ReviewStore(database).get_review_item(paper_id)
    assert item["claims"][0]["review_suggestion"]["condition_code"] == ("COND_OSTEOPOROSIS_RISK")

    for unrelated_outcome in ("adverse event incidence", "EQ-5D-3L index and EQ-VAS scores"):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE results SET outcome = ? WHERE paper_id = ?",
                (unrelated_outcome, paper_id),
            )
        item = ReviewStore(database).get_review_item(paper_id)
        assert item["claims"][0]["review_suggestion"]["decision"] == "rejected"


def test_picots_duration_normalizes_chinese_units() -> None:
    assert _picots_text_matches("At least 24 weeks", "干预周期 24 周")
    assert _picots_text_matches("At least 6 months", "干预周期 24 周至 24 个月")


def test_picots_matches_explicit_non_age_population_alternatives() -> None:
    assert _picots_text_matches(
        "Adults aged 40 and older or adults with metabolic risk",
        "Adults with metabolic dysfunction-associated steatotic liver disease",
    )
    assert _picots_text_matches(
        "Adults aged 40 and older or adults with kidney disease risk",
        "Adults with chronic kidney disease",
    )
    assert not _picots_text_matches(
        "Adults aged 40 and older or adults at nutritional risk",
        "Children at nutritional risk",
    )


def test_picots_matches_chinese_kidney_disease_population() -> None:
    assert _picots_text_matches(
        "Adults aged 40 and older or adults with kidney disease risk",
        "CKD患者，具体研究数量和样本量未报告",
        require_qualifiers=False,
    )


def test_profile_scope_uses_canonical_metric_and_ignores_ratio_outcomes() -> None:
    picots = {
        "population": "Adults aged 18 and older",
        "intervention_or_exposure": "Dietary oils and solid fats",
        "comparator": "Alternative dietary oil or solid fat",
        "outcomes": "LDL cholesterol, HDL cholesterol, triglycerides, or total cholesterol",
        "timing": "At least 3 weeks",
    }
    dimensions = {
        "population": "Adults aged 50-75 years",
        "ingredient_name": "Coconut oil",
        "outcome": "LDL-C levels",
        "timepoint": "After 4 weeks",
    }

    assert _profile_scopes(picots, "COND_DYSLIPIDEMIA", dimensions) == {
        "metric:ldl_c": "低密度脂蛋白胆固醇"
    }
    dimensions["outcome"] = "Change in TC/HDL-C ratio and non-HDL-C"
    assert _profile_scopes(picots, "COND_DYSLIPIDEMIA", dimensions) == {}

    picots["outcomes"] += ", non-HDL cholesterol"
    assert _profile_scopes(picots, "COND_DYSLIPIDEMIA", dimensions) == {
        "metric:non_hdl_c": "非高密度脂蛋白胆固醇"
    }

    dimensions["outcome"] = "Total body fat (%)"
    assert _profile_scopes(picots, "COND_DYSLIPIDEMIA", dimensions) == {}


def test_adult_40_plus_scope_accepts_explicit_postmenopausal_population() -> None:
    assert _picots_text_matches(
        "Adults aged 40 and older", "Postmenopausal women", require_qualifiers=False
    )
    assert not _picots_text_matches("Adults aged 40 and older", "Women", require_qualifiers=False)


def test_profile_scope_does_not_treat_concentrations_as_ratio() -> None:
    picots = {
        "population": "Adults aged 40 and older",
        "intervention_or_exposure": "Vitamin D supplementation",
        "outcomes": "25-hydroxyvitamin D serum concentration",
        "timing": "8 weeks or longer",
    }
    dimensions = {
        "population": "Postmenopausal women",
        "ingredient_name": "Vitamin D",
        "outcome": "Mean serum 25(OH)D concentrations",
        "timepoint": "After 12 weeks of treatment",
    }
    assert _profile_scopes(picots, "COND_VITAMIN_D_DEFICIENCY", dimensions) == {
        "metric:25_oh_vitamin_d": "25-羟维生素 D"
    }


def test_profile_scope_recognizes_bmd_as_bone_density() -> None:
    picots = {
        "population": "Adults aged 40 and older or postmenopausal adults",
        "intervention_or_exposure": "Calcium or vitamin D supplementation",
        "outcomes": "Bone mineral density or T-score",
        "timing": "At least 6 months",
    }
    dimensions = {
        "population": "Postmenopausal women",
        "ingredient_name": "Calcium and vitamin D",
        "outcome": "Lumbar spine BMD",
        "timepoint": "After 12 months",
    }

    assert _profile_scopes(picots, "COND_OSTEOPOROSIS_RISK", dimensions) == {
        "metric:bone_density_t_score": "骨密度 T 值"
    }
    dimensions["outcome"] = "腰椎L1-4骨密度变化百分比"
    assert _profile_scopes(picots, "COND_OSTEOPOROSIS_RISK", dimensions) == {
        "metric:bone_density_t_score": "骨密度 T 值"
    }


def test_profile_scope_keeps_bmd_when_result_also_reports_a_bone_ratio() -> None:
    picots = {
        "population": "Adults aged 40 and older or postmenopausal adults",
        "intervention_or_exposure": "Calcium or vitamin D supplementation",
        "outcomes": "Bone mineral density, calcium, or alkaline phosphatase",
        "timing": "At least 6 months",
    }
    dimensions = {
        "population": "Postmenopausal women aged 50-59 years",
        "ingredient_name": "Eggshell-derived calcium and vitamin D",
        "outcome": "Changes in BMD, serum calcium, and urine calcium/creatinine ratio",
        "timepoint": "After 6 months",
    }

    assert _profile_scopes(picots, "COND_OSTEOPOROSIS_RISK", dimensions) == {
        "metric:bone_density_t_score": "骨密度 T 值",
        "metric:calcium": "钙",
    }


def test_publishing_card_only_stales_the_same_outcome_scope(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES ('topic', 'lipid-scopes', '1', 'COND_DYSLIPIDEMIA', 'locked',
                'Test question', '{}', '[]', '[]', '[]', '[]', '2026-08-12', 'reviewer',
                '2026-08-12T00:00:00Z', 'reviewer', '2026-08-12T00:00:00Z')
            """
        )
        for profile_id, scope_key, version in (
            ("profile-old", "metric:ldl_c", "1.0.0"),
            ("profile-other", "metric:hdl_c", "1.0.1"),
            ("profile-new", "metric:ldl_c", "1.0.2"),
        ):
            connection.execute(
                """
                INSERT INTO evidence_profiles(
                    id, topic_id, condition_code, scope_key, version,
                    ingredient_name, ingredient_form,
                    population, baseline_nutrient_status, dose, comparator, outcome, timepoint,
                    estimate_target, evidence_body_complete, certainty, certainty_rationale,
                    evidence_cutoff_date, reviewer, reviewed_at, created_at
                ) VALUES (?, 'topic', 'COND_DYSLIPIDEMIA', ?, ?,
                    'Dietary fat', 'As reported',
                    'Adults', 'Mixed', 'As reported', 'Comparator', 'Lipid outcome',
                    'Follow-up', 'Target', 1, 'moderate', 'Test profile', '2026-08-12',
                    'reviewer', '2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z')
                """,
                (profile_id, scope_key, version),
            )
        for card_id, profile_id, status, version in (
            ("card-old", "profile-old", "published", "1.0.0"),
            ("card-other", "profile-other", "published", "1.0.1"),
            ("card-new", "profile-new", "approved", "1.0.2"),
        ):
            connection.execute(
                """
                INSERT INTO knowledge_cards(
                    id, condition_code, version, status, grade, evidence_profile_id, reviewer,
                    reviewed_at, published_at, patient_visible_body, created_at
                ) VALUES (?, 'COND_DYSLIPIDEMIA', ?, ?, 'moderate', ?, 'reviewer',
                    '2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z', 'Test card',
                    '2026-08-12T00:00:00Z')
                """,
                (card_id, version, status, profile_id),
            )
    monkeypatch.setattr(ReviewStore, "_require_publishable", lambda *_: None)

    ReviewStore(database).transition_card("card-new", reviewer="reviewer", target="published")

    with database.connect() as connection:
        statuses = dict(connection.execute("SELECT id, status FROM knowledge_cards").fetchall())
    assert statuses == {
        "card-new": "published",
        "card-old": "stale",
        "card-other": "published",
    }


def test_profile_version_is_monotonic_within_the_topic_series() -> None:
    assert _next_profile_version("4", {"3.0.9", "4.0.0", "4.0.2"}) == "4.0.3"


def test_claim_picots_uses_the_paper_population_context(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE results SET population = 'Patients with frailty' WHERE paper_id = ?",
            (paper_id,),
        )

    service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    with database.connect() as connection:
        decision = connection.execute(
            "SELECT decision FROM claim_reviews WHERE claim_id = ?", (claim_id,)
        ).fetchone()[0]
    assert decision == "approved"


def test_ai_reopens_only_its_own_claim_rejection(tmp_path) -> None:
    database, service = _service(tmp_path)
    ai_paper, ai_claim = _review_case(database)
    human_paper, human_claim = _review_case(database)
    _admit(service, ai_paper)
    _admit(service, human_paper)
    service.review_claim(
        ai_claim, reviewer="ai:old-policy", review=ClaimReviewInput(decision="rejected")
    )
    service.review_claim(
        human_claim, reviewer="reviewer-1", review=ClaimReviewInput(decision="rejected")
    )

    service.auto_review_paper(ai_paper, requested_by="authenticated-reviewer")
    service.auto_review_paper(human_paper, requested_by="authenticated-reviewer")

    with database.connect() as connection:
        decisions = {
            row["claim_id"]: tuple(row)[1:]
            for row in connection.execute(
                "SELECT claim_id, decision, reviewer FROM claim_reviews WHERE claim_id IN (?, ?)",
                (ai_claim, human_claim),
            ).fetchall()
        }
    assert decisions[ai_claim] == ("approved", "ai:checker")
    assert decisions[human_claim] == ("rejected", "reviewer-1")


def test_ai_completes_review_and_card_without_human_participation(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database, consistency="needs_review")
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE collection_papers SET title_abstract_decision = NULL,
                title_abstract_reviewer = NULL, title_abstract_reviewed_at = NULL,
                full_text_decision = NULL, full_text_reviewer = NULL,
                full_text_reviewed_at = NULL, primary_exclusion_reason = NULL
            WHERE paper_id = ?
            """,
            (paper_id,),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    assert result["decision"] == "internally_admitted"
    assert result["reviewed_claims"] == 1
    assert result["cards"][0]["status"] == "approved"
    assert result["cards"][0]["certainty"] in {"low", "very_low"}
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, reviewer, consistency_resolution FROM paper_admissions"
        ).fetchone()
        claim = connection.execute(
            "SELECT decision, reviewer, corrected_study_design, inference, risk_of_bias_json "
            "FROM claim_reviews WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        card = connection.execute("SELECT status, reviewer FROM knowledge_cards").fetchone()
        screening = connection.execute(
            "SELECT title_abstract_reviewer, full_text_reviewer FROM collection_papers"
        ).fetchone()
        events = connection.execute(
            "SELECT action, actor, detail_json FROM audit_events ORDER BY id"
        ).fetchall()
    assert tuple(admission[:2]) == ("internally_admitted", "ai:checker")
    assert "AI consistency adjudication" in admission["consistency_resolution"]
    assert tuple(claim[:4]) == ("approved", "ai:checker", "cohort_study", "associational")
    assert json.loads(claim["risk_of_bias_json"])["overall"] == "some_concerns"
    assert tuple(card) == ("approved", "ai:checker")
    assert tuple(screening) == ("ai:checker", "ai:checker")
    actions = {event["action"] for event in events}
    assert {
        "autonomous_review_started",
        "autonomous_screening_completed",
        "autonomous_admission_completed",
        "autonomous_claim_review_completed",
        "autonomous_profile_completed",
        "autonomous_review_completed",
    } <= actions
    started = next(event for event in events if event["action"] == "autonomous_review_started")
    trace = json.loads(started["detail_json"])["extraction_trace"]
    assert trace["extraction_run_id"] == "extract-run-a"
    assert trace["second_run_id"] == "extract-run-b"
    assert trace["check_run_id"] == "check-run"
    verification = next(
        event for event in events if event["action"] == "autonomous_claim_source_verification"
    )
    assert json.loads(verification["detail_json"])["verified"] is True
    service.review_claim(
        claim_id,
        reviewer="human-exception-reviewer",
        review=ClaimReviewInput(decision="rejected"),
    )
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM knowledge_cards").fetchone()[0] == "stale"


def test_ai_ignores_claims_from_an_older_extraction(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            )
            SELECT 'new-extraction', paper_id, model, 'new-extract-run', extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json,
                '2026-08-12T00:00:00Z'
            FROM paper_extractions WHERE paper_id = ?
            """,
            (paper_id,),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    assert result["reviewed_claims"] == 0
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM audit_events WHERE entity_type = 'claim' AND entity_id = ? "
                "AND action = 'autonomous_claim_source_verification'",
                (claim_id,),
            ).fetchone()[0]
            == 0
        )


def test_profile_eligibility_uses_only_each_papers_latest_extraction(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, old_claim_id = _review_case(database)
    new_claim_id = str(uuid.uuid4())
    new_result_id = str(uuid.uuid4())
    _admit(service, paper_id)
    service.review_claim(old_claim_id, reviewer="reviewer-1", review=_approved_review())
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) SELECT 'new-extraction', paper_id, model, 'new-extract-run', extraction_json,
                second_model, 'new-second-run', second_extraction_json,
                check_model, 'new-check-run', consistency_status, consistency_json,
                '2026-08-12T00:00:00Z'
            FROM paper_extractions WHERE paper_id = ?
            """,
            (paper_id,),
        )
        connection.execute(
            """
            INSERT INTO results(
                id, study_id, paper_id, extraction_id, population,
                baseline_nutrient_status, ingredient_name, ingredient_form,
                dose, comparator, outcome, timepoint, effect_estimate,
                statistical_details, evidence_text, locator, created_at
            ) SELECT ?, study_id, paper_id, 'new-extraction', population,
                baseline_nutrient_status, ingredient_name, ingredient_form,
                dose, comparator, outcome, timepoint, effect_estimate,
                statistical_details, evidence_text, locator, '2026-08-12T00:00:00Z'
            FROM results WHERE id = (SELECT result_id FROM claims WHERE id = ?)
            """,
            (new_result_id, old_claim_id),
        )
        connection.execute(
            """
                INSERT INTO claims(
                    id, paper_id, extraction_id, result_id, candidate_text,
                    evidence_text, locator, candidate_claim_type, candidate_study_design, created_at
                ) SELECT ?, paper_id, 'new-extraction', ?, candidate_text,
                    evidence_text, locator, candidate_claim_type, candidate_study_design,
                    '2026-08-12T00:00:00Z'
            FROM claims WHERE id = ?
            """,
            (new_claim_id, new_result_id, old_claim_id),
        )
    service.review_claim(new_claim_id, reviewer="reviewer-1", review=_approved_review())

    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[new_claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(new_claim_id),
    )

    assert card_id


def test_ai_review_is_idempotent_and_resumes_an_existing_draft(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with database.connect() as connection:
        topic_id = connection.execute(
            "SELECT id FROM evidence_topics ORDER BY created_at LIMIT 1"
        ).fetchone()[0]
    card_id = service.create_card_draft(
        topic_id=topic_id,
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id, certainty="very_low"),
    )

    first = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")
    second = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert first["cards"][0]["card_id"] == card_id
    assert first["cards"][0]["status"] == "approved"
    assert second["cards"][0]["card_id"] == card_id
    assert second["cards"][0]["status"] == "approved"
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_cards").fetchone()[0] == 1


def test_ai_profile_records_a_null_result_as_not_supporting(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE claims SET candidate_text = ? WHERE id = ?",
            ("Vitamin D was not significantly associated with frailty.", claim_id),
        )
        connection.execute(
            "UPDATE results SET effect_estimate = ? WHERE paper_id = ?",
            ("No significant association", paper_id),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    with database.connect() as connection:
        profile_result = connection.execute(
            "SELECT interpretation FROM evidence_profile_results"
        ).fetchone()[0]
        body = connection.execute("SELECT patient_visible_body FROM knowledge_cards").fetchone()[0]
    assert profile_result == "does_not_support"
    assert "未支持上述研究关系" in body


def test_ai_blocks_unresolved_material_difference_without_polluting_risk(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, consistency="needs_review")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE paper_extractions SET consistency_json = ? WHERE paper_id = ?",
            (
                json.dumps(
                    {
                        "verdict": "needs_review",
                        "issues": [
                            {
                                "field": "claims[0].effect_estimate",
                                "severity": "high",
                                "message": "The two estimates conflict.",
                                "evidence": "Results table 2",
                            }
                        ],
                    }
                ),
                paper_id,
            ),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "attention_required"
    assert result["stage"] == "consistency_adjudication"
    _admit(
        service,
        paper_id,
        consistency_resolution=(
            "AI consistency adjudication (literature-review-ai/1.3): "
            "the primary extraction remains the structured source of record."
        ),
    )
    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")
    assert result["status"] == "attention_required"
    assert result["stage"] == "consistency_adjudication"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0] == "pending"
    _admit(
        service,
        paper_id,
        consistency_resolution="The primary estimate was verified against Results table 2.",
    )
    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")
    assert result["status"] == "completed"
    assert result["decision"] == "internally_admitted"
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, consistency_resolution FROM paper_admissions WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        risk = json.loads(
            connection.execute("SELECT risk_of_bias_json FROM claim_reviews").fetchone()[0]
        )
        card = connection.execute("SELECT status, grade FROM knowledge_cards").fetchone()
    assert admission["status"] == "internally_admitted"
    assert "primary estimate was verified" in admission["consistency_resolution"].casefold()
    assert risk["overall"] == "some_concerns"
    assert tuple(card) == ("approved", "very_low")


def test_unresolved_difference_removes_paper_from_active_profiles(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, consistency="consistent")
    first = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")
    assert first["cards"][0]["status"] == "approved"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE paper_extractions SET consistency_status = 'needs_review', "
            "consistency_json = ? WHERE paper_id = ?",
            (
                json.dumps(
                    {
                        "verdict": "needs_review",
                        "issues": [
                            {
                                "field": "claims[0].effect_estimate",
                                "severity": "high",
                                "message": "The two estimates conflict.",
                                "evidence": "Results table 2",
                            }
                        ],
                    }
                ),
                paper_id,
            ),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["stage"] == "consistency_adjudication"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0] == "pending"
        assert connection.execute("SELECT status FROM knowledge_cards").fetchone()[0] == "stale"


def test_low_claim_difference_does_not_downgrade_risk(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, consistency="needs_review")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE paper_extractions SET consistency_json = ? WHERE paper_id = ?",
            (
                json.dumps(
                    {
                        "verdict": "needs_review",
                        "issues": [
                            {
                                "field": "claims",
                                "severity": "low",
                                "message": "A minor claim wording difference remains.",
                                "evidence": "Claim 1 wording only.",
                            }
                        ],
                    }
                ),
                paper_id,
            ),
        )

    service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")
    with database.connect() as connection:
        risk = json.loads(
            connection.execute("SELECT risk_of_bias_json FROM claim_reviews").fetchone()[0]
        )
    assert risk["overall"] == "some_concerns"


def test_ai_preserves_reported_high_risk_of_bias(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, screened=False)
    with database.transaction() as connection:
        extraction = json.loads(
            connection.execute(
                "SELECT extraction_json FROM paper_extractions WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
        )
        extraction["study_design"] = "systematic_review_meta_analysis"
        extraction["limitations"] = ["High risk of bias and very-low certainty of evidence"]
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ?, second_extraction_json = ? "
            "WHERE paper_id = ?",
            (json.dumps(extraction), json.dumps(extraction), paper_id),
        )
        connection.execute(
            "UPDATE studies SET study_design = 'systematic_review_meta_analysis' "
            "WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)",
            (paper_id,),
        )
        connection.execute(
            "UPDATE claims SET candidate_study_design = 'systematic_review_meta_analysis' "
            "WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE results SET statistical_details = '95% CI 0.8 to 1.2', "
            "baseline_nutrient_status = 'Not reported' WHERE paper_id = ?",
            (paper_id,),
        )
    _complete_topic(
        database,
        paper_id,
        eligible_study_designs=("systematic_review_meta_analysis",),
    )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    with database.connect() as connection:
        risk = json.loads(
            connection.execute("SELECT risk_of_bias_json FROM claim_reviews").fetchone()[0]
        )
        card = connection.execute("SELECT status, grade FROM knowledge_cards").fetchone()
    assert risk["overall"] == "high"
    assert tuple(card) == ("approved", "low")
    assert result["cards"][0]["status"] == "approved_evidence_limited"
    assert "risk-of-bias" in result["cards"][0]["reason"]


def test_ai_caps_a_single_study_profile_at_low_certainty(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, screened=False)
    with database.transaction() as connection:
        extraction = json.loads(
            connection.execute(
                "SELECT extraction_json FROM paper_extractions WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
        )
        extraction["study_design"] = "randomized_controlled_trial"
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ?, second_extraction_json = ? "
            "WHERE paper_id = ?",
            (json.dumps(extraction), json.dumps(extraction), paper_id),
        )
        connection.execute(
            "UPDATE studies SET study_design = 'randomized_controlled_trial' "
            "WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)",
            (paper_id,),
        )
        connection.execute(
            "UPDATE claims SET candidate_study_design = 'randomized_controlled_trial' "
            "WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE results SET dose = '1000 IU/day', "
            "statistical_details = '95% CI 0.8 to 1.2' WHERE paper_id = ?",
            (paper_id,),
        )
    _complete_topic(
        database,
        paper_id,
        eligible_study_designs=("randomized_controlled_trial",),
    )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["cards"][0]["certainty"] == "low"
    with database.connect() as connection:
        profile = connection.execute(
            "SELECT certainty, certainty_rationale FROM evidence_profiles"
        ).fetchone()
    assert profile["certainty"] == "low"
    assert "single-study evidence is capped at low certainty" in profile["certainty_rationale"]


def test_ai_can_publish_a_moderate_systematic_review_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database, screened=False)
    with database.transaction() as connection:
        extraction = json.loads(
            connection.execute(
                "SELECT extraction_json FROM paper_extractions WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
        )
        extraction["study_design"] = "systematic_review_meta_analysis"
        connection.execute(
            "UPDATE paper_extractions SET extraction_json = ?, second_extraction_json = ? "
            "WHERE paper_id = ?",
            (json.dumps(extraction), json.dumps(extraction), paper_id),
        )
        connection.execute(
            "UPDATE studies SET study_design = 'systematic_review_meta_analysis' "
            "WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)",
            (paper_id,),
        )
        connection.execute(
            "UPDATE claims SET candidate_study_design = 'systematic_review_meta_analysis' "
            "WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute(
            "UPDATE results SET statistical_details = '95% CI 0.8 to 1.2', "
            "baseline_nutrient_status = 'Not reported' WHERE paper_id = ?",
            (paper_id,),
        )
    topic_id = _complete_topic(
        database,
        paper_id,
        eligible_study_designs=("systematic_review_meta_analysis",),
    )
    _admit(service, paper_id, study_design="systematic_review_meta_analysis")
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(
            decision="approved",
            corrected_text="Lower vitamin D status was associated with frailty.",
            corrected_study_design="systematic_review_meta_analysis",
            inference="associational",
            risk_of_bias=RiskOfBiasInput(
                tool="robis", overall="low", rationale="The review methods were adequate."
            ),
            applicability="Applies to older adults with measured serum 25(OH)D.",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            source_verified=True,
        ),
    )
    legacy_card = service.create_card_draft(
        topic_id=topic_id,
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="Legacy low-certainty synthesis.",
        profile=_profile(claim_id, certainty="low"),
    )
    service.transition_card(legacy_card, reviewer="reviewer-1", target="in_review")
    service.transition_card(legacy_card, reviewer="reviewer-1", target="approved")

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["cards"][0]["status"] == "published"
    assert result["cards"][0]["certainty"] == "moderate"
    assert result["cards"][0]["version"] == "1.0.1"
    with database.connect() as connection:
        cards = connection.execute(
            "SELECT version, status, grade FROM knowledge_cards ORDER BY version"
        ).fetchall()
    assert [tuple(card) for card in cards] == [
        ("1.0.0", "stale", "low"),
        ("1.0.1", "published", "moderate"),
    ]


def test_ai_publishes_a_moderate_profile_from_two_randomized_studies(tmp_path) -> None:
    database, service = _service(tmp_path)
    first_paper, _ = _review_case(database, screened=False)
    second_paper, _ = _review_case(database, screened=False)
    with database.transaction() as connection:
        for paper_id in (first_paper, second_paper):
            extraction = json.loads(
                connection.execute(
                    "SELECT extraction_json FROM paper_extractions WHERE paper_id = ?",
                    (paper_id,),
                ).fetchone()[0]
            )
            extraction["study_design"] = "randomized_controlled_trial"
            connection.execute(
                "UPDATE paper_extractions SET extraction_json = ?, second_extraction_json = ? "
                "WHERE paper_id = ?",
                (json.dumps(extraction), json.dumps(extraction), paper_id),
            )
            connection.execute(
                "UPDATE studies SET study_design = 'randomized_controlled_trial' "
                "WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)",
                (paper_id,),
            )
            connection.execute(
                "UPDATE claims SET candidate_study_design = 'randomized_controlled_trial' "
                "WHERE paper_id = ?",
                (paper_id,),
            )
            connection.execute(
                "UPDATE results SET statistical_details = '95% CI 0.8 to 1.2' WHERE paper_id = ?",
                (paper_id,),
            )
    _complete_topic(
        database,
        first_paper,
        second_paper,
        eligible_study_designs=("randomized_controlled_trial",),
    )

    first = service.auto_review_paper(first_paper, requested_by="authenticated-reviewer")
    second = service.auto_review_paper(second_paper, requested_by="authenticated-reviewer")

    assert first["cards"][0]["status"] == "waiting_for_complete_evidence_body"
    assert second["cards"][0]["status"] == "published"
    assert second["cards"][0]["certainty"] == "moderate"
    with database.connect() as connection:
        card = connection.execute("SELECT status, grade FROM knowledge_cards").fetchone()
        claim_count = connection.execute("SELECT count(*) FROM card_claims").fetchone()[0]
    assert tuple(card) == ("published", "moderate")
    assert claim_count == 2


def test_ai_requeues_failed_extraction_without_human_intervention(tmp_path) -> None:
    database, service = _service(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, created_at) VALUES ('paper-1', 'Paper', 'now')"
        )
        connection.execute(
            """
            INSERT INTO full_texts(
                paper_id, object_key, sha256, media_type, rights_status, processed_at
            ) VALUES (
                'paper-1', 'paper.xml', ?, 'application/xml', 'internal_tdm_only',
                '2026-08-11T00:00:00Z'
            )
            """,
            ("0" * 64,),
        )
        connection.execute(
            """
            INSERT INTO paper_extraction_jobs(
                id, paper_id, status, stage, error_class, error_message,
                created_at, updated_at
            ) VALUES ('job-1', 'paper-1', 'failed', 'extraction_b',
                'PaperAnalysisError', 'invalid extraction', 'now', 'now')
            """
        )

    result = service.auto_review_paper("paper-1", requested_by="authenticated-reviewer")

    assert result["status"] == "queued"
    with database.connect() as connection:
        job = connection.execute(
            "SELECT status, error_class, error_message FROM paper_extraction_jobs"
        ).fetchone()
        event = connection.execute(
            "SELECT action, actor FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert tuple(job) == ("queued", None, None)
    assert tuple(event) == ("autonomous_extraction_queued", "ai:extraction-worker")


def test_observational_claim_cannot_be_approved_as_causal() -> None:
    with pytest.raises(ValidationError, match="cannot be approved as causal"):
        ClaimReviewInput(
            decision="approved",
            corrected_text="Vitamin D caused lower frailty.",
            corrected_study_design="cohort_study",
            inference="causal",
            risk_of_bias=RiskOfBiasInput(
                tool="exposure_study", overall="some_concerns", rationale="Confounding."
            ),
            applicability="Older adults only.",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            source_verified=True,
        )


def test_approved_claim_requires_executing_actor_source_verification() -> None:
    values = _approved_review().model_dump()
    values["source_verified"] = False
    with pytest.raises(ValidationError, match="executing-actor source verification"):
        ClaimReviewInput.model_validate(values)


def test_claim_condition_must_match_paper_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    with pytest.raises(ValueError, match="not selected"):
        service.review_claim(
            claim_id,
            reviewer="reviewer-1",
            review=ClaimReviewInput(
                decision="approved",
                corrected_text="Lower vitamin D status was associated with frailty.",
                corrected_study_design="cohort_study",
                inference="associational",
                risk_of_bias=RiskOfBiasInput(
                    tool="exposure_study", overall="some_concerns", rationale="Confounding."
                ),
                applicability="Older adults only.",
                condition_code="COND_SARCOPENIA_FRAILTY",
                source_verified=True,
            ),
        )


def test_one_reviewer_can_publish_a_traceable_card(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=_approved_review(),
    )
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联，结果需要结合个人检查理解。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
        assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
    service.transition_card(card_id, reviewer="reviewer-1", target="published")

    cards = ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY")
    assert [card["id"] for card in cards] == [card_id]
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, condition_codes_json FROM paper_admissions"
        ).fetchone()
        assert admission["status"] == "internally_admitted"
        assert json.loads(admission["condition_codes_json"]) == ["COND_VITAMIN_D_DEFICIENCY"]
        assert connection.execute("SELECT grade FROM knowledge_cards").fetchone()[0] == "moderate"


def test_profile_links_a_shared_result_only_once(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    duplicate_claim_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """
                INSERT INTO claims(
                    id, paper_id, extraction_id, result_id, candidate_text,
                    evidence_text, locator, candidate_claim_type, candidate_study_design, created_at
                ) SELECT ?, paper_id, extraction_id, result_id, candidate_text || ' Confirmed.',
                    evidence_text, locator, candidate_claim_type, candidate_study_design, created_at
            FROM claims WHERE id = ?
            """,
            (duplicate_claim_id, claim_id),
        )
    _admit(service, paper_id)
    for selected_claim_id in (claim_id, duplicate_claim_id):
        service.review_claim(selected_claim_id, reviewer="reviewer-1", review=_approved_review())
    profile = _profile(claim_id)
    profile.interpretations[duplicate_claim_id] = "supports"

    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id, duplicate_claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=profile,
    )

    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM evidence_profile_results ep "
                "JOIN knowledge_cards kc ON kc.evidence_profile_id = ep.profile_id "
                "WHERE kc.id = ?",
                (card_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM card_claims WHERE card_id = ?", (card_id,)
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("claim_type", ["association", "mechanism", "other"])
def test_patient_profile_excludes_non_effect_claims_but_keeps_audit_record(
    tmp_path, claim_type
) -> None:
    database, service = _service(tmp_path)
    paper_id, intervention_claim_id = _review_case(database)
    audit_claim_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, result_id, candidate_text,
                evidence_text, locator, candidate_claim_type, candidate_study_design, created_at
            ) SELECT ?, paper_id, extraction_id, result_id, candidate_text || ' Audit only.',
                evidence_text, locator, ?, candidate_study_design, created_at
            FROM claims WHERE id = ?
            """,
            (audit_claim_id, claim_type, intervention_claim_id),
        )

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    assert result["cards"][0]["status"] == "approved"
    with database.connect() as connection:
        reviews = dict(
            connection.execute(
                "SELECT claim_id, decision FROM claim_reviews WHERE claim_id IN (?, ?)",
                (intervention_claim_id, audit_claim_id),
            ).fetchall()
        )
        card_claims = connection.execute("SELECT claim_id FROM card_claims").fetchall()
    assert reviews == {intervention_claim_id: "approved", audit_claim_id: "approved"}
    assert [row[0] for row in card_claims] == [intervention_claim_id]


def test_patient_card_requires_doi_and_processed_full_text(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    with database.transaction() as connection:
        connection.execute("UPDATE papers SET doi = NULL WHERE id = ?", (paper_id,))
    with pytest.raises(ValueError, match="ineligible evidence"):
        service.transition_card(card_id, reviewer="reviewer-1", target="published")


def test_non_published_and_stale_cards_are_invisible_to_patient_queries(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=_approved_review(),
    )
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
    for target in ("in_review", "approved", "published"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    PaperStore(database).update_integrity(
        paper_id,
        "corrected",
        detail={"source": "test"},
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []


def test_ai_differences_require_documented_actor_resolution_before_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, consistency="needs_review")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE paper_extractions SET consistency_json = ? WHERE paper_id = ?",
            (
                json.dumps(
                    {
                        "verdict": "needs_review",
                        "issues": [
                            {
                                "field": "claims[0].effect_estimate",
                                "severity": "high",
                                "message": "The effect estimate differs.",
                                "evidence": "Results table 2",
                            }
                        ],
                    }
                ),
                paper_id,
            ),
        )
    with pytest.raises(ValueError, match="executing-actor verification"):
        _admit(service, paper_id)
    with pytest.raises(ValueError, match="executing-actor verification"):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
            consistency_resolution="The locator was corrected against the full text.",
            differences_confirmed=False,
            study_design="cohort_study",
            publication_role="primary",
            identity_confirmed=True,
        )
    _admit(
        service,
        paper_id,
        consistency_resolution="The locator was corrected against the full text.",
    )
    item = ReviewStore(database).get_review_item(paper_id)
    assert item is not None
    assert item["review_guidance"]["state"] == "ready_for_automation"

    result = service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    assert result["status"] == "completed"
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT consistency_resolution FROM paper_admissions WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()[0]
            == "The locator was corrected against the full text."
        )


def test_low_certainty_context_card_can_be_patient_visible(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="研究结论仍然不确定。",
        profile=_profile(claim_id, certainty="low"),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    service.transition_card(card_id, reviewer="reviewer-1", target="published")
    published = ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY")
    assert published[0]["grade"] == "low"


def test_very_low_certainty_card_remains_internal(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="证据极不确定，仅供内部审核使用。",
        profile=_profile(claim_id, certainty="very_low"),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    with pytest.raises(ValueError, match="low, moderate, or high"):
        service.transition_card(card_id, reviewer="reviewer-1", target="published")


def test_moderate_profile_rejects_high_risk_claims(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    values = _approved_review().model_dump()
    values["risk_of_bias"]["overall"] = "high"
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput.model_validate(values),
    )
    with pytest.raises(ValueError, match="resolved non-high risk-of-bias"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="研究证据仍需谨慎解释。",
            profile=_profile(claim_id),
        )


def test_claim_without_structured_result_cannot_be_admitted(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute("DELETE FROM claims WHERE paper_id = ?", (paper_id,))
        connection.execute("DELETE FROM results WHERE paper_id = ?", (paper_id,))
    with pytest.raises(ValueError, match="structured Result"):
        _admit(service, paper_id)


def test_preprint_cannot_support_patient_visible_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET publication_status = 'preprint' WHERE id = ?", (paper_id,)
        )
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="formal publications"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="研究结论仍需正式发表后确认。",
            profile=_profile(claim_id),
        )


def test_unknown_publication_status_cannot_support_patient_visible_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database, publication_status="unknown")
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="formal publications"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="论文发表状态仍需核验。",
            profile=_profile(claim_id),
        )


def test_unknown_publication_does_not_block_complete_formal_evidence_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    formal_paper, formal_claim = _review_case(database)
    unknown_paper, _ = _review_case(database, publication_status="unknown")
    for paper_id in (formal_paper, unknown_paper):
        _admit(service, paper_id)
    service.review_claim(formal_claim, reviewer="reviewer-1", review=_approved_review())
    service.create_card_draft(
        topic_id=_complete_topic(database, formal_paper),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[formal_claim],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(formal_claim),
    )


def test_publish_rechecks_publication_status_after_card_draft(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET publication_status = 'unknown' WHERE id = ?", (paper_id,)
        )
    with pytest.raises(ValueError, match="ineligible evidence"):
        service.transition_card(card_id, reviewer="reviewer-1", target="published")


@pytest.mark.parametrize("study_design", ["case_report", "case_series"])
def test_case_reports_cannot_support_patient_visible_profile(tmp_path, study_design) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET study_design_candidate = ? WHERE id = ?",
            (study_design, paper_id),
        )
        connection.execute(
            "UPDATE claims SET candidate_study_design = ?, candidate_claim_type = 'safety' "
            "WHERE id = ?",
            (study_design, claim_id),
        )
    _admit(service, paper_id, study_design=study_design)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(
            decision="approved",
            corrected_text="This report records a possible safety signal.",
            corrected_study_design=study_design,
            inference="descriptive",
            risk_of_bias=RiskOfBiasInput(
                tool="safety_signal",
                overall="high",
                rationale="A case report cannot estimate incidence or establish causality.",
            ),
            applicability="Safety signal only.",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            source_verified=True,
        ),
    )
    with pytest.raises(ValueError, match="case-report"):
        service.create_card_draft(
            topic_id=_complete_topic(
                database,
                paper_id,
                eligible_study_designs=(study_design,),
            ),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="研究结果仅作为安全信号记录。",
            profile=_profile(claim_id),
        )


def test_unreviewed_eligible_result_blocks_evidence_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    first_paper, first_claim = _review_case(database)
    second_paper, _ = _review_case(database)
    for paper_id in (first_paper, second_paper):
        _admit(service, paper_id)
    service.review_claim(first_claim, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="full-text included paper"):
        service.create_card_draft(
            topic_id=_complete_topic(database, first_paper, second_paper),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[first_claim],
            reviewer="reviewer-1",
            patient_body="维生素 D 状态与衰弱之间存在研究关联。",
            profile=_profile(first_claim),
        )


def test_patient_card_body_rejects_diagnostic_wording(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=_approved_review(),
    )
    with pytest.raises(ValueError, match="forbidden term"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="这是疾病诊断结果。",
            profile=_profile(claim_id),
        )


def test_rejecting_reviewed_evidence_stales_a_published_card(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    _admit(service, paper_id)
    approved = _approved_review()
    service.review_claim(claim_id, reviewer="reviewer-1", review=approved)
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved", "published"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(decision="rejected"),
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
