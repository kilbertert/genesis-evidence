"""Atomic persistence operations for one-reviewer evidence publication."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

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
    ) -> None:
        with self.database.transaction() as connection:
            paper = connection.execute(
                "SELECT integrity_status FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                raise ValueError("paper not found")
            if paper["integrity_status"] != "clear":
                raise ValueError("paper integrity must be clear before internal admission")
            extraction = connection.execute(
                """
                SELECT id, consistency_status FROM paper_extractions
                WHERE paper_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            if extraction is None:
                raise ValueError("paper has no AI extraction")
            if extraction["consistency_status"] == "needs_review" and not consistency_resolution:
                raise ValueError("AI extraction differences require a human resolution")
            studies = connection.execute(
                """
                SELECT s.id FROM studies s JOIN study_publications sp ON sp.study_id = s.id
                WHERE sp.paper_id = ?
                """,
                (paper_id,),
            ).fetchall()
            if not studies:
                raise ValueError("paper has no Study/Publication relationship")
            result_count = connection.execute(
                "SELECT count(*) FROM results WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            if result_count == 0:
                raise ValueError("paper has no structured Result records")
            known = {
                row[0]
                for row in connection.execute(
                    f"SELECT code FROM conditions WHERE code IN ({_placeholders(condition_codes)})",
                    condition_codes,
                ).fetchall()
            }
            if known != set(condition_codes):
                raise ValueError("paper contains an unknown condition code")
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
                UPDATE studies SET status = 'verified', reviewer = ?, reviewed_at = ?
                WHERE id IN (SELECT study_id FROM study_publications WHERE paper_id = ?)
                """,
                (reviewer, reviewed_at, paper_id),
            )
            connection.execute(
                """
                UPDATE study_publications SET reviewer = ?, reviewed_at = ? WHERE paper_id = ?
                """,
                (reviewer, reviewed_at, paper_id),
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
                },
            )

    def reject_paper(self, paper_id: str, *, reviewer: str) -> None:
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE paper_admissions SET status = 'rejected', condition_codes_json = '[]',
                    reviewer = ?, reviewed_at = ? WHERE paper_id = ?
                """,
                (reviewer, _now(), paper_id),
            ).rowcount
            if updated != 1:
                raise ValueError("paper admission item not found")
            self._stale_cards_for_paper(connection, paper_id)
            self._audit(connection, "paper", paper_id, "admission_rejected", reviewer, {})

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
    ) -> None:
        with self.database.transaction() as connection:
            claim = connection.execute(
                """
                SELECT c.paper_id, c.result_id, p.integrity_status,
                    pa.status AS admission_status,
                    pa.condition_codes_json
                FROM claims c JOIN papers p ON p.id = c.paper_id
                LEFT JOIN paper_admissions pa ON pa.paper_id = c.paper_id
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
            connection.execute(
                """
                UPDATE knowledge_cards SET status = 'stale'
                WHERE status = 'published' AND id IN (
                    SELECT card_id FROM card_claims WHERE claim_id = ?
                )
                """,
                (claim_id,),
            )
            self._audit(
                connection,
                "claim",
                claim_id,
                f"claim_{decision}",
                reviewer,
                {
                    "condition_code": condition_code,
                    "risk_of_bias": risk_of_bias,
                    "applicability": applicability,
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
            rows = connection.execute(
                f"""
                SELECT c.id, c.paper_id, c.result_id, c.evidence_text, c.locator,
                    c.candidate_claim_type,
                    cr.decision, cr.condition_code, cr.corrected_study_design,
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
                row["candidate_claim_type"] == "mechanism"
                or row["corrected_study_design"]
                in {"animal_study", "in_vitro_study", "case_series", "case_report"}
                for row in rows
            ):
                raise ValueError(
                    "mechanism and case-report results cannot support a patient-visible card"
                )
            dimensions = (
                "population",
                "baseline_nutrient_status",
                "ingredient_name",
                "ingredient_form",
                "dose",
                "comparator",
                "outcome",
                "timepoint",
            )
            if any(len({str(row[field]) for row in rows}) != 1 for field in dimensions):
                raise ValueError("one evidence profile cannot mix different PICOTS result scopes")
            first = rows[0]
            eligible = connection.execute(
                """
                SELECT c.id, c.status, cr.decision FROM claims c
                LEFT JOIN claim_reviews cr ON cr.claim_id = c.id
                JOIN results r ON r.id = c.result_id
                JOIN papers p ON p.id = c.paper_id
                JOIN paper_admissions pa ON pa.paper_id = p.id
                WHERE p.integrity_status = 'clear'
                    AND p.publication_status = 'formal'
                    AND pa.status = 'internally_admitted'
                    AND c.candidate_claim_type <> 'mechanism'
                    AND COALESCE(cr.corrected_study_design, c.candidate_study_design) NOT IN (
                        'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                    )
                    AND EXISTS (
                        SELECT 1 FROM json_each(pa.condition_codes_json) WHERE value = ?
                    )
                    AND EXISTS (
                        SELECT 1 FROM collection_papers cp
                        JOIN collection_runs cr ON cr.id = cp.run_id
                        WHERE cr.topic_id = ? AND cp.paper_id = p.id
                            AND cp.full_text_decision = 'included'
                    )
                    AND r.population = ? AND r.baseline_nutrient_status = ?
                    AND r.ingredient_name = ? AND r.ingredient_form = ?
                    AND r.dose = ? AND r.comparator = ? AND r.outcome = ?
                    AND r.timepoint = ?
                """,
                (condition_code, topic_id, *(first[field] for field in dimensions)),
            ).fetchall()
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
            connection.execute(
                """
                INSERT INTO evidence_profiles(
                    id, topic_id, condition_code, version, ingredient_name, ingredient_form,
                    population, baseline_nutrient_status, dose, comparator, outcome,
                    timepoint, estimate_target, certainty, certainty_rationale,
                    evidence_body_complete, evidence_cutoff_date, reviewer, reviewed_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    topic_id,
                    condition_code,
                    version,
                    first["ingredient_name"],
                    first["ingredient_form"],
                    first["population"],
                    first["baseline_nutrient_status"],
                    first["dose"],
                    first["comparator"],
                    first["outcome"],
                    first["timepoint"],
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
            connection.executemany(
                """
                INSERT INTO evidence_profile_results(profile_id, result_id, interpretation)
                VALUES (?, ?, ?)
                """,
                [(profile_id, row["result_id"], interpretations[row["id"]]) for row in rows],
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
                "SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)
            ).fetchone()
            if card is None:
                raise ValueError("knowledge card not found")
            if target not in allowed.get(str(card["status"]), set()):
                raise ValueError(f"invalid card transition: {card['status']} -> {target}")
            if target == "published":
                self._require_publishable(connection, card_id)
                if card["grade"] not in {"high", "moderate"}:
                    raise ValueError(
                        "patient-visible benefit cards require high or moderate certainty"
                    )
                connection.execute(
                    """
                    UPDATE knowledge_cards SET status = 'stale'
                    WHERE condition_code = ? AND status = 'published' AND id <> ?
                    """,
                    (card["condition_code"], card_id),
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
                {},
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
                    COALESCE(pa.status, 'pending') AS admission_status,
                    pe.consistency_status,
                    pej.status AS extraction_job_status,
                    pej.stage AS extraction_job_stage,
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
                GROUP BY p.id, pa.status, pe.consistency_status, pej.status, pej.stage
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
                    extraction_run_id, second_run_id, check_run_id, updated_at, completed_at
                FROM paper_extraction_jobs WHERE paper_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id,),
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
                    et.exclusion_reasons_json, cr.id AS run_id, cr.source, cr.search_stream,
                    cr.status AS run_status, cp.title_abstract_decision,
                    cp.full_text_decision, cp.primary_exclusion_reason
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN evidence_topics et ON et.id = cr.topic_id
                WHERE cp.paper_id = ? ORDER BY cr.created_at, cr.id
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
                    r.effect_estimate, r.statistical_details
                FROM claims c LEFT JOIN claim_reviews cr ON cr.claim_id = c.id
                LEFT JOIN results r ON r.id = c.result_id
                WHERE c.paper_id = ? ORDER BY c.created_at, c.id
                """,
                (paper_id,),
            ).fetchall()
        return {
            "paper": dict(paper),
            "extraction": json.loads(extraction["extraction_json"]) if extraction else None,
            "second_extraction": (
                json.loads(extraction["second_extraction_json"]) if extraction else None
            ),
            "consistency": json.loads(extraction["consistency_json"]) if extraction else None,
            "extraction_job": dict(extraction_job) if extraction_job else None,
            "admission": (
                {
                    **dict(admission),
                    "condition_codes": json.loads(admission["condition_codes_json"]),
                }
                if admission
                else None
            ),
            "sources": [dict(row) for row in sources],
            "collections": [
                {
                    **{
                        key: value
                        for key, value in dict(row).items()
                        if key != "exclusion_reasons_json"
                    },
                    "exclusion_reasons": json.loads(row["exclusion_reasons_json"]),
                }
                for row in collections
            ],
            "claims": [_claim_dict(row) for row in claims],
        }

    def list_cards(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT kc.id, kc.condition_code, kc.version, kc.status, kc.grade,
                    kc.evidence_profile_id,
                    kc.reviewer, kc.reviewed_at, kc.published_at, kc.patient_visible_body,
                    count(cc.claim_id) AS claim_count,
                    group_concat(cc.claim_id) AS claim_ids,
                    group_concat(DISTINCT c.paper_id) AS paper_ids
                FROM knowledge_cards kc
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

    @staticmethod
    def _require_publishable(connection, card_id: str) -> None:
        card = connection.execute(
            """
            SELECT kc.condition_code, kc.evidence_profile_id, ep.topic_id
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
                    OR c.candidate_claim_type = 'mechanism'
                    OR cr.corrected_study_design IN (
                        'animal_study', 'in_vitro_study', 'case_series', 'case_report'
                    )
                    OR cr.condition_code <> ? THEN 1 ELSE 0 END) AS invalid
            FROM card_claims cc
            JOIN claims c ON c.id = cc.claim_id
            JOIN claim_reviews cr ON cr.claim_id = c.id
            JOIN papers p ON p.id = c.paper_id
            JOIN paper_admissions pa ON pa.paper_id = p.id
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
        if evidence["total"] == 0 or evidence["invalid"] or missing_profile_results:
            raise ValueError("knowledge card has ineligible evidence")

    @staticmethod
    def _stale_cards_for_paper(connection, paper_id: str) -> None:
        connection.execute(
            """
            UPDATE knowledge_cards SET status = 'stale'
            WHERE status = 'published' AND id IN (
                SELECT cc.card_id FROM card_claims cc
                JOIN claims c ON c.id = cc.claim_id WHERE c.paper_id = ?
            )
            """,
            (paper_id,),
        )

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
            OR (cp.title_abstract_decision = 'included' AND cp.full_text_decision IS NULL)
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
    missing_included_claim = connection.execute(
        """
        SELECT 1 FROM collection_papers cp JOIN collection_runs cr ON cr.id = cp.run_id
        WHERE cr.topic_id = ? AND cr.status = 'completed'
            AND cp.full_text_decision = 'included' AND NOT EXISTS (
                SELECT 1 FROM claims c JOIN claim_reviews review ON review.claim_id = c.id
                WHERE c.paper_id = cp.paper_id AND review.decision = 'approved'
                    AND review.condition_code = ?
            ) LIMIT 1
        """,
        (topic_id, condition_code),
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
    if incomplete_included_paper or missing_included_claim:
        raise ValueError("every full-text included paper must be extracted, admitted, and reviewed")
    return topic


def _claim_dict(row) -> dict[str, object]:
    claim = dict(row)
    raw = claim.pop("risk_of_bias_json", None)
    claim["risk_of_bias"] = json.loads(raw) if raw else None
    return claim


def _now() -> str:
    return datetime.now(UTC).isoformat()
