"""Atomic persistence operations for one-reviewer evidence publication."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from ..patient_copy import validate_patient_copy
from .database import Database

GRADE_ORDER = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}


class ReviewStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def admit_paper(
        self, paper_id: str, *, reviewer: str, condition_codes: tuple[str, ...]
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
                "SELECT id FROM paper_extractions WHERE paper_id = ? LIMIT 1", (paper_id,)
            ).fetchone()
            if extraction is None:
                raise ValueError("paper has no AI extraction")
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
                    paper_id, status, condition_codes_json, reviewer, reviewed_at
                ) VALUES (?, 'internally_admitted', ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    status = excluded.status,
                    condition_codes_json = excluded.condition_codes_json,
                    reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at
                """,
                (paper_id, json.dumps(condition_codes), reviewer, _now()),
            )
            self._audit(
                connection,
                "paper",
                paper_id,
                "internally_admitted",
                reviewer,
                {"condition_codes": condition_codes},
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
        grade: str | None,
        condition_code: str | None,
    ) -> None:
        with self.database.transaction() as connection:
            claim = connection.execute(
                """
                SELECT c.paper_id, p.integrity_status, pa.status AS admission_status,
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
            if decision == "approved" and condition_code not in json.loads(
                claim["condition_codes_json"]
            ):
                raise ValueError("claim condition was not selected during paper admission")
            connection.execute(
                """
                INSERT INTO claim_reviews(
                    claim_id, decision, corrected_text, corrected_study_design,
                    inference, grade, condition_code, reviewer, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    decision = excluded.decision, corrected_text = excluded.corrected_text,
                    corrected_study_design = excluded.corrected_study_design,
                    inference = excluded.inference, grade = excluded.grade,
                    condition_code = excluded.condition_code, reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at
                """,
                (
                    claim_id,
                    decision,
                    corrected_text,
                    corrected_study_design,
                    inference,
                    grade,
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
                {"condition_code": condition_code, "grade": grade},
            )

    def create_card(
        self,
        *,
        condition_code: str,
        version: str,
        claim_ids: tuple[str, ...],
        reviewer: str,
        patient_body: str,
    ) -> str:
        card_id = str(uuid.uuid4())
        patient_body = validate_patient_copy(patient_body)
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT c.id, c.paper_id, c.evidence_text, c.locator,
                    cr.decision, cr.condition_code, cr.grade,
                    p.integrity_status, pa.status AS admission_status
                FROM claims c JOIN claim_reviews cr ON cr.claim_id = c.id
                JOIN papers p ON p.id = c.paper_id
                JOIN paper_admissions pa ON pa.paper_id = c.paper_id
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
            grade = max((str(row["grade"]) for row in rows), key=GRADE_ORDER.__getitem__)
            now = _now()
            connection.execute(
                """
                INSERT INTO knowledge_cards(
                    id, condition_code, version, status, grade, reviewer,
                    reviewed_at, patient_visible_body, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (card_id, condition_code, version, grade, reviewer, now, patient_body, now),
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
                {"condition_code": condition_code, "version": version, "claims": claim_ids},
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
                SELECT id, condition_code, version, grade, patient_visible_body, published_at
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
                    count(c.id) AS claim_count,
                    sum(CASE WHEN c.status = 'candidate' THEN 1 ELSE 0 END) AS pending_claims
                FROM papers p
                LEFT JOIN paper_admissions pa ON pa.paper_id = p.id
                LEFT JOIN paper_extractions pe ON pe.id = (
                    SELECT id FROM paper_extractions
                    WHERE paper_id = p.id ORDER BY created_at DESC, id DESC LIMIT 1
                )
                LEFT JOIN claims c ON c.paper_id = p.id
                GROUP BY p.id, pa.status, pe.consistency_status
                ORDER BY p.created_at DESC, p.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_review_item(self, paper_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            paper = connection.execute(
                "SELECT * FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                return None
            extraction = connection.execute(
                """
                SELECT * FROM paper_extractions WHERE paper_id = ?
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
            claims = connection.execute(
                """
                SELECT c.*, cr.decision, cr.corrected_text, cr.corrected_study_design,
                    cr.inference, cr.grade, cr.condition_code, cr.reviewer, cr.reviewed_at
                FROM claims c LEFT JOIN claim_reviews cr ON cr.claim_id = c.id
                WHERE c.paper_id = ? ORDER BY c.created_at, c.id
                """,
                (paper_id,),
            ).fetchall()
        return {
            "paper": dict(paper),
            "extraction": json.loads(extraction["extraction_json"]) if extraction else None,
            "consistency": json.loads(extraction["consistency_json"]) if extraction else None,
            "admission": (
                {
                    **dict(admission),
                    "condition_codes": json.loads(admission["condition_codes_json"]),
                }
                if admission
                else None
            ),
            "sources": [dict(row) for row in sources],
            "claims": [dict(row) for row in claims],
        }

    def list_cards(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT kc.id, kc.condition_code, kc.version, kc.status, kc.grade,
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
            "SELECT condition_code FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        evidence = connection.execute(
            """
            SELECT count(*) AS total,
                sum(CASE WHEN cr.decision <> 'approved'
                    OR p.integrity_status <> 'clear'
                    OR pa.status <> 'internally_admitted'
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
        if evidence["total"] == 0 or evidence["invalid"]:
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


def _now() -> str:
    return datetime.now(UTC).isoformat()
