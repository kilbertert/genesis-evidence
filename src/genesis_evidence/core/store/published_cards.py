"""One owner of the row→patient-card projection (#221).

Both patient-response readers — `EvidenceStore._published_cards` and
`ReportStore.match_published_cards` — turn the same joined rows into the same card
dict, and did so as a byte-identical pair. They differ only in the query that
produces the rows, so the projection lives here and each reader supplies rows.

ADR 0007 lets one scope carry one card per component. Those are several answers,
not several versions of one answer, so the projection keeps them apart and serves
all of them; the patient-facing shape already carries a list of items, each with
its own card body and its own citations, so no card's conclusion is ever shown
with another card's sources.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..contracts import card_capabilities


def project_published_cards(
    rows: Iterable[Mapping[str, object]],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    """Group joined card rows into patient-visible cards keyed by (condition, scope).

    The value is a list because ADR 0007 lets one scope carry one card per
    component: several independently reviewed answers to the same observation.
    They are returned newest-first and all of them are served — choosing one
    silently is what the earlier fail-closed rule avoided, and it avoided it by
    serving nothing.

    Rows must carry: `id`, `condition_code`, `scope_key`, `version`, `grade`,
    `published_at`, `evidence_profile_id`, `patient_visible_body`, and the source
    columns `claim_id`, `paper_id`, `paper_title`, `doi`, `card_evidence`,
    `locator`, `candidate_text` (the last two may be null on a card with no claim).
    """

    by_card: dict[str, dict[str, object]] = {}
    by_scope: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        scope_key = str(row["scope_key"] or "").strip()
        # Legacy cards without an explicit outcome scope are deliberately not
        # eligible for external metric matching.
        if not scope_key:
            continue
        card_id = str(row["id"])
        card = by_card.get(card_id)
        if card is None:
            card = {
                "id": row["id"],
                "condition_code": row["condition_code"],
                "scope_key": scope_key,
                "version": row["version"],
                "status": "published",
                "grade": row["grade"],
                "published_at": row["published_at"],
                "evidence_profile_id": row["evidence_profile_id"],
                "patient_visible_body": row["patient_visible_body"],
                "sources": [],
                **card_capabilities(str(row["grade"])),
            }
            by_card[card_id] = card
        by_scope.setdefault((str(row["condition_code"]), scope_key), set()).add(card_id)
        if row["claim_id"]:
            source = {
                "claim_id": row["claim_id"],
                "paper_id": row["paper_id"],
                "paper_title": row["paper_title"],
                "doi": row["doi"],
                "evidence": row["card_evidence"] or row["candidate_text"] or "",
                "locator": row["locator"] or "",
            }
            sources = card["sources"]
            if isinstance(sources, list) and source not in sources:
                sources.append(source)
    return {
        key: sorted(
            (by_card[card_id] for card_id in card_ids),
            key=lambda card: (str(card["published_at"]), str(card["version"])),
            reverse=True,
        )
        for key, card_ids in by_scope.items()
    }
