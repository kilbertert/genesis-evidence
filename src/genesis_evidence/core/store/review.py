"""Atomic persistence operations for evidence review and publication."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime

from ..conditions import CONDITION_BY_CODE
from ..metrics import METRIC_LABELS
from ..patient_copy import validate_patient_copy
from .database import Database


class ReviewStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def admit_paper(
        self,
        paper_id: str,
        *,
        reviewer: str,
        condition_codes: tuple[str, ...],
        consistency_resolution: str | None,
        differences_confirmed: bool,
        study_design: str,
        publication_role: str,
        identity_confirmed: bool,
    ) -> None:
        with self.database.transaction() as connection:
            if not identity_confirmed:
                raise ValueError(
                    "Study/Publication identity must be verified by the executing actor"
                )
            paper = connection.execute(
                """
                SELECT integrity_status,
                    EXISTS(SELECT 1 FROM paper_sources WHERE paper_id = ?) AS source_available
                FROM papers WHERE id = ?
                """,
                (paper_id, paper_id),
            ).fetchone()
            if paper is None:
                raise ValueError("paper not found")
            if paper["integrity_status"] != "clear":
                raise ValueError("paper integrity must be clear before internal admission")
            if not paper["source_available"]:
                raise ValueError("paper source identity must be present before internal admission")
            extraction = connection.execute(
                """
                SELECT id, consistency_status FROM paper_extractions
                WHERE paper_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            if extraction is None:
                raise ValueError("paper has no AI extraction")
            if extraction["consistency_status"] == "needs_review":
                if not differences_confirmed:
                    raise ValueError(
                        "AI extraction differences require executing-actor verification"
                    )
                if not consistency_resolution:
                    raise ValueError("AI extraction differences require a documented resolution")
            studies = connection.execute(
                """
                SELECT s.id, s.study_design, sp.role
                FROM studies s JOIN study_publications sp ON sp.study_id = s.id
                WHERE sp.paper_id = ?
                """,
                (paper_id,),
            ).fetchall()
            if len(studies) != 1:
                raise ValueError("paper requires one resolved Study/Publication relationship")
            result_count = connection.execute(
                "SELECT count(*) FROM results WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            if result_count == 0:
                raise ValueError("paper has no structured Result records")
            included_conditions = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT et.condition_code
                    FROM collection_papers cp
                    JOIN collection_runs cr ON cr.id = cp.run_id
                    JOIN evidence_topics et ON et.id = cr.topic_id
                    WHERE cp.paper_id = ? AND cp.title_abstract_decision = 'included'
                        AND cp.full_text_decision = 'included'
                        AND cr.status = 'completed' AND et.status = 'locked'
                    """,
                    (paper_id,),
                ).fetchall()
            }
            if not included_conditions:
                raise ValueError(
                    "paper must pass title/abstract and full-text screening before admission"
                )
            known = {
                row[0]
                for row in connection.execute(
                    f"SELECT code FROM conditions WHERE code IN ({_placeholders(condition_codes)})",
                    condition_codes,
                ).fetchall()
            }
            if known != set(condition_codes):
                raise ValueError("paper contains an unknown condition code")
            if not set(condition_codes) <= included_conditions:
                raise ValueError("paper conditions must match its full-text included topics")
            previous = connection.execute(
                "SELECT * FROM paper_admissions WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            evidence_changed = (
                previous
                and previous["status"] == "internally_admitted"
                and (
                    set(json.loads(previous["condition_codes_json"])) != set(condition_codes)
                    or (previous["consistency_resolution"] or None) != consistency_resolution
                    or any(
                        row["study_design"] != study_design or row["role"] != publication_role
                        for row in studies
                    )
                )
            )
            stale_cards = (
                self._stale_cards_for_paper(connection, paper_id) if evidence_changed else 0
            )
            connection.execute(
                """
                INSERT INTO paper_admissions(
                    paper_id, status, condition_codes_json, reviewer, reviewed_at,
                    consistency_resolution
                ) VALUES (?, 'internally_admitted', ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    status = excluded.status,
                    condition_codes_json = excluded.condition_codes_json,
                    reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at,
                    consistency_resolution = excluded.consistency_resolution
                """,
                (
                    paper_id,
                    json.dumps(condition_codes),
                    reviewer,
                    _now(),
                    consistency_resolution,
                ),
            )
            reviewed_at = _now()
            connection.execute(
                """
                UPDATE studies SET status = 'verified', study_design = ?, reviewer = ?,
                    reviewed_at = ?
                WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)
                """,
                (study_design, reviewer, reviewed_at, paper_id),
            )
            connection.execute(
                """
                UPDATE study_publications SET role = ?, reviewer = ?, reviewed_at = ?
                WHERE paper_id = ?
                """,
                (publication_role, reviewer, reviewed_at, paper_id),
            )
            self._audit(
                connection,
                "paper",
                paper_id,
                "internally_admitted",
                reviewer,
                {
                    "condition_codes": condition_codes,
                    "consistency_resolution": consistency_resolution,
                    "differences_confirmed": differences_confirmed,
                    "study_design": study_design,
                    "publication_role": publication_role,
                    "identity_confirmed": True,
                    "from_status": previous["status"] if previous else None,
                    "to_status": "internally_admitted",
                    "stale_cards": stale_cards,
                },
            )

    def reject_paper(self, paper_id: str, *, reviewer: str) -> None:
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT status FROM paper_admissions WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if previous is None:
                connection.execute(
                    """
                    INSERT INTO paper_admissions(paper_id, status, condition_codes_json)
                    VALUES (?, 'pending', '[]')
                    """,
                    (paper_id,),
                )
                previous = connection.execute(
                    "SELECT status FROM paper_admissions WHERE paper_id = ?", (paper_id,)
                ).fetchone()
            updated = connection.execute(
                """
                UPDATE paper_admissions SET status = 'rejected', condition_codes_json = '[]',
                    reviewer = ?, reviewed_at = ? WHERE paper_id = ?
                """,
                (reviewer, _now(), paper_id),
            ).rowcount
            if updated != 1:
                raise ValueError("paper admission item not found")
            rejected_at = _now()
            candidate_claims = connection.execute(
                "SELECT id FROM claims WHERE paper_id = ? AND status = 'candidate'",
                (paper_id,),
            ).fetchall()
            if candidate_claims:
                connection.executemany(
                    """
                    INSERT INTO claim_reviews(claim_id, decision, reviewer, reviewed_at)
                    VALUES (?, 'rejected', ?, ?)
                    ON CONFLICT(claim_id) DO UPDATE SET
                        decision = 'rejected', corrected_text = NULL,
                        corrected_study_design = NULL, inference = NULL,
                        risk_of_bias_json = NULL, applicability = NULL,
                        condition_code = NULL, reviewer = excluded.reviewer,
                        reviewed_at = excluded.reviewed_at
                    """,
                    [(row["id"], reviewer, rejected_at) for row in candidate_claims],
                )
                connection.execute(
                    "UPDATE claims SET status = 'rejected' "
                    "WHERE paper_id = ? AND status = 'candidate'",
                    (paper_id,),
                )
                connection.execute(
                    "UPDATE results SET status = 'rejected' "
                    "WHERE paper_id = ? AND status = 'candidate'",
                    (paper_id,),
                )
                for claim in candidate_claims:
                    self._audit(
                        connection,
                        "claim",
                        claim["id"],
                        "claim_rejected_by_paper",
                        reviewer,
                        {
                            "paper_id": paper_id,
                            "from_status": "candidate",
                            "to_status": "rejected",
                        },
                    )
            stale_cards = self._stale_cards_for_paper(connection, paper_id)
            self._audit(
                connection,
                "paper",
                paper_id,
                "admission_rejected",
                reviewer,
                {
                    "from_status": previous["status"],
                    "to_status": "rejected",
                    "rejected_candidate_claim_ids": [row["id"] for row in candidate_claims],
                    "stale_cards": stale_cards,
                },
            )

    def require_consistency_adjudication(self, paper_id: str, *, reviewer: str) -> None:
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT status FROM paper_admissions WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if previous is None:
                connection.execute(
                    """
                    INSERT INTO paper_admissions(
                        paper_id, status, condition_codes_json, reviewer, reviewed_at
                    ) VALUES (?, 'pending', '[]', ?, ?)
                    """,
                    (paper_id, reviewer, _now()),
                )
            else:
                connection.execute(
                    """
                    UPDATE paper_admissions SET status = 'pending', consistency_resolution = NULL,
                        reviewer = ?, reviewed_at = ? WHERE paper_id = ?
                    """,
                    (reviewer, _now(), paper_id),
                )
            stale_cards = self._stale_cards_for_paper(connection, paper_id)
            if previous is None or previous["status"] != "pending" or stale_cards:
                self._audit(
                    connection,
                    "paper",
                    paper_id,
                    "consistency_adjudication_required",
                    reviewer,
                    {
                        "from_status": previous["status"] if previous else None,
                        "to_status": "pending",
                        "stale_cards": stale_cards,
                    },
                )

    def review_claim(
        self,
        claim_id: str,
        *,
        reviewer: str,
        decision: str,
        corrected_text: str | None,
        corrected_study_design: str | None,
        inference: str | None,
        risk_of_bias: object | None,
        applicability: str | None,
        condition_code: str | None,
        source_verified: bool,
    ) -> None:
        with self.database.transaction() as connection:
            claim = connection.execute(
                """
                SELECT c.paper_id, c.result_id, c.status AS claim_status,
                    prior.decision AS prior_decision, p.integrity_status,
                    pa.status AS admission_status,
                    pa.condition_codes_json
                FROM claims c JOIN papers p ON p.id = c.paper_id
                LEFT JOIN paper_admissions pa ON pa.paper_id = c.paper_id
                LEFT JOIN claim_reviews prior ON prior.claim_id = c.id
                WHERE c.id = ?
                """,
                (claim_id,),
            ).fetchone()
            if claim is None:
                raise ValueError("claim not found")
            if claim["admission_status"] != "internally_admitted":
                raise ValueError("paper must be internally admitted before claim review")
            if decision == "approved" and claim["integrity_status"] != "clear":
                raise ValueError("claim cannot be approved while paper integrity is not clear")
            if decision == "approved" and not claim["result_id"]:
                raise ValueError("claim requires a structured Result before approval")
            if decision == "approved" and not source_verified:
                raise ValueError("claim source evidence must be verified by the executing actor")
            if decision == "approved" and condition_code not in json.loads(
                claim["condition_codes_json"]
            ):
                raise ValueError("claim condition was not selected during paper admission")
            connection.execute(
                """
                INSERT INTO claim_reviews(
                    claim_id, decision, corrected_text, corrected_study_design,
                    inference, risk_of_bias_json, applicability,
                    condition_code, reviewer, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    decision = excluded.decision, corrected_text = excluded.corrected_text,
                    corrected_study_design = excluded.corrected_study_design,
                    inference = excluded.inference,
                    risk_of_bias_json = excluded.risk_of_bias_json,
                    applicability = excluded.applicability,
                    condition_code = excluded.condition_code, reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at
                """,
                (
                    claim_id,
                    decision,
                    corrected_text,
                    corrected_study_design,
                    inference,
                    json.dumps(risk_of_bias, ensure_ascii=False) if risk_of_bias else None,
                    applicability,
                    condition_code,
                    reviewer,
                    _now(),
                ),
            )
            connection.execute(
                "UPDATE claims SET status = ? WHERE id = ?",
                ("reviewed" if decision == "approved" else "rejected", claim_id),
            )
            connection.execute(
                """
                UPDATE results SET status = ? WHERE id = (
                    SELECT result_id FROM claims WHERE id = ?
                )
                """,
                ("reviewed" if decision == "approved" else "rejected", claim_id),
            )
            stale_cards = connection.execute(
                """
                UPDATE knowledge_cards SET status = 'stale'
                WHERE status IN ('draft', 'in_review', 'approved', 'published') AND id IN (
                    SELECT card_id FROM card_claims WHERE claim_id = ?
                )
                """,
                (claim_id,),
            ).rowcount
            self._audit(
                connection,
                "claim",
                claim_id,
                f"claim_{decision}",
                reviewer,
                {
                    "corrected_text": corrected_text,
                    "corrected_study_design": corrected_study_design,
                    "inference": inference,
                    "condition_code": condition_code,
                    "risk_of_bias": risk_of_bias,
                    "applicability": applicability,
                    "source_verified": source_verified,
                    "from_status": claim["claim_status"],
                    "to_status": "reviewed" if decision == "approved" else "rejected",
                    "from_decision": claim["prior_decision"],
                    "to_decision": decision,
                    "stale_cards": stale_cards,
                },
            )

    def create_card(
        self,
        *,
        topic_id: str,
        condition_code: str,
        version: str,
        claim_ids: tuple[str, ...],
        reviewer: str,
        patient_body: str,
        profile: dict[str, object],
    ) -> str:
        card_id = str(uuid.uuid4())
        patient_body = validate_patient_copy(patient_body)
        with self.database.transaction() as connection:
            topic = _require_complete_topic(connection, topic_id, condition_code)
            picots = json.loads(topic["picots_json"])
            rows = connection.execute(
                f"""
                SELECT c.id, c.paper_id, c.result_id, c.evidence_text, c.locator,
                    c.extraction_id,
                    c.candidate_claim_type,
                    cr.decision, cr.condition_code, cr.corrected_study_design,
                    cr.risk_of_bias_json,
                    p.integrity_status, pa.status AS admission_status
                    , r.population, r.baseline_nutrient_status, r.ingredient_name,
                    r.ingredient_form, r.dose, r.comparator, r.outcome, r.timepoint
                FROM claims c JOIN claim_reviews cr ON cr.claim_id = c.id
                JOIN papers p ON p.id = c.paper_id
                JOIN paper_admissions pa ON pa.paper_id = c.paper_id
                JOIN results r ON r.id = c.result_id
                WHERE c.id IN ({_placeholders(claim_ids)})
                """,
                claim_ids,
            ).fetchall()
            if len(rows) != len(claim_ids):
                raise ValueError("one or more reviewed claims were not found")
            rows = [_augment_profile_population(connection, dict(row)) for row in rows]
            if any(
                row["decision"] != "approved"
                or row["condition_code"] != condition_code
                or row["integrity_status"] != "clear"
                or row["admission_status"] != "internally_admitted"
                for row in rows
            ):
                raise ValueError("card claims are not eligible for publication")
            included_papers = {
                row["paper_id"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT cp.paper_id FROM collection_papers cp
                    JOIN collection_runs cr ON cr.id = cp.run_id
                    WHERE cr.topic_id = ? AND cp.full_text_decision = 'included'
                    """,
                    (topic_id,),
                ).fetchall()
            }
            if not {row["paper_id"] for row in rows} <= included_papers:
                raise ValueError(
                    "card claims must come from full-text records included in the topic"
                )
            publication_status = connection.execute(
                f"""
                SELECT p.publication_status FROM papers p
                JOIN claims c ON c.paper_id = p.id
                WHERE c.id IN ({_placeholders(claim_ids)})
                """,
                claim_ids,
            ).fetchall()
            if any(row["publication_status"] != "formal" for row in publication_status):
                raise ValueError(
                    "only verified formal publications can support a patient-visible profile"
                )
            if any(
                row["corrected_study_design"]
                in {"animal_study", "in_vitro_study", "case_series", "case_report"}
                for row in rows
            ):
                raise ValueError(
                    "mechanism and case-report results cannot support a patient-visible card"
                )
            if any(row["candidate_claim_type"] != "intervention_effect" for row in rows):
                raise ValueError(
                    "only direct intervention-effect results can support a patient-visible card"
                )
            if profile["certainty"] in {"high", "moderate"} and any(
                json.loads(row["risk_of_bias_json"])["overall"] in {"high", "critical", "uncertain"}
                for row in rows
            ):
                raise ValueError(
                    "high or moderate certainty requires resolved non-high risk-of-bias judgments"
                )
            scope_key, scope_label = _resolve_profile_scope(
                picots,
                condition_code,
                [dict(row) for row in rows],
                requested=str(profile.get("scope_key") or ""),
                estimate_target=str(profile["estimate_target"]),
            )
            eligible = connection.execute(
                """
                SELECT c.id, c.status, cr.decision,
                    c.extraction_id,
                    r.population, r.baseline_nutrient_status, r.ingredient_name,
                    r.ingredient_form, r.dose, r.comparator, r.outcome, r.timepoint
                FROM claims c
                LEFT JOIN claim_reviews cr ON cr.claim_id = c.id
                JOIN results r ON r.id = c.result_id
                JOIN papers p ON p.id = c.paper_id
                JOIN paper_admissions pa ON pa.paper_id = p.id
                WHERE p.integrity_status = 'clear'
                    AND p.publication_status = 'formal'
                    AND pa.status = 'internally_admitted'
                    AND c.candidate_claim_type = 'intervention_effect'
                    AND COALESCE(cr.corrected_study_design, c.candidate_study_design) NOT IN (
                        'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                    )
                    AND EXISTS (
                        SELECT 1 FROM json_each(pa.condition_codes_json) WHERE value = ?
                    )
                    AND c.extraction_id = (
                        SELECT latest.id FROM paper_extractions latest
                        WHERE latest.paper_id = p.id
                        ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
                    )
                    AND EXISTS (
                        SELECT 1 FROM collection_papers cp
                        JOIN collection_runs cr ON cr.id = cp.run_id
                        WHERE cr.topic_id = ? AND cp.paper_id = p.id
                            AND cp.full_text_decision = 'included'
                    )
                """,
                (condition_code, topic_id),
            ).fetchall()
            scoped_eligible = []
            for row in eligible:
                item = _augment_profile_population(connection, dict(row))
                if scope_key in _profile_scopes(picots, condition_code, item):
                    scoped_eligible.append(item)
            eligible = scoped_eligible
            if any(
                row["status"] != "reviewed" or row["decision"] != "approved" for row in eligible
            ):
                raise ValueError("all eligible results must be reviewed before profile creation")
            if {row["id"] for row in eligible} != set(claim_ids):
                raise ValueError("evidence profile must include every reviewed eligible result")
            interpretations = profile["interpretations"]
            if not isinstance(interpretations, dict) or set(interpretations) != set(claim_ids):
                raise ValueError("evidence profile requires one interpretation per selected result")
            now = _now()
            profile_id = str(uuid.uuid4())
            dimensions = _synthesis_dimensions(picots, scope_label)
            predecessors = connection.execute(
                """
                SELECT kc.id, kc.status FROM knowledge_cards kc
                JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
                WHERE kc.condition_code = ? AND ep.scope_key = ?
                    AND kc.status IN ('draft', 'in_review', 'approved')
                """,
                (condition_code, scope_key),
            ).fetchall()
            if predecessors:
                connection.execute(
                    "UPDATE knowledge_cards SET status = 'stale' WHERE id IN ({})".format(
                        _placeholders(tuple(str(row["id"]) for row in predecessors))
                    ),
                    tuple(str(row["id"]) for row in predecessors),
                )
                for predecessor in predecessors:
                    self._audit(
                        connection,
                        "knowledge_card",
                        str(predecessor["id"]),
                        "card_superseded",
                        reviewer,
                        {
                            "from_status": predecessor["status"],
                            "to_status": "stale",
                            "replacement_card_id": card_id,
                            "scope_key": scope_key,
                        },
                    )
            connection.execute(
                """
                INSERT INTO evidence_profiles(
                    id, topic_id, condition_code, scope_key, version,
                    ingredient_name, ingredient_form,
                    population, baseline_nutrient_status, dose, comparator, outcome,
                    timepoint, estimate_target, certainty, certainty_rationale,
                    evidence_body_complete, evidence_cutoff_date, reviewer, reviewed_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    topic_id,
                    condition_code,
                    scope_key,
                    version,
                    dimensions["ingredient_name"],
                    dimensions["ingredient_form"],
                    dimensions["population"],
                    dimensions["baseline_nutrient_status"],
                    dimensions["dose"],
                    dimensions["comparator"],
                    dimensions["outcome"],
                    dimensions["timepoint"],
                    profile["estimate_target"],
                    profile["certainty"],
                    profile["certainty_rationale"],
                    1,
                    topic["evidence_cutoff_date"],
                    reviewer,
                    now,
                    now,
                ),
            )
            profile_results: dict[str, str] = {}
            for row in rows:
                result_id = str(row["result_id"])
                interpretation = interpretations[row["id"]]
                if result_id in profile_results and profile_results[result_id] != interpretation:
                    raise ValueError("claims for one result require one shared interpretation")
                profile_results[result_id] = interpretation
            connection.executemany(
                """
                INSERT INTO evidence_profile_results(profile_id, result_id, interpretation)
                VALUES (?, ?, ?)
                """,
                [(profile_id, result_id, value) for result_id, value in profile_results.items()],
            )
            connection.execute(
                """
                INSERT INTO knowledge_cards(
                    id, condition_code, version, status, grade, evidence_profile_id, reviewer,
                    reviewed_at, patient_visible_body, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    condition_code,
                    version,
                    profile["certainty"],
                    profile_id,
                    reviewer,
                    now,
                    patient_body,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO card_claims(card_id, claim_id, evidence_text, locator)
                VALUES (?, ?, ?, ?)
                """,
                [(card_id, row["id"], row["evidence_text"], row["locator"]) for row in rows],
            )
            self._audit(
                connection,
                "knowledge_card",
                card_id,
                "card_drafted",
                reviewer,
                {
                    "condition_code": condition_code,
                    "version": version,
                    "claims": claim_ids,
                    "evidence_profile_id": profile_id,
                    "topic_id": topic_id,
                    "profile": profile,
                    "from_status": None,
                    "to_status": "draft",
                },
            )
        return card_id

    def transition_card(self, card_id: str, *, reviewer: str, target: str) -> None:
        allowed = {
            "draft": {"in_review", "rejected"},
            "in_review": {"approved", "rejected"},
            "approved": {"published", "rejected"},
        }
        with self.database.transaction() as connection:
            card = connection.execute(
                "SELECT kc.*, ep.scope_key FROM knowledge_cards kc "
                "JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id "
                "WHERE kc.id = ?",
                (card_id,),
            ).fetchone()
            if card is None:
                raise ValueError("knowledge card not found")
            if target not in allowed.get(str(card["status"]), set()):
                raise ValueError(f"invalid card transition: {card['status']} -> {target}")
            if target == "published":
                self._require_publishable(connection, card_id)
                if card["grade"] not in {"high", "moderate", "low"}:
                    raise ValueError(
                        "patient-visible context cards require low, moderate, or high certainty"
                    )
                connection.execute(
                    """
                    UPDATE knowledge_cards SET status = 'stale'
                    WHERE condition_code = ? AND status = 'published' AND id <> ?
                        AND evidence_profile_id IN (
                            SELECT id FROM evidence_profiles WHERE scope_key = ?
                        )
                    """,
                    (card["condition_code"], card_id, card["scope_key"]),
                )
            connection.execute(
                """
                UPDATE knowledge_cards SET status = ?, reviewer = ?, reviewed_at = ?,
                    published_at = CASE WHEN ? = 'published' THEN ? ELSE published_at END
                WHERE id = ?
                """,
                (target, reviewer, _now(), target, _now(), card_id),
            )
            self._audit(
                connection,
                "knowledge_card",
                card_id,
                f"card_{target}",
                reviewer,
                {"from_status": card["status"], "to_status": target},
            )

    def list_published_cards(self, condition_code: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, condition_code, version, grade, evidence_profile_id,
                    patient_visible_body, published_at
                FROM knowledge_cards
                WHERE condition_code = ? AND status = 'published'
                ORDER BY published_at DESC, version DESC
                """,
                (condition_code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_review_queue(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.id, p.title, p.doi, p.pmid, p.pmcid, p.year,
                    p.integrity_status, p.study_design_candidate,
                    EXISTS(SELECT 1 FROM full_texts ft WHERE ft.paper_id = p.id)
                        AS full_text_available,
                    EXISTS(
                        SELECT 1 FROM collection_papers cp
                        JOIN collection_runs cr ON cr.id = cp.run_id
                        JOIN evidence_topics et ON et.id = cr.topic_id
                        WHERE cp.paper_id = p.id
                            AND cr.status = 'completed' AND et.status = 'locked'
                            AND cp.full_text_retrieval_status = 'not_retrieved'
                    ) AS full_text_not_retrieved,
                    COALESCE(pa.status, 'pending') AS admission_status,
                    pe.consistency_status,
                    pej.status AS extraction_job_status,
                    pej.stage AS extraction_job_stage,
                    CASE
                        WHEN pe.id IS NULL AND EXISTS (
                            SELECT 1 FROM collection_papers cp
                            JOIN collection_runs cr ON cr.id = cp.run_id
                            JOIN evidence_topics et ON et.id = cr.topic_id
                            WHERE cp.paper_id = p.id
                                AND cr.status = 'completed' AND et.status = 'locked'
                                AND cp.full_text_retrieval_status = 'not_retrieved'
                        ) AND NOT EXISTS (
                            SELECT 1 FROM collection_papers cp
                            JOIN collection_runs cr ON cr.id = cp.run_id
                            JOIN evidence_topics et ON et.id = cr.topic_id
                            WHERE cp.paper_id = p.id
                                AND cr.status = 'completed' AND et.status = 'locked'
                                AND (
                                    cp.title_abstract_decision IS NULL
                                    OR (cp.title_abstract_decision = 'included' AND
                                        COALESCE(cp.full_text_retrieval_status, 'pending')
                                            <> 'not_retrieved')
                                )
                        ) THEN 'completed'
                        WHEN pe.id IS NULL THEN 'blocked'
                        WHEN p.integrity_status <> 'clear' THEN 'blocked'
                        WHEN NOT EXISTS (
                            SELECT 1 FROM collection_papers cp
                            JOIN collection_runs cr ON cr.id = cp.run_id
                            JOIN evidence_topics et ON et.id = cr.topic_id
                            WHERE cp.paper_id = p.id
                                AND cr.status = 'completed' AND et.status = 'locked'
                        ) THEN 'blocked'
                        WHEN pa.status = 'rejected' THEN 'blocked'
                        WHEN pa.status = 'internally_admitted' AND NOT EXISTS (
                            SELECT 1 FROM claims pending
                            WHERE pending.paper_id = p.id AND pending.status = 'candidate'
                        ) THEN 'completed'
                        ELSE 'ready_for_automation'
                    END AS review_state,
                    count(c.id) AS claim_count,
                    sum(CASE WHEN c.status = 'candidate' THEN 1 ELSE 0 END) AS pending_claims
                FROM papers p
                LEFT JOIN paper_admissions pa ON pa.paper_id = p.id
                LEFT JOIN paper_extractions pe ON pe.id = (
                    SELECT id FROM paper_extractions
                    WHERE paper_id = p.id ORDER BY created_at DESC, id DESC LIMIT 1
                )
                LEFT JOIN paper_extraction_jobs pej ON pej.id = (
                    SELECT id FROM paper_extraction_jobs
                    WHERE paper_id = p.id ORDER BY created_at DESC, id DESC LIMIT 1
                )
                LEFT JOIN claims c ON c.paper_id = p.id
                GROUP BY p.id, pa.status, pa.consistency_resolution, pe.id,
                    pe.consistency_status, pej.status, pej.stage
                ORDER BY p.created_at DESC, p.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_review_item(self, paper_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            paper = connection.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if paper is None:
                return None
            extraction = connection.execute(
                """
                SELECT * FROM paper_extractions WHERE paper_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            extraction_job = connection.execute(
                """
                SELECT id, status, stage, attempt_count, error_class, error_message,
                    extraction_run_id, second_run_id, check_run_id, updated_at, completed_at,
                    EXISTS(SELECT 1 FROM full_texts WHERE paper_id = ?) AS full_text_available
                FROM paper_extraction_jobs WHERE paper_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id, paper_id),
            ).fetchone()
            admission = connection.execute(
                "SELECT * FROM paper_admissions WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            sources = connection.execute(
                """
                SELECT source, source_id, source_url, license
                FROM paper_sources WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchall()
            collections = connection.execute(
                """
                SELECT et.id AS topic_id, et.code AS topic_code, et.version AS topic_version,
                    et.condition_code AS topic_condition_code, et.picots_json,
                    et.eligible_study_designs_json, et.exclusion_reasons_json,
                    cr.id AS run_id, cr.source, cr.search_stream,
                    cr.status AS run_status, cp.title_abstract_decision,
                    cp.title_abstract_reviewer, cp.title_abstract_reviewed_at,
                    cp.full_text_retrieval_status, cp.full_text_retrieval_reason,
                    cp.full_text_retrieval_reviewer, cp.full_text_retrieval_recorded_at,
                    cp.full_text_decision, cp.primary_exclusion_reason,
                    cp.full_text_reviewer, cp.full_text_reviewed_at
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN evidence_topics et ON et.id = cr.topic_id
                WHERE cp.paper_id = ? ORDER BY cr.created_at, cr.id
                """,
                (paper_id,),
            ).fetchall()
            studies = connection.execute(
                """
                SELECT s.id, s.status, s.study_design, s.registration_ids_json,
                    sp.role AS publication_role, sp.reviewer, sp.reviewed_at
                FROM studies s JOIN study_publications sp ON sp.study_id = s.id
                WHERE sp.paper_id = ? ORDER BY s.created_at, s.id
                """,
                (paper_id,),
            ).fetchall()
            claims = connection.execute(
                """
                SELECT c.*, cr.decision, cr.corrected_text, cr.corrected_study_design,
                    cr.inference, cr.risk_of_bias_json, cr.applicability,
                    cr.condition_code, cr.reviewer, cr.reviewed_at,
                    r.population, r.baseline_nutrient_status, r.ingredient_name,
                    r.ingredient_form, r.dose, r.comparator, r.outcome, r.timepoint,
                    r.effect_estimate, r.statistical_details,
                    r.evidence_text AS result_evidence_text, r.locator AS result_locator,
                    r.extraction_id AS result_extraction_id
                FROM claims c LEFT JOIN claim_reviews cr ON cr.claim_id = c.id
                LEFT JOIN results r ON r.id = c.result_id
                WHERE c.paper_id = ? ORDER BY c.created_at, c.id
                """,
                (paper_id,),
            ).fetchall()
            audit_events = connection.execute(
                """
                SELECT id, entity_type, entity_id, action, actor, detail_json, created_at
                FROM audit_events
                WHERE (entity_type = 'paper' AND entity_id = ?)
                    OR (entity_type = 'claim' AND entity_id IN (
                        SELECT id FROM claims WHERE paper_id = ?
                    ))
                    OR (entity_type = 'collection_paper' AND entity_id LIKE '%:' || ?)
                ORDER BY id DESC LIMIT 100
                """,
                (paper_id, paper_id, paper_id),
            ).fetchall()
        extraction_data = json.loads(extraction["extraction_json"]) if extraction else None
        second_extraction_data = (
            json.loads(extraction["second_extraction_json"]) if extraction else None
        )
        consistency_data = json.loads(extraction["consistency_json"]) if extraction else None
        admission_data = (
            {
                **dict(admission),
                "condition_codes": json.loads(admission["condition_codes_json"]),
            }
            if admission
            else None
        )
        collection_items = [_collection_dict(row, extraction_data or {}) for row in collections]
        claim_items = [
            _claim_dict(
                row,
                extraction=extraction_data or {},
                collections=collection_items,
            )
            for row in claims
        ]
        study_items = [
            {
                **{
                    key: value for key, value in dict(row).items() if key != "registration_ids_json"
                },
                "registration_ids": json.loads(row["registration_ids_json"]),
            }
            for row in studies
        ]
        return {
            "paper": dict(paper),
            "extraction": extraction_data,
            "second_extraction": second_extraction_data,
            "consistency": consistency_data,
            "extraction_trace": (
                {
                    key: extraction[key]
                    for key in (
                        "id",
                        "model",
                        "extraction_run_id",
                        "second_model",
                        "second_run_id",
                        "check_model",
                        "check_run_id",
                    )
                }
                if extraction
                else None
            ),
            "extraction_job": dict(extraction_job) if extraction_job else None,
            "admission": admission_data,
            "sources": [dict(row) for row in sources],
            "collections": collection_items,
            "studies": study_items,
            "claims": claim_items,
            "audit_events": [
                {
                    **{key: value for key, value in dict(row).items() if key != "detail_json"},
                    "detail": json.loads(row["detail_json"]),
                }
                for row in audit_events
            ],
            "review_guidance": _review_guidance(
                paper=dict(paper),
                extraction=extraction_data,
                consistency=consistency_data,
                admission=admission_data,
                collections=collection_items,
                studies=study_items,
                claims=claim_items,
                source_count=len(sources),
            ),
        }

    def list_profile_candidates(self, paper_id: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            topics = connection.execute(
                """
                SELECT DISTINCT et.id, et.version, et.condition_code, et.evidence_cutoff_date,
                    et.picots_json, condition.name AS condition_name
                FROM evidence_topics et
                JOIN conditions condition ON condition.code = et.condition_code
                JOIN collection_runs run ON run.topic_id = et.id
                JOIN collection_papers item ON item.run_id = run.id
                WHERE item.paper_id = ? AND item.full_text_decision = 'included'
                ORDER BY et.id
                """,
                (paper_id,),
            ).fetchall()
            candidates: list[dict[str, object]] = []
            for topic in topics:
                base = {
                    **{key: value for key, value in dict(topic).items() if key != "picots_json"},
                    "picots": json.loads(topic["picots_json"]),
                }
                try:
                    _require_complete_topic(connection, topic["id"], topic["condition_code"])
                except ValueError as exc:
                    candidates.append({**base, "status": "waiting", "reason": str(exc)})
                    continue
                rows = connection.execute(
                    """
                    SELECT c.id, c.paper_id, c.candidate_text, c.candidate_claim_type,
                        c.extraction_id,
                        cr.corrected_study_design, cr.inference, cr.risk_of_bias_json,
                        r.study_id, r.population, r.baseline_nutrient_status, r.ingredient_name,
                        r.ingredient_form, r.dose, r.comparator, r.outcome, r.timepoint,
                        r.effect_estimate, r.statistical_details
                    FROM claims c
                    JOIN claim_reviews cr ON cr.claim_id = c.id
                    JOIN results r ON r.id = c.result_id
                    JOIN papers p ON p.id = c.paper_id
                    JOIN paper_admissions pa ON pa.paper_id = p.id
                    WHERE cr.decision = 'approved' AND cr.condition_code = ?
                        AND p.integrity_status = 'clear' AND p.publication_status = 'formal'
                        AND pa.status = 'internally_admitted'
                        AND c.candidate_claim_type = 'intervention_effect'
                        AND cr.corrected_study_design NOT IN (
                            'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                        )
                        AND c.extraction_id = (
                            SELECT latest.id FROM paper_extractions latest
                            WHERE latest.paper_id = p.id
                            ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
                        )
                        AND EXISTS (
                            SELECT 1 FROM collection_papers cp
                            JOIN collection_runs run ON run.id = cp.run_id
                            WHERE run.topic_id = ? AND cp.paper_id = p.id
                                AND cp.full_text_decision = 'included'
                        )
                    ORDER BY c.id
                    """,
                    (topic["condition_code"], topic["id"]),
                ).fetchall()
                groups: dict[str, dict[str, object]] = {}
                for row in rows:
                    item = _augment_profile_population(connection, dict(row))
                    item["risk_of_bias"] = json.loads(item.pop("risk_of_bias_json"))
                    for scope_key, scope_label in _profile_scopes(
                        base["picots"], str(topic["condition_code"]), item
                    ).items():
                        group = groups.setdefault(
                            scope_key,
                            {
                                "scope_key": scope_key,
                                "dimensions": _synthesis_dimensions(base["picots"], scope_label),
                                "claims": [],
                            },
                        )
                        group["claims"].append(item)  # type: ignore[union-attr]
                candidates.append(
                    {
                        **base,
                        "status": "ready",
                        "groups": [groups[key] for key in sorted(groups)],
                    }
                )
        return candidates

    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        *,
        actor: str,
        detail: object,
    ) -> None:
        with self.database.transaction() as connection:
            self._audit(connection, entity_type, entity_id, action, actor, detail)

    def list_cards(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT kc.id, kc.condition_code, kc.version, kc.status, kc.grade,
                    kc.evidence_profile_id, ep.topic_id, ep.scope_key,
                    kc.reviewer, kc.reviewed_at, kc.published_at, kc.patient_visible_body,
                    count(cc.claim_id) AS claim_count,
                    group_concat(cc.claim_id) AS claim_ids,
                    group_concat(DISTINCT c.paper_id) AS paper_ids
                FROM knowledge_cards kc
                LEFT JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
                LEFT JOIN card_claims cc ON cc.card_id = kc.id
                LEFT JOIN claims c ON c.id = cc.claim_id
                GROUP BY kc.id ORDER BY kc.created_at DESC, kc.id DESC
                """
            ).fetchall()
        cards = []
        for row in rows:
            card = dict(row)
            card["claim_ids"] = str(card["claim_ids"] or "").split(",") if card["claim_ids"] else []
            card["paper_ids"] = str(card["paper_ids"] or "").split(",") if card["paper_ids"] else []
            cards.append(card)
        return cards

    def list_coverage_matrix(self) -> list[dict[str, object]]:
        """Derive first-batch coverage from the existing evidence tables."""

        with self.database.connect() as connection:
            conditions = connection.execute(
                """
                SELECT code, name, metrics_json, department, recheck_direction
                FROM conditions ORDER BY code
                """
            ).fetchall()
            matrix: list[dict[str, object]] = []
            for condition in conditions:
                topics = connection.execute(
                    "SELECT * FROM evidence_topics WHERE condition_code = ?",
                    (condition["code"],),
                ).fetchall()
                metrics = json.loads(condition["metrics_json"]) or [None]
                for metric_code in metrics:
                    scope_key = (
                        f"metric:{metric_code}" if metric_code else f"condition:{condition['code']}"
                    )
                    profile_topic_ids = {
                        row["topic_id"]
                        for row in connection.execute(
                            """
                            SELECT topic_id FROM evidence_profiles
                            WHERE condition_code = ? AND scope_key = ?
                            """,
                            (condition["code"], scope_key),
                        ).fetchall()
                    }
                    matching_topics = [
                        topic
                        for topic in topics
                        if topic["id"] in profile_topic_ids
                        or not metric_code
                        or _metric_outcome_matches_text(
                            metric_code, json.loads(topic["picots_json"]).get("outcomes", "")
                        )
                    ]
                    topic_ids = tuple(topic["id"] for topic in matching_topics)
                    topic_counts = {
                        "total": len(matching_topics),
                        "locked": sum(topic["status"] == "locked" for topic in matching_topics),
                    }
                    if topic_ids:
                        placeholders = _placeholders(topic_ids)
                        run_counts = connection.execute(
                            f"""
                            SELECT count(DISTINCT cr.id) AS completed_runs,
                                count(DISTINCT CASE WHEN cp.full_text_decision = 'included'
                                    THEN cp.paper_id END) AS full_text_included,
                                count(DISTINCT CASE WHEN cp.title_abstract_decision IS NULL
                                    THEN cp.paper_id END) AS title_abstract_pending,
                                count(DISTINCT CASE WHEN cp.title_abstract_decision = 'included'
                                    AND cp.full_text_retrieval_status = 'pending'
                                    THEN cp.paper_id END) AS retrieval_pending,
                                count(DISTINCT CASE WHEN cp.title_abstract_decision = 'included'
                                    AND cp.full_text_retrieval_status = 'retrieved'
                                    AND cp.full_text_decision IS NULL
                                    THEN cp.paper_id END) AS full_text_screening_pending
                            FROM collection_runs cr
                            LEFT JOIN collection_papers cp ON cp.run_id = cr.id
                            WHERE cr.topic_id IN ({placeholders}) AND cr.status = 'completed'
                            """,
                            topic_ids,
                        ).fetchone()
                    else:
                        run_counts = {
                            "completed_runs": 0,
                            "full_text_included": 0,
                            "title_abstract_pending": 0,
                            "retrieval_pending": 0,
                            "full_text_screening_pending": 0,
                        }
                    claim_rows = (
                        connection.execute(
                            f"""
                            SELECT DISTINCT cr.claim_id, c.extraction_id, r.population,
                                r.ingredient_name, r.ingredient_form, r.dose,
                                r.outcome, r.timepoint, topic.picots_json
                            FROM claim_reviews cr
                            JOIN claims c ON c.id = cr.claim_id
                            JOIN results r ON r.id = c.result_id
                            JOIN papers p ON p.id = c.paper_id
                            JOIN paper_admissions pa ON pa.paper_id = p.id
                            JOIN collection_papers cp ON cp.paper_id = c.paper_id
                                AND cp.full_text_decision = 'included'
                            JOIN collection_runs run ON run.id = cp.run_id
                            JOIN evidence_topics topic ON topic.id = run.topic_id
                            WHERE cr.condition_code = ? AND cr.decision = 'approved'
                                AND p.integrity_status = 'clear'
                                AND p.publication_status = 'formal'
                                AND pa.status = 'internally_admitted'
                                AND c.candidate_claim_type = 'intervention_effect'
                                AND cr.corrected_study_design NOT IN (
                                    'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                                )
                                AND run.topic_id IN ({_placeholders(topic_ids)})
                            """,
                            (condition["code"], *topic_ids),
                        ).fetchall()
                        if topic_ids
                        else []
                    )
                    approved_claims = len(
                        {
                            row["claim_id"]
                            for row in claim_rows
                            if not metric_code
                            or f"metric:{metric_code}"
                            in _profile_scopes(
                                json.loads(row["picots_json"]),
                                str(condition["code"]),
                                _augment_profile_population(connection, dict(row)),
                            )
                        }
                    )
                    profiles = connection.execute(
                        """
                        SELECT count(*) FROM evidence_profiles
                        WHERE condition_code = ? AND scope_key = ?
                        """,
                        (condition["code"], scope_key),
                    ).fetchone()[0]
                    card_rows = connection.execute(
                        """
                        SELECT kc.id, kc.version, kc.status, kc.grade, kc.published_at
                        FROM knowledge_cards kc
                        JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
                        WHERE kc.condition_code = ? AND ep.scope_key = ?
                        ORDER BY kc.published_at DESC, kc.created_at DESC, kc.id DESC
                        """,
                        (condition["code"], scope_key),
                    ).fetchall()
                    card_counts = {
                        status: sum(row["status"] == status for row in card_rows)
                        for status in ("draft", "in_review", "approved", "published", "stale")
                    }
                    published = next(
                        (row for row in card_rows if row["status"] == "published"), None
                    )
                    publishable_approved = {
                        str(row["id"])
                        for row in card_rows
                        if row["status"] == "approved"
                        and _card_passes_publish_gate(connection, row)
                    }
                    action_publishable_approved = any(
                        str(row["id"]) in publishable_approved
                        and row["grade"] in {"high", "moderate"}
                        for row in card_rows
                    )
                    context_publishable_approved = any(
                        str(row["id"]) in publishable_approved and row["grade"] == "low"
                        for row in card_rows
                    )
                    approved_gate_blocked = any(
                        row["status"] == "approved" and str(row["id"]) not in publishable_approved
                        for row in card_rows
                    )
                    if published and published["grade"] == "low":
                        coverage_status = "published_context"
                        next_action = "已发布证据背景卡；补充证据达到中等或高确定性后再开放行动建议"
                    elif published:
                        coverage_status, next_action = "published", "已覆盖，可继续扩充同主题证据"
                    elif action_publishable_approved:
                        coverage_status = "ready_to_publish"
                        next_action = "按发布状态机完成最后审核"
                    elif context_publishable_approved:
                        coverage_status = "ready_to_publish_context"
                        next_action = "发布为证据背景卡；行动建议仍需中等或高确定性"
                    elif any(
                        row["status"] == "approved" and row["grade"] == "very_low"
                        for row in card_rows
                    ):
                        coverage_status = "blocked_very_low_certainty"
                        next_action = "补充或合并证据体后再进入患者端"
                    elif approved_gate_blocked:
                        coverage_status = "publication_gate_blocked"
                        next_action = "解决证据、偏倚或原文完整性闸门后再发布"
                    elif profiles:
                        coverage_status, next_action = "profile_ready", "创建并审核患者知识卡"
                    elif any(
                        run_counts[key]
                        for key in (
                            "title_abstract_pending",
                            "retrieval_pending",
                            "full_text_screening_pending",
                        )
                    ):
                        coverage_status, next_action = "screening", "完成题录、全文获取和筛选"
                    elif approved_claims and run_counts["full_text_included"]:
                        coverage_status = "claims_ready"
                        next_action = "合并同一 PICOTS 的 Evidence Profile"
                    elif run_counts["full_text_included"]:
                        coverage_status = "full_text_ready"
                        next_action = "完成全文抽取与 Claim 审核"
                    elif run_counts["completed_runs"]:
                        coverage_status = "no_eligible_evidence"
                        next_action = "扩展检索，当前主题尚无全文纳入证据"
                    elif topic_counts["locked"]:
                        coverage_status, next_action = "topic_locked", "启动该主题的论文检索"
                    else:
                        coverage_status, next_action = "planned", "建立并锁定版本化主题/PICOTS"
                    matrix.append(
                        {
                            "condition_code": condition["code"],
                            "condition_name": condition["name"],
                            "department": condition["department"],
                            "recheck_direction": condition["recheck_direction"],
                            "metric_code": metric_code,
                            "metric_label": METRIC_LABELS.get(metric_code) if metric_code else None,
                            "scope_key": scope_key,
                            "topic_count": topic_counts["total"] or 0,
                            "locked_topic_count": topic_counts["locked"] or 0,
                            "completed_run_count": run_counts["completed_runs"] or 0,
                            "full_text_included_count": run_counts["full_text_included"] or 0,
                            "screening_backlog": {
                                "title_abstract": run_counts["title_abstract_pending"] or 0,
                                "retrieval": run_counts["retrieval_pending"] or 0,
                                "full_text": run_counts["full_text_screening_pending"] or 0,
                            },
                            "approved_claim_count": approved_claims,
                            "evidence_profile_count": profiles,
                            "cards": card_counts,
                            "published_card": dict(published) if published else None,
                            "coverage_status": coverage_status,
                            "next_action": next_action,
                        }
                    )
        return matrix

    @staticmethod
    def _require_publishable(connection, card_id: str) -> None:
        card = connection.execute(
            """
            SELECT kc.condition_code, kc.evidence_profile_id, kc.grade, ep.topic_id
            FROM knowledge_cards kc
            LEFT JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
            WHERE kc.id = ?
            """,
            (card_id,),
        ).fetchone()
        if card["topic_id"] is None:
            raise ValueError("knowledge card has no governed evidence topic")
        _require_complete_topic(connection, card["topic_id"], card["condition_code"])
        evidence = connection.execute(
            """
            SELECT count(*) AS total,
                sum(CASE WHEN cr.decision <> 'approved'
                    OR p.integrity_status <> 'clear'
                    OR p.publication_status <> 'formal'
                    OR pa.status <> 'internally_admitted'
                    OR p.doi IS NULL OR trim(p.doi) = ''
                    OR ft.paper_id IS NULL OR ft.processed_at IS NULL
                    OR trim(cc.evidence_text) = '' OR trim(cc.locator) = ''
                    OR c.candidate_claim_type <> 'intervention_effect'
                    OR cr.corrected_study_design IN (
                        'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                    )
                    OR cr.condition_code <> ? THEN 1 ELSE 0 END) AS invalid
            FROM card_claims cc
            JOIN claims c ON c.id = cc.claim_id
            JOIN claim_reviews cr ON cr.claim_id = c.id
            JOIN papers p ON p.id = c.paper_id
            JOIN paper_admissions pa ON pa.paper_id = p.id
            LEFT JOIN full_texts ft ON ft.paper_id = p.id
            WHERE cc.card_id = ?
            """,
            (card["condition_code"], card_id),
        ).fetchone()
        missing_profile_results = connection.execute(
            """
            SELECT count(*) FROM card_claims cc
            JOIN claims c ON c.id = cc.claim_id
            LEFT JOIN evidence_profile_results epr
                ON epr.profile_id = ? AND epr.result_id = c.result_id
            WHERE cc.card_id = ? AND epr.result_id IS NULL
            """,
            (card["evidence_profile_id"], card_id),
        ).fetchone()[0]
        scope_key = connection.execute(
            "SELECT ep.scope_key FROM knowledge_cards kc "
            "JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id "
            "WHERE kc.id = ?",
            (card_id,),
        ).fetchone()[0]
        if (
            evidence["total"] == 0
            or evidence["invalid"]
            or missing_profile_results
            or not str(scope_key or "").strip()
        ):
            raise ValueError("knowledge card has ineligible evidence")
        if card["grade"] == "low":
            high_risk = connection.execute(
                """
                SELECT 1
                FROM card_claims cc
                JOIN claim_reviews cr ON cr.claim_id = cc.claim_id
                WHERE cc.card_id = ?
                    AND json_extract(cr.risk_of_bias_json, '$.overall')
                        IN ('high', 'critical', 'uncertain')
                LIMIT 1
                """,
                (card_id,),
            ).fetchone()
            if high_risk:
                raise ValueError("context cards require resolved non-high risk-of-bias judgments")

    @staticmethod
    def _stale_cards_for_paper(connection, paper_id: str) -> int:
        return connection.execute(
            """
            UPDATE knowledge_cards SET status = 'stale'
            WHERE status IN ('draft', 'in_review', 'approved', 'published') AND id IN (
                SELECT cc.card_id FROM card_claims cc
                JOIN claims c ON c.id = cc.claim_id WHERE c.paper_id = ?
            )
            """,
            (paper_id,),
        ).rowcount

    @staticmethod
    def _audit(
        connection,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        detail: object,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(entity_type, entity_id, action, actor, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, action, actor, json.dumps(detail, ensure_ascii=False), _now()),
        )


def _card_passes_publish_gate(connection, card) -> bool:
    try:
        ReviewStore._require_publishable(connection, str(card["id"]))
    except ValueError:
        return False
    return True


def _placeholders(values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("at least one value is required")
    return ",".join("?" for _ in values)


def _require_complete_topic(connection, topic_id: str, condition_code: str):
    topic = connection.execute("SELECT * FROM evidence_topics WHERE id = ?", (topic_id,)).fetchone()
    if topic is None or topic["status"] != "locked" or topic["condition_code"] != condition_code:
        raise ValueError("a locked matching evidence topic is required")
    missing_stream = connection.execute(
        """
        SELECT 1 FROM json_each(?) required
        WHERE NOT EXISTS (
            SELECT 1 FROM collection_runs cr
            WHERE cr.topic_id = ? AND cr.search_stream = required.value
                AND cr.status = 'completed' AND cr.completed_at IS NOT NULL
        ) LIMIT 1
        """,
        (topic["required_search_streams_json"], topic_id),
    ).fetchone()
    running_search = connection.execute(
        "SELECT 1 FROM collection_runs WHERE topic_id = ? AND status = 'running' LIMIT 1",
        (topic_id,),
    ).fetchone()
    incomplete_screening = connection.execute(
        """
        SELECT 1 FROM collection_papers cp
        JOIN collection_runs cr ON cr.id = cp.run_id
        WHERE cr.topic_id = ? AND cr.status = 'completed' AND (
            cp.title_abstract_decision IS NULL
            OR (cp.title_abstract_decision = 'included' AND (
                cp.full_text_retrieval_status IS NULL
                OR cp.full_text_retrieval_status NOT IN ('retrieved', 'not_retrieved')
                OR (cp.full_text_retrieval_status = 'retrieved' AND (
                    cp.full_text_decision IS NULL OR NOT EXISTS (
                        SELECT 1 FROM full_texts ft WHERE ft.paper_id = cp.paper_id
                    )
                ))
                OR (cp.full_text_retrieval_status = 'not_retrieved' AND (
                    cp.full_text_decision IS NOT NULL
                    OR cp.full_text_retrieval_reason IS NULL
                    OR trim(cp.full_text_retrieval_reason) = ''
                    OR cp.full_text_retrieval_reviewer IS NULL
                    OR trim(cp.full_text_retrieval_reviewer) = ''
                    OR cp.full_text_retrieval_recorded_at IS NULL
                    OR EXISTS (SELECT 1 FROM full_texts ft WHERE ft.paper_id = cp.paper_id)
                ))
            ))
            OR (COALESCE(cp.full_text_decision, cp.title_abstract_decision) = 'excluded' AND (
                cp.primary_exclusion_reason IS NULL OR NOT EXISTS (
                    SELECT 1 FROM json_each(?) reason
                    WHERE reason.value = cp.primary_exclusion_reason
                )
            ))
        ) LIMIT 1
        """,
        (topic_id, topic["exclusion_reasons_json"]),
    ).fetchone()
    conflicting_screening = connection.execute(
        """
        SELECT 1 FROM collection_papers cp JOIN collection_runs cr ON cr.id = cp.run_id
        WHERE cr.topic_id = ? AND cr.status = 'completed'
        GROUP BY cp.paper_id
        HAVING count(DISTINCT COALESCE(cp.full_text_decision, cp.title_abstract_decision)) > 1
        LIMIT 1
        """,
        (topic_id,),
    ).fetchone()
    incomplete_included_paper = connection.execute(
        """
        SELECT 1 FROM collection_papers cp
        JOIN collection_runs cr ON cr.id = cp.run_id
        LEFT JOIN full_texts ft ON ft.paper_id = cp.paper_id
        LEFT JOIN paper_extractions pe ON pe.id = (
            SELECT id FROM paper_extractions
            WHERE paper_id = cp.paper_id ORDER BY created_at DESC, id DESC LIMIT 1
        )
        LEFT JOIN paper_admissions pa ON pa.paper_id = cp.paper_id
            WHERE cr.topic_id = ? AND cr.status = 'completed'
                AND cp.full_text_decision = 'included' AND (
                ft.paper_id IS NULL OR pe.id IS NULL OR pa.status <> 'internally_admitted'
                OR NOT EXISTS (
                    SELECT 1 FROM json_each(pa.condition_codes_json) admitted
                    WHERE admitted.value = ?
                )
                OR EXISTS (
                    SELECT 1 FROM study_publications sp JOIN studies s ON s.id = sp.study_id
                    WHERE sp.paper_id = cp.paper_id AND NOT EXISTS (
                        SELECT 1 FROM json_each(?) design WHERE design.value = s.study_design
                    )
                )
                OR EXISTS (
                    SELECT 1 FROM claims c LEFT JOIN claim_reviews review ON review.claim_id = c.id
                    WHERE c.paper_id = cp.paper_id AND (
                        c.status NOT IN ('reviewed', 'rejected') OR review.claim_id IS NULL
                        OR review.reviewer IS NULL OR trim(review.reviewer) = ''
                    )
                )
            )
        LIMIT 1
        """,
        (topic_id, condition_code, topic["eligible_study_designs_json"]),
    ).fetchone()
    included_count = connection.execute(
        """
        SELECT count(DISTINCT cp.paper_id) FROM collection_papers cp
        JOIN collection_runs cr ON cr.id = cp.run_id
        WHERE cr.topic_id = ? AND cr.status = 'completed'
            AND cp.full_text_decision = 'included'
        """,
        (topic_id,),
    ).fetchone()[0]
    if missing_stream:
        raise ValueError("every required search stream must have a completed collection run")
    if running_search:
        raise ValueError("every active topic collection run must finish before profile creation")
    if incomplete_screening:
        raise ValueError("topic screening ledger is incomplete or has an invalid exclusion reason")
    if conflicting_screening:
        raise ValueError("deduplicated papers cannot have conflicting topic screening decisions")
    if not included_count:
        raise ValueError("topic has no full-text included evidence")
    # A fully reviewed paper may have no patient-profile claim after its
    # out-of-scope, mechanistic, or associational results are rejected. Those
    # results remain auditable but must not block a profile built from other
    # directly matching evidence. Unreviewed claims are still blocked above.
    if incomplete_included_paper:
        raise ValueError("every full-text included paper must be extracted, admitted, and reviewed")
    return topic


_RISK_OF_BIAS_TOOL = {
    "randomized_controlled_trial": "rob2",
    "systematic_review_meta_analysis": "robis",
    "non_randomized_controlled_study": "robins_i",
    "natural_experiment": "robins_i",
    "biomarker_validation_study": "diagnostic_accuracy",
    "cohort_study": "exposure_study",
    "case_control_study": "exposure_study",
    "cross_sectional_study": "exposure_study",
    "ecological_study": "exposure_study",
    "case_series": "safety_signal",
    "case_report": "safety_signal",
}


def _collection_dict(row, extraction: dict[str, object]) -> dict[str, object]:
    item = {
        **{
            key: value
            for key, value in dict(row).items()
            if key
            not in {
                "picots_json",
                "eligible_study_designs_json",
                "exclusion_reasons_json",
            }
        },
        "picots": json.loads(row["picots_json"]),
        "eligible_study_designs": json.loads(row["eligible_study_designs_json"]),
        "exclusion_reasons": json.loads(row["exclusion_reasons_json"]),
    }
    design = str(extraction.get("study_design") or "uncertain")
    if not item["title_abstract_decision"]:
        item["screening_suggestion"] = {
            "stage": "title_abstract",
            "decision": "included",
            "primary_exclusion_reason": None,
            "reason": "题录初筛优先保证召回率；信息不足但可能符合时进入全文筛选。",
        }
    elif (
        item["title_abstract_decision"] == "included"
        and item["full_text_retrieval_status"] != "not_retrieved"
        and not item["full_text_decision"]
    ):
        eligible = design in item["eligible_study_designs"]
        wrong_design = next(
            (code for code in item["exclusion_reasons"] if "design" in code.casefold()),
            None,
        )
        picots_ready, picots_exclusion = (
            _picots_exclusion(item, extraction) if eligible else (True, None)
        )
        exclusion = picots_exclusion if eligible else wrong_design
        item["screening_suggestion"] = {
            "stage": "full_text",
            "decision": "included"
            if eligible and picots_ready and not exclusion
            else ("excluded" if exclusion else ""),
            "primary_exclusion_reason": exclusion,
            "reason": (
                f"AI-A 结构化事实与锁定主题 PICOTS 相符，研究设计为 {design}。"
                if eligible and picots_ready and not exclusion
                else (
                    f"AI-A 结构化事实不符合锁定主题，按 {exclusion} 全文排除。"
                    if exclusion
                    else (
                        "AI-A 缺少完成 PICOTS 判断所需的结构化事实，或主题未配置"
                        "对应排除原因；进入异常队列。"
                        if eligible
                        else (
                            f"AI-A 研究设计为 {design}，不在主题允许设计中，"
                            "但主题未配置对应排除原因；进入异常队列。"
                        )
                    )
                )
            ),
        }
    elif (
        item["title_abstract_decision"] == "included"
        and item["full_text_decision"] == "excluded"
        and str(item["full_text_reviewer"] or "").startswith("ai:")
        and item["full_text_retrieval_status"] != "not_retrieved"
    ):
        eligible = design in item["eligible_study_designs"]
        wrong_design = next(
            (code for code in item["exclusion_reasons"] if "design" in code.casefold()),
            None,
        )
        picots_ready, picots_exclusion = (
            _picots_exclusion(item, extraction) if eligible else (True, None)
        )
        if eligible and picots_ready and not picots_exclusion:
            item["screening_suggestion"] = {
                "stage": "full_text",
                "decision": "included",
                "primary_exclusion_reason": None,
                "reason": "当前 PICOTS 规则已更新，AI 重新评估该全文为可纳入。",
            }
        elif not eligible and wrong_design:
            item["screening_suggestion"] = None
    else:
        item["screening_suggestion"] = None
    return item


def _picots_exclusion(
    collection: dict[str, object], extraction: dict[str, object]
) -> tuple[bool, str | None]:
    picots = collection["picots"]
    reasons = collection["exclusion_reasons"]
    checks = (
        (
            "population",
            " ".join(str(value) for value in extraction.get("population", [])),
            ("population",),
        ),
        (
            "intervention_or_exposure",
            " ".join(
                [*(str(value) for value in extraction.get("studied_approach", []))]
                + [
                    f"{claim.get('ingredient_name', '')} {claim.get('ingredient_form', '')}"
                    for claim in extraction.get("claims", [])
                    if isinstance(claim, dict)
                ]
            ),
            ("intervention", "exposure"),
        ),
        (
            "outcomes",
            " ".join(
                [*(str(value) for value in extraction.get("outcomes", []))]
                + [
                    str(claim.get("outcome", ""))
                    for claim in extraction.get("claims", [])
                    if isinstance(claim, dict)
                ]
            ),
            ("outcome",),
        ),
        (
            "comparator",
            " ".join(
                str(claim.get("comparator", ""))
                for claim in extraction.get("claims", [])
                if isinstance(claim, dict)
            ),
            ("comparator",),
        ),
        (
            "timing",
            " ".join(
                str(claim.get("timepoint", ""))
                for claim in extraction.get("claims", [])
                if isinstance(claim, dict)
            ),
            ("timing", "duration"),
        ),
    )
    for topic_field, extracted_text, reason_tokens in checks:
        topic_text = str(picots.get(topic_field) or "")
        if not extracted_text.strip():
            return False, None
        if not _picots_text_matches(topic_text, extracted_text):
            reason = next(
                (
                    reason
                    for reason in reasons
                    if any(token in str(reason).casefold() for token in reason_tokens)
                ),
                None,
            )
            return reason is not None, reason
    candidates = {
        str(value.get("condition_code"))
        for value in extraction.get("condition_candidates", [])
        if isinstance(value, dict)
    }
    if not candidates:
        return False, None
    if collection["topic_condition_code"] not in candidates:
        reason = next((reason for reason in reasons if "outcome" in str(reason).casefold()), None)
        return reason is not None, reason
    return True, None


def _picots_text_matches(
    topic_text: str, extracted_text: str, *, require_qualifiers: bool = True
) -> bool:
    topic_text = _normalize_picots_text(topic_text)
    extracted_text = _normalize_picots_text(extracted_text)
    # Comparator phrases can contain "nutrition intervention" while the
    # actual match is the no-treatment/placebo arm; resolve that explicit
    # overlap before the nutrition-exposure guard below.
    if "no intervention" in topic_text and "no intervention" in extracted_text:
        return True
    if "placebo" in topic_text and "placebo" in extracted_text:
        return True
    aliases = {
        "bp": ("blood", "pressure"),
        "sbp": ("blood", "pressure"),
        "dbp": ("blood", "pressure"),
        "salt": ("sodium",),
        "fat": ("fatty",),
        "men": ("adult",),
        "women": ("adult",),
        "adults": ("adult",),
        "control": ("control", "usual", "alternative"),
        "placebo": ("placebo", "usual", "control"),
        "alternative": ("alternative", "intervention"),
        "supplement": ("supplement", "intervention"),
        "collagen": ("collagen", "protein"),
        "25ohd": ("25", "vitamin"),
        "hydroxyvitamin": ("vitamin",),
    }
    stopwords = {
        "and",
        "or",
        "the",
        "of",
        "with",
        "versus",
        "usual",
        "alternative",
        "aged",
        "older",
        "serum",
        "concentration",
        "dietary",
    }

    def tokens(value: str) -> set[str]:
        raw = re.findall(r"[a-z0-9]+", value.casefold())
        result = set()
        if {"25", "oh", "d"} <= set(raw):
            result.update(("25", "vitamin"))
        for token in raw:
            if len(token) <= 1 or token in stopwords:
                continue
            for canonical in aliases.get(token, (token.rstrip("s"),)):
                canonical = next(
                    (
                        stem
                        for prefix, stem in (
                            ("supplement", "supplement"),
                            ("reduc", "reduce"),
                            ("modif", "modify"),
                        )
                        if canonical.startswith(prefix)
                    ),
                    canonical,
                )
                result.add(canonical)
        return result

    topic_age = re.search(r"\baged\s*(?:≥|>=|>)?\s*(\d{1,3})", topic_text.casefold())
    if topic_age:
        extracted_age = re.search(r"\baged\s*(?:≥|>=|>)?\s*(\d{1,3})", extracted_text.casefold())
        extracted_folded = extracted_text.casefold()
        adult_scope = int(topic_age.group(1)) <= 18 and re.search(r"\badults?\b", extracted_folded)
        # A locked adult-40+ topic may receive an explicit postmenopausal
        # population label; this is a bounded adult proxy, not a generic
        # inference for women or older-sounding text.
        postmenopausal_scope = int(topic_age.group(1)) >= 40 and re.search(
            r"\bpostmenopausal\b", extracted_folded
        )
        alternate_population_scope = _matches_alternate_population_scope(topic_text, extracted_text)
        if (
            not adult_scope
            and not postmenopausal_scope
            and not alternate_population_scope
            and (not extracted_age or int(extracted_age.group(1)) < int(topic_age.group(1)))
        ):
            return False
    topic_duration = re.search(
        r"\b(?:(?:at least|minimum(?: of)?)\s*)?(\d+(?:\.\d+)?)\s*"
        r"(day|week|month|year)s?(?:\s*(?:or|and)\s*(?:longer|more))?",
        topic_text.casefold(),
    )
    if topic_duration:
        extracted_durations = re.findall(
            r"(\d+(?:\.\d+)?)\s*(?:\w+\s+)?(day|week|month|year)s?",
            extracted_text.casefold(),
        )
        if not extracted_durations:
            return False
        weeks = {"day": 1 / 7, "week": 1, "month": 4.345, "year": 52}
        topic_weeks = float(topic_duration.group(1)) * weeks[topic_duration.group(2)]
        if max(float(amount) * weeks[unit] for amount, unit in extracted_durations) < topic_weeks:
            return False
    # A generic nutrition PICOTS describes a class of exposures, not one
    # literal ingredient. Keep an explicit marker requirement so unrelated
    # exercise, medication, or missing-exposure text does not pass.
    nutrition_topic = any(
        marker in topic_text
        for marker in (
            "dietary pattern",
            "defined food",
            "nutrient intervention",
            "nutrition intervention",
        )
    )
    if nutrition_topic:
        nutrition_markers = {
            "diet",
            "dietary",
            "food",
            "ferric",
            "ferrous",
            "iron",
            "nutrient",
            "protein",
            "vitamin",
            "mineral",
            "supplement",
            "collagen",
            "fiber",
            "fibre",
            "fat",
            "oil",
            "salt",
            "sodium",
            "potassium",
            "calcium",
            "soy",
            "isoflavone",
            "barley",
            "grain",
            "fruit",
            "vegetable",
            "milk",
            "tea",
            "coffee",
            "beverage",
            "drink",
            "water",
            "alkaline",
            "electrolyte",
            "omega",
            "probiotic",
            "prebiotic",
        }
        extracted_words = set(re.findall(r"[a-z0-9]+", extracted_text.casefold()))
        return bool(nutrition_markers & extracted_words)
    if re.search(r"\b(?:usual|alternative|placebo)\b", topic_text) and re.search(
        r"\b(?:control|usual|alternative|placebo)\b", extracted_text
    ):
        return True
    topic_tokens = tokens(topic_text)
    extracted_tokens = tokens(extracted_text)
    qualifiers = topic_tokens & {"supplement", "reduce", "modify"}
    if {"usual", "alternative"} & extracted_tokens and {
        "usual",
        "alternative",
    } & topic_tokens:
        return True
    return bool(topic_duration) or (
        bool(topic_tokens & extracted_tokens)
        and (not require_qualifiers or qualifiers <= extracted_tokens)
    )


def _normalize_picots_text(value: str) -> str:
    """Normalize common bilingual extraction terms before PICOTS matching."""

    replacements = {
        "绝经后": "postmenopausal",
        "绝经": "postmenopausal",
        "年龄": "aged ",
        "成年人": "adults",
        "成人": "adults",
        "女性": "women",
        "男性": "men",
        "儿童": "children",
        "老年人": "older adults",
        "肌少症": "sarcopenia",
        "衰弱": "frailty",
        "骨质疏松": "osteoporosis",
        "骨量减少": "osteopenia",
        "骨密度": "bone mineral density",
        "bmd": "bone mineral density",
        "慢性肾脏病": "chronic kidney disease",
        "肾脏病": "kidney disease",
        "肾功能": "kidney function",
        "ckd": "chronic kidney disease",
        "患者": "patients",
        "病人": "patients",
        "缺铁性贫血": "iron deficiency anemia",
        "缺铁": "iron deficiency",
        "贫血": "anemia",
        "ida": "iron deficiency anemia",
        "大麦嫩叶": "barley green",
        "大麦": "barley",
        "电解碱性水": "electrolyzed alkaline water",
        "碱性水": "alkaline water",
        "大豆": "soy",
        "异黄酮": "isoflavone",
        "蛋白质": "protein",
        "营养素": "nutrient",
        "食物": "food",
        "口服": "oral",
        "铁": "iron",
        "饮用": "drink",
        "服用": "consume",
        "摄入": "intake",
        "平衡膳食": "usual diet",
        "纯净中性水": "control water",
        "中性水": "control water",
        "碳酸氢钠": "sodium bicarbonate",
        "氯化钠": "sodium chloride",
        "低钠高钾盐替代品": "low sodium high potassium salt substitute",
        "盐替代品": "salt substitute",
        "低钠": "low sodium",
        "高钾": "high potassium",
        "钠摄入": "sodium intake",
        "钾摄入": "potassium intake",
        "普通盐": "control salt",
        "收缩压": "systolic blood pressure",
        "舒张压": "diastolic blood pressure",
        "减少": "reduction",
        "降低": "reduction",
        "胆钙化醇": "cholecalciferol vitamin d",
        "胶原蛋白肽": "collagen protein peptide",
        "胶原蛋白": "collagen protein",
        "蛋白质补充剂": "protein supplementation",
        "膳食": "dietary",
        "饮食": "dietary",
        "对照组": "control",
        "普通鲜奶": "control milk",
        "普通牛奶": "control milk",
        "常规牛奶": "control milk",
        "对照牛奶": "control milk",
        "no treatment": "no intervention",
        "no-treatment": "no intervention",
        "regular milk": "control milk",
        "plain milk": "control milk",
        "安慰剂": "placebo",
        "常规护理": "usual care",
        "标准治疗": "standard care",
        "相互比较": "alternative",
        "补充": "supplement",
        "肌酐清除率": "creatinine clearance",
        "血清肌酐": "serum creatinine",
        "尿白蛋白肌酐比": "urine albumin creatinine ratio",
        "营养不良": "malnutrition",
        "便秘": "constipation",
        "血压": "blood pressure",
        "握力": "grip strength",
        "步速": "gait speed",
        "肌肉量": "muscle mass",
        "肌肉质量": "muscle mass",
        "骨骼肌质量": "skeletal muscle mass",
        "软瘦肉组织": "soft lean mass",
        "运动": "exercise",
        "钙": "calcium",
        "单独": "alternative",
        "血红蛋白": "hemoglobin",
        "铁蛋白": "ferritin",
        "尿酸": "uric acid",
        "甘油三酯": "triglycerides",
        "高密度脂蛋白胆固醇": "hdl cholesterol",
        "低密度脂蛋白胆固醇": "ldl cholesterol",
        "总胆固醇": "total cholesterol",
        "空腹血糖": "fasting glucose",
        "糖化血红蛋白": "hba1c",
        "维生素\u00a0d": "vitamin d",
        "维生素d": "vitamin d",
        "masld": "metabolic risk",
        "nafld": "metabolic risk",
        "周": " weeks ",
        "月": " months ",
        "年": " years ",
        "天": " days ",
        "岁": " years ",
    }
    normalized = value.casefold()
    normalized = re.sub(
        r"(\d{1,3})\s*岁\s*(?:或|及)?以上",
        lambda match: f" aged {match.group(1)} years and older ",
        normalized,
    )
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    duration_units = {"天": "days", "周": "weeks", "月": "months", "年": "years"}

    def replace_duration(match: re.Match[str]) -> str:
        raw, unit = match.groups()
        if "十" in raw:
            left, _, right = raw.partition("十")
            number = digits.get(left, 1) * 10 + digits.get(right, 0)
        else:
            number = digits[raw]
        return f" {number} {duration_units[unit]} "

    normalized = re.sub(
        r"([一二三四五六七八九十]+)\s*个?\s*(天|周|月|年)", replace_duration, normalized
    )
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(source, f" {target.strip()} ")
    return normalized


def _matches_alternate_population_scope(topic_text: str, extracted_text: str) -> bool:
    """Allow explicit non-age alternatives in versioned population PICOTS."""

    if re.search(r"\bchildren?\b", extracted_text):
        return False
    alternatives = {
        "kidney disease risk": ("kidney disease", "chronic kidney disease", "ckd"),
        "metabolic risk": (
            "metabolic risk",
            "metabolic dysfunction",
            "masld",
            "fatty liver",
            "obesity",
            "diabetes",
        ),
        "nutritional risk": ("nutritional risk", "malnutrition", "undernutrition"),
        "sarcopenia/frailty": ("sarcopenia", "frailty"),
    }
    if "nutritional risk" in topic_text and any(
        marker in extracted_text
        for marker in ("mna-sf", "mini nutritional assessment", "mna score")
    ):
        return True
    return any(
        marker in topic_text and any(term in extracted_text for term in terms)
        for marker, terms in alternatives.items()
    )


def _profile_scope_matches(picots: object, dimensions: dict[str, str]) -> bool:
    if not isinstance(picots, dict):
        return False
    for topic_field, result_field in (
        ("population", "population"),
        ("intervention_or_exposure", "ingredient_name"),
        ("outcomes", "outcome"),
        ("timing", "timepoint"),
    ):
        topic_value = str(picots.get(topic_field) or "").strip()
        result_value = dimensions[result_field].strip()
        if topic_field == "intervention_or_exposure":
            result_value = " ".join(
                value
                for key in ("ingredient_name", "ingredient_form", "dose")
                if (value := dimensions.get(key, "")).strip()
            )
        if topic_value and (
            not result_value
            or not _picots_text_matches(topic_value, result_value, require_qualifiers=False)
        ):
            return False
    return True


_PROFILE_OUTCOME_ALIASES = {
    "systolic_blood_pressure": (
        "systolicbloodpressure",
        "sbp",
        "收缩压",
    ),
    "diastolic_blood_pressure": (
        "diastolicbloodpressure",
        "dbp",
        "舒张压",
    ),
    "triglycerides": ("triglyceride", "triglycerides", "甘油三酯"),
    "hdl_c": (
        "hdlc",
        "hdlcholesterol",
        "highdensitylipoproteincholesterol",
        "高密度脂蛋白胆固醇",
    ),
    "ldl_c": (
        "ldlc",
        "ldlcholesterol",
        "lowdensitylipoproteincholesterol",
        "低密度脂蛋白胆固醇",
    ),
    "total_cholesterol": ("totalcholesterol", "总胆固醇"),
    "non_hdl_c": (
        "nonhdlc",
        "nonhdlcholesterol",
        "nonhighdensitylipoproteincholesterol",
        "非高密度脂蛋白胆固醇",
    ),
    "fasting_glucose": ("fastingglucose", "fastingbloodglucose", "fbg", "空腹血糖"),
    "hba1c": ("hba1c", "glycatedhemoglobin", "glycosylatedhemoglobin", "糖化血红蛋白"),
    "alt": ("alanineaminotransferase", "alaninetransaminase", "alt", "丙氨酸氨基转移酶"),
    "ast": ("aspartateaminotransferase", "aspartatetransaminase", "ast", "天门冬氨酸氨基转移酶"),
    "ggt": ("gammaglutamyltransferase", "gammaglutamyltranspeptidase", "ggt", "谷氨酰转移酶"),
    "uric_acid": ("uricacid", "serumuricacid", "urate", "尿酸"),
    "egfr": ("egfr", "estimatedglomerularfiltrationrate", "估算肾小球滤过率"),
    "creatinine": ("creatinine", "serumcreatinine", "肌酐"),
    "uacr": (
        "uacr",
        "urinealbumincreatinineratio",
        "urinealbumintocreatinineratio",
        "urinaryalbumincreatinineratio",
        "urinaryalbumintocreatinineratio",
        "尿白蛋白肌酐比",
    ),
    "hemoglobin": ("hemoglobin", "haemoglobin", "血红蛋白"),
    "mcv": ("mcv", "meancorpuscularvolume", "平均红细胞体积"),
    "ferritin": ("ferritin", "serumferritin", "铁蛋白"),
    "tsat": ("tsat", "transferrinsaturation", "转铁蛋白饱和度"),
    "25_oh_vitamin_d": (
        "25ohd",
        "25hydroxyvitamind",
        "25羟维生素d",
    ),
    "bone_density_t_score": (
        "bonemineraldensity",
        "bmd",
        "tscore",
        "bonedensitytscore",
        "bonemineraldensitytscore",
        "bmdtscore",
        "骨密度",
        "骨密度t值",
    ),
    "calcium": ("calcium", "serumcalcium", "bloodcalcium", "血钙", "钙"),
    "alp": ("alkalinephosphatase", "alp", "碱性磷酸酶"),
    "grip_strength": ("gripstrength", "handgripstrength", "握力"),
    "walking_speed": (
        "walkingspeed",
        "6mwalkingspeed",
        "gaitspeed",
        "walkingperformance",
        "步行速度",
        "步速",
    ),
    "muscle_mass": (
        "musclemass",
        "skeletalmusclemass",
        "appendicularskeletalmusclemass",
        "skeletalmusclemassindex",
        "appendicularskeletalmusclemassindex",
        "asmm",
        "smm",
        "smi",
        "leanmass",
        "fatfreemass",
        "softleanmass",
        "肌肉量",
        "肌肉质量",
        "骨骼肌质量",
        "软瘦肉组织",
    ),
    "albumin": ("albumin", "serumalbumin", "白蛋白"),
    "bmi": ("bodymassindex", "bmi", "体重指数"),
    "prealbumin": ("prealbumin", "transthyretin", "前白蛋白"),
}


def _compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", value).casefold())


def _metric_outcome_matches(metric_code: str, value: str) -> bool:
    compact = _compact_text(value)
    if metric_code in {"hdl_c", "ldl_c", "total_cholesterol"} and "ratio" in value.casefold():
        return False
    if metric_code == "hdl_c" and "nonhdl" in compact:
        return False
    if metric_code == "creatinine" and any(
        term in compact
        for term in (
            "clearance",
            "ratio",
            "uacr",
            "urine",
            "urinary",
            "excretion",
            "清除率",
            "比值",
            "尿",
        )
    ):
        return False
    aliases = _PROFILE_OUTCOME_ALIASES.get(metric_code, ())
    risky_abbreviations = {"alt", "ast", "alp"}
    if any(alias in compact for alias in aliases if alias not in risky_abbreviations):
        return True
    tokens = set(re.findall(r"[0-9a-z]+", unicodedata.normalize("NFKC", value).casefold()))
    if any(alias in tokens for alias in aliases if alias in risky_abbreviations):
        return True
    if metric_code == "systolic_blood_pressure":
        return "systolic" in compact and "bloodpressure" in compact
    if metric_code == "diastolic_blood_pressure":
        return "diastolic" in compact and "bloodpressure" in compact
    return False


def _topic_outcome_components(value: str) -> tuple[str, ...]:
    components = tuple(
        item.strip(" .")
        for item in re.split(r"\s*(?:[,;，；]|\b(?:and|or)\b|和|或)\s*", value, flags=re.I)
        if item.strip(" .")
    )
    return components or (value.strip(),)


def _metric_outcome_matches_text(metric_code: str, value: str) -> bool:
    """Match a metric in either a single or a compound reported outcome."""

    return _metric_outcome_matches(metric_code, value) or any(
        _metric_outcome_matches(metric_code, component)
        for component in _topic_outcome_components(value)
    )


def _augment_profile_population(connection, row: dict[str, object]) -> dict[str, object]:
    """Include the paper-level population when validating a result's PICOTS scope."""

    extraction_id = str(row.get("extraction_id") or "").strip()
    if not extraction_id:
        return row
    stored = connection.execute(
        "SELECT extraction_json FROM paper_extractions WHERE id = ?", (extraction_id,)
    ).fetchone()
    if stored is None:
        return row
    try:
        payload = json.loads(stored["extraction_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return row
    population = payload.get("population") if isinstance(payload, dict) else None
    if not isinstance(population, list):
        return row
    extracted = " ".join(str(value).strip() for value in population if str(value).strip())
    if extracted:
        row["population"] = " ".join(
            value for value in (str(row.get("population") or "").strip(), extracted) if value
        )
    return row


def _generic_scope_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    slug = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", normalized).strip("-")
    return f"outcome:{slug[:140]}"


def _profile_scopes(
    picots: object, condition_code: str, dimensions: dict[str, object]
) -> dict[str, str]:
    if not isinstance(picots, dict):
        return {}
    values = {key: str(value or "") for key, value in dimensions.items()}
    if not _profile_scope_matches(picots, values):
        return {}
    topic_outcome = str(picots.get("outcomes") or "").strip()
    result_outcome = values.get("outcome", "")
    compact_result = _compact_text(result_outcome)
    condition = CONDITION_BY_CODE.get(condition_code)
    if condition and not condition.metrics:
        if _picots_text_matches(topic_outcome, result_outcome, require_qualifiers=False):
            return {f"condition:{condition_code}": condition.name}
        return {}
    lipid_metrics = {"hdl_c", "ldl_c", "total_cholesterol"}
    if (
        condition
        and lipid_metrics.intersection(condition.metrics)
        and re.search(r"\bratio\b", result_outcome.casefold())
        and "nonhdl" not in compact_result
    ):
        return {}
    scopes = {
        f"metric:{metric_code}": METRIC_LABELS[metric_code]
        for metric_code in (condition.metrics if condition else ())
        if metric_code in _PROFILE_OUTCOME_ALIASES
        and _metric_outcome_matches_text(metric_code, topic_outcome)
        and _metric_outcome_matches_text(metric_code, result_outcome)
    }
    if scopes:
        return scopes
    for component in _topic_outcome_components(topic_outcome):
        if condition and any(
            _metric_outcome_matches(metric_code, component) for metric_code in condition.metrics
        ):
            continue
        if _picots_text_matches(component, result_outcome, require_qualifiers=False):
            scopes[_generic_scope_key(component)] = component
    return scopes


def _synthesis_dimensions(picots: object, outcome: str) -> dict[str, str]:
    if not isinstance(picots, dict):
        raise ValueError("locked topic PICOTS is required")
    return {
        "population": str(picots.get("population") or "Not restricted"),
        "baseline_nutrient_status": "Mixed or not restricted by the locked topic",
        "ingredient_name": str(picots.get("intervention_or_exposure") or "Not specified"),
        "ingredient_form": "As reported across included studies",
        "dose": "As reported across included studies",
        "comparator": str(picots.get("comparator") or "Not specified"),
        "outcome": outcome,
        "timepoint": str(picots.get("timing") or "As reported across included studies"),
    }


def _resolve_profile_scope(
    picots: object,
    condition_code: str,
    rows: list[dict[str, object]],
    *,
    requested: str,
    estimate_target: str,
) -> tuple[str, str]:
    scopes = [_profile_scopes(picots, condition_code, row) for row in rows]
    common = set(scopes[0]) if scopes else set()
    for item in scopes[1:]:
        common &= set(item)
    if requested:
        if requested not in common:
            raise ValueError("selected claims do not share the requested outcome scope")
        return requested, scopes[0][requested]
    if len(common) == 1:
        key = common.pop()
        return key, scopes[0][key]
    target_matches = [
        key
        for key in common
        if _picots_text_matches(scopes[0][key], estimate_target, require_qualifiers=False)
    ]
    if len(target_matches) == 1:
        key = target_matches[0]
        return key, scopes[0][key]
    raise ValueError("evidence profile requires one explicit shared outcome scope")


def _claim_dict(
    row,
    *,
    extraction: dict[str, object],
    collections: list[dict[str, object]],
) -> dict[str, object]:
    claim = dict(row)
    raw = claim.pop("risk_of_bias_json", None)
    claim["risk_of_bias"] = json.loads(raw) if raw else None
    extracted_claim = next(
        (
            item
            for item in extraction.get("claims", [])
            if isinstance(item, dict)
            and (
                (
                    item.get("evidence") == claim.get("evidence_text")
                    and item.get("locator") == claim.get("locator")
                )
                or item.get("text") == claim.get("candidate_text")
            )
        ),
        {},
    )
    extracted_claim_index = next(
        (
            index
            for index, item in enumerate(extraction.get("claims", []), 1)
            if item is extracted_claim
        ),
        None,
    )
    design = str(claim.get("candidate_study_design") or "uncertain")
    if design == "uncertain":
        design = str(extraction.get("study_design") or "uncertain")
    active_collections = [
        item
        for item in collections
        if item.get("title_abstract_decision") != "excluded"
        and item.get("full_text_decision") != "excluded"
    ]
    dimensions = {
        field: str(claim.get(field) or "") for field in ("population", "outcome", "timepoint")
    }
    dimensions["ingredient_name"] = " ".join(
        str(claim.get(field) or "") for field in ("ingredient_name", "ingredient_form", "dose")
    )
    dimensions["population"] = " ".join(
        (
            dimensions["population"],
            *(str(value) for value in extraction.get("population", [])),
        )
    )
    if extracted_claim_index:
        claim["extraction_claim_index"] = extracted_claim_index
    matching_conditions = list(
        dict.fromkeys(
            str(item["topic_condition_code"])
            for item in active_collections
            if _profile_scopes(item["picots"], str(item["topic_condition_code"]), dimensions)
        )
    )
    limitations = [str(value) for value in extraction.get("limitations", []) if str(value).strip()]
    tool = _RISK_OF_BIAS_TOOL.get(design, "other")
    condition_code = matching_conditions[0] if len(matching_conditions) == 1 else ""
    suggested_decision = (
        "approved" if condition_code else ("rejected" if not matching_conditions else "")
    )
    claim["review_suggestion"] = {
        "decision": suggested_decision,
        "decision_reason": (
            "Result scope matches one locked topic PICOTS."
            if suggested_decision == "approved"
            else (
                "Result scope does not match any locked topic PICOTS."
                if suggested_decision == "rejected"
                else "Result scope matches multiple topic conditions and needs exception review."
            )
        ),
        "corrected_text": claim.get("candidate_text") or "",
        "corrected_study_design": design,
        "inference": extracted_claim.get("inference") or "descriptive",
        "risk_of_bias": {
            "tool": tool,
            "overall": "uncertain",
            "rationale": (
                "AI 初审提取到的研究局限：" + "；".join(limitations[:4])
                if limitations
                else f"AI 未抽取到足够的偏倚判断依据；请按 {tool} 核对全文。"
            ),
        },
        "applicability": (
            f"AI 初审：研究人群为 {claim.get('population') or '未报告'}；"
            f"基线营养状态为 {claim.get('baseline_nutrient_status') or '未报告'}。"
            "请对照锁定主题 PICOTS 确认适用性。"
        ),
        "condition_code": condition_code,
        "human_checks": [
            "原文定位与摘录",
            "成分形式、剂量、对照",
            "结局、时间点、单位、方向、效应量与统计信息",
            "安全事件、协议偏离及适用性",
        ],
    }
    return claim


def _review_guidance(
    *,
    paper: dict[str, object],
    extraction: dict[str, object] | None,
    consistency: dict[str, object] | None,
    admission: dict[str, object] | None,
    collections: list[dict[str, object]],
    studies: list[dict[str, object]],
    claims: list[dict[str, object]],
    source_count: int,
) -> dict[str, object]:
    admission_status = str((admission or {}).get("status") or "pending")
    retrieval_records = [
        item for item in collections if item.get("full_text_retrieval_status") == "not_retrieved"
    ]
    retrieval_terminal = bool(retrieval_records) and all(
        item.get("title_abstract_decision") == "excluded"
        or item.get("full_text_retrieval_status") == "not_retrieved"
        for item in collections
    )
    if retrieval_terminal and extraction is None:
        reasons = "；".join(
            str(item.get("full_text_retrieval_reason") or "未记录原因")
            for item in retrieval_records
        )
        return {
            "state": "completed",
            "terminal_decision": "not_retrieved",
            "next_action": "全文未取得台账已闭合；该结果不构成科学排除，也不进入抽取异常队列。",
            "checks": [
                {
                    "id": "identity_integrity",
                    "label": "论文题录与来源",
                    "status": "pass",
                    "detail": (
                        f"已记录 {source_count} 个来源；"
                        f"完整性状态为 {paper.get('integrity_status')}。"
                    ),
                },
                {
                    "id": "topic_screening",
                    "label": "版本化主题与全文获取",
                    "status": "pass",
                    "detail": f"全文未取得原因已由具名执行者记录：{reasons}",
                },
                {
                    "id": "dual_ai",
                    "label": "两次独立同模型抽取与差异",
                    "status": "not_applicable",
                    "detail": "没有合法取得的全文，两次独立全文抽取不适用。",
                },
                {
                    "id": "structured_results",
                    "label": "Result、Claim 与原文定位",
                    "status": "not_applicable",
                    "detail": "没有全文抽取结果，不生成 Result 或 Claim。",
                },
                {
                    "id": "executing_actor",
                    "label": "论文准入与知识卡",
                    "status": "not_applicable",
                    "detail": "论文保持未准入状态，也不作为科学排除记录。",
                },
            ],
            "blockers": [],
            "issues": [],
            "admission_suggestion": {
                "condition_codes": [],
                "study_design": "uncertain",
                "publication_role": "primary",
                "consistency_resolution": "",
            },
        }
    incomplete_screening = any(
        not item.get("title_abstract_decision")
        or (
            item.get("title_abstract_decision") == "included"
            and item.get("full_text_retrieval_status") != "not_retrieved"
            and not item.get("full_text_decision")
        )
        for item in collections
    )
    included = [item for item in collections if item.get("full_text_decision") == "included"]
    all_excluded = bool(collections) and not incomplete_screening and not included
    result_fields = (
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
    structured_results = bool(claims) and all(
        all(str(claim.get(field) or "").strip() for field in result_fields) for claim in claims
    )
    issues = [
        {
            **issue,
            "priority": "must_resolve" if _critical_issue(issue, claims) else "verify",
        }
        for issue in (consistency or {}).get("issues", [])
        if isinstance(issue, dict)
    ]
    unresolved_consistency = (consistency or {}).get(
        "verdict"
    ) == "needs_review" and not _source_based_consistency_resolution(admission)
    material_issues = [issue for issue in issues if issue["priority"] == "must_resolve"]
    pending_claims = [claim for claim in claims if claim.get("status") == "candidate"]
    checks = [
        {
            "id": "identity_integrity",
            "label": "论文身份与完整性",
            "status": "pass"
            if paper.get("integrity_status") == "clear" and source_count and len(studies) == 1
            else "blocked",
            "detail": "来源、完整性状态和唯一 Study/Publication 关系齐全。"
            if paper.get("integrity_status") == "clear" and source_count and len(studies) == 1
            else "完整性必须为 clear，且需要来源和唯一 Study/Publication 关系。",
        },
        {
            "id": "topic_screening",
            "label": "版本化主题与 PICOTS 筛选",
            "status": (
                "blocked" if not collections else ("action" if incomplete_screening else "pass")
            ),
            "detail": "已有全文纳入记录。"
            if included
            else (
                "所有关联主题均已全文排除，AI 将保留台账并结束准入。"
                if all_excluded
                else (
                    "AI 将按锁定主题完成题录和全文筛选。"
                    if collections
                    else "论文尚未关联已完成采集的锁定主题。"
                )
            ),
        },
        {
            "id": "dual_ai",
            "label": "两次独立同模型抽取与差异",
            "status": (
                "blocked" if not extraction else ("action" if unresolved_consistency else "pass")
            ),
            "detail": (
                f"AI 将逐项记录 {len(issues)} 项差异；"
                f"{len(material_issues)} 项关键差异需要基于原文完成裁决，"
                "且不会自动改变研究偏倚风险。"
                if unresolved_consistency
                else (
                    "关键差异已由具名执行者裁决。"
                    if material_issues
                    else ("差异已裁决或两次抽取一致。" if extraction else "尚无完整独立抽取。")
                )
            ),
        },
        {
            "id": "structured_results",
            "label": "Result、Claim 与原文定位",
            "status": "pass" if structured_results else "blocked",
            "detail": f"{len(claims)} 条候选均有结构化 Result 和原文定位。"
            if structured_results
            else "缺少可核对的结构化 Result、Claim 或原文定位。",
        },
        {
            "id": "executing_actor",
            "label": "AI 正式执行与具名追溯",
            "status": (
                "pass"
                if admission_status == "internally_admitted" and not pending_claims
                else ("blocked" if admission_status == "rejected" else "action")
            ),
            "detail": "论文准入和全部 Claim 已由具名执行者完成。"
            if admission_status == "internally_admitted" and not pending_claims
            else (
                "论文已被具名执行者拒绝，不能进入内部证据库。"
                if admission_status == "rejected"
                else (
                    f"AI 将继续审核 {len(pending_claims)} 条候选 Claim。"
                    if admission_status == "internally_admitted"
                    else "AI 将核验研究设计、版本关系和关键事实后准入。"
                )
            ),
        },
    ]
    blockers = [check["detail"] for check in checks if check["status"] == "blocked"]
    if blockers:
        state, next_action = "blocked", blockers[0]
    elif admission_status == "internally_admitted" and not pending_claims:
        state, next_action = "completed", "论文级证据审核已完成，继续形成 Evidence Profile。"
    else:
        state, next_action = (
            "ready_for_automation",
            "由 AI 自动执行剩余审核；人工只处理异常或抽查。",
        )
    topic_conditions = list(
        dict.fromkeys(
            str(item["topic_condition_code"])
            for item in collections
            if item.get("title_abstract_decision") != "excluded"
            and item.get("full_text_decision") != "excluded"
        )
    )
    study = studies[0] if len(studies) == 1 else {}
    return {
        "state": state,
        "next_action": next_action,
        "checks": checks,
        "blockers": blockers,
        "issues": issues,
        "admission_suggestion": {
            "condition_codes": topic_conditions,
            "study_design": str(
                (extraction or {}).get("study_design")
                or study.get("study_design")
                or paper.get("study_design_candidate")
                or "uncertain"
            ),
            "publication_role": str(study.get("publication_role") or "primary"),
            "consistency_resolution": _resolution_draft(issues),
        },
    }


def _source_based_consistency_resolution(admission: object) -> bool:
    if not isinstance(admission, dict):
        return False
    resolution = str(admission.get("consistency_resolution") or "").strip()
    return bool(resolution) and not resolution.startswith("AI consistency adjudication (")


def _critical_issue(
    issue: dict[str, object], claims: list[dict[str, object]] | None = None
) -> bool:
    severity = str(issue.get("severity") or "").casefold()
    if severity == "low":
        return False
    if severity == "high":
        return True
    field = str(issue.get("field") or "").casefold()
    message = str(issue.get("message") or "").casefold()
    value = f"{field} {message}"
    coverage_only = (
        "未包含",
        "额外包含",
        "覆盖范围",
        "结局覆盖",
        "声明粒度",
        "拆分",
        "合并",
        "未单独列出",
        "多出一条",
        "数量不一致",
        "背景性",
        "背景声明",
        "次要结局",
        "研究级 claim",
        "claim_type",
        "inference",
        "标记为",
        "分类",
        "格式不一致",
        "空格",
        "措辞不一致",
        "实质内容一致",
        "额外说明",
        "仅报告",
        "format",
        "spacing",
        "发表偏倚",
        "meta 回归",
        "not include",
        "not present in extraction",
        "coverage",
        "granularity",
    )
    if severity == "medium" and any(token in value for token in coverage_only):
        return False
    if claims is not None and "claim" in field:
        patient_claims = [
            claim
            for claim in claims
            if claim.get("candidate_claim_type") == "intervention_effect"
            and (claim.get("review_suggestion") or {}).get("decision") == "approved"
        ]
        indexed = re.search(r"claims\[(\d+)\]", field)
        if indexed:
            extraction_index = int(indexed.group(1)) + 1
            return any(
                claim.get("extraction_claim_index") == extraction_index for claim in patient_claims
            )
        return any(token in value for token in ("primary outcome", "primary_outcome", "主要结局"))
    tokens = (
        "claim",
        "study_design",
        "population",
        "ingredient",
        "dose",
        "comparator",
        "outcome",
        "effect",
        "statistical",
        "safety",
        "protocol",
        "registration",
        "样本",
        "剂量",
        "单位",
        "方向",
        "效应",
        "置信区间",
        "安全",
        "注册",
        "协议",
        "撤稿",
        "更正",
    )
    return any(token in value for token in tokens)


def _resolution_draft(issues: list[dict[str, object]]) -> str:
    lines = []
    for issue in issues:
        line = f"{issue.get('field', '差异')}：{issue.get('message', '')}"
        if issue.get("evidence"):
            line += f" 原文线索：{issue['evidence']}"
        lines.append(line)
    return "\n".join(lines)[:5000]


def _now() -> str:
    return datetime.now(UTC).isoformat()
