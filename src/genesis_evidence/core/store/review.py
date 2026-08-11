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
