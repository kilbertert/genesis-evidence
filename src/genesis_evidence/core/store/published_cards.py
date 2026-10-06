"""One owner of the row→patient-card projection (#221).

Both patient-response readers — `EvidenceStore._published_cards` and
`ReportStore.match_published_cards` — turn the same joined rows into the same card
dict, and did so as a byte-identical pair. They differ only in the query that
produces the rows, so the projection lives here and each reader supplies rows.

The fail-closed rule travels with it: a `(condition_code, scope_key)` is indexed
only when exactly one card claims it. ADR 0007 lets one scope carry one card per
component, and how several should be shown is a product decision, so an ambiguous
scope serves nothing rather than one component's conclusion with another's
citations attached.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..contracts import card_capabilities


def project_published_cards(
    rows: Iterable[Mapping[str, object]],
) -> dict[tuple[str, str], dict[str, object]]:
    """Group joined card rows into patient-visible cards keyed by (condition, scope).

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
        key: by_card[next(iter(card_ids))]
        for key, card_ids in by_scope.items()
        if len(card_ids) == 1
    }
