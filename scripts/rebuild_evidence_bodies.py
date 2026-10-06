"""Rebuild the published evidence bodies under the component axis (ADR 0007 D).

Dry run by default; nothing is written unless ``--apply`` is given. The rebuild
itself does **not** re-search, re-fetch or re-extract: it re-derives profiles and
cards from the results the database already holds, because the evidence gate
forbids adding evidence or relaxing a threshold to make a build succeed.

    scripts/rebuild_evidence_bodies.py --db var/genesis-evidence.sqlite3
    scripts/rebuild_evidence_bodies.py --db ... --apply --backup /var/backups/...

What the run has to tell the operator, and therefore prints:

- which published cards the rebuild would retire, and why each left the pool;
- which scopes had a published card before and have none after — these are not
  failures. A card that pooled ten different interventions is *supposed* to lose
  its body; the point is that the operator sees the list rather than discovering
  it from patient reports.

The write path refuses to run without a backup, because the only way to undo this
is to restore one. Stop the service before taking that backup: a SQLite database
in WAL mode can be copied mid-transaction, and a backup taken while the service
runs may hold a transaction that only lives in the `-wal` file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genesis_evidence.core.store import (  # noqa: E402
    Database,
    ObjectStore,
    PaperStore,
    ReviewStore,
)
from genesis_evidence.review.service import EvidenceReviewService  # noqa: E402


def _published_by_scope(connection) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Published cards keyed by (condition_code, scope_key) as (card_id, token)."""

    rows = connection.execute(
        """
        SELECT kc.id, kc.condition_code, ep.scope_key, ep.component_token
        FROM knowledge_cards kc
        JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
        WHERE kc.status = 'published' AND trim(ep.scope_key) <> ''
        """
    ).fetchall()
    scopes: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in rows:
        key = (str(row["condition_code"]), str(row["scope_key"]))
        scopes.setdefault(key, []).append((str(row["id"]), str(row["component_token"])))
    return scopes


def _statuses(connection) -> dict[str, str]:
    return {
        str(row["id"]): str(row["status"])
        for row in connection.execute("SELECT id, status FROM knowledge_cards").fetchall()
    }


def _admitted_papers(connection) -> list[str]:
    return [
        str(row["paper_id"])
        for row in connection.execute(
            "SELECT paper_id FROM paper_admissions WHERE status = 'internally_admitted'"
        ).fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--objects", type=Path, help="object store root; default <db dir>/objects")
    parser.add_argument("--apply", action="store_true", help="write; absent means dry run")
    parser.add_argument("--backup", type=Path, help="backup path; required with --apply")
    parser.add_argument("--reviewer", default="system:component-rebuild")
    parser.add_argument("--json-out", type=Path, help="also write this report as JSON")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"refused: database not found: {args.db}", file=sys.stderr)
        return 2
    if args.apply and not args.backup:
        print("refused: --apply requires --backup (the only way to undo this)", file=sys.stderr)
        return 2
    if args.apply and not args.backup.is_file():
        print(
            f"refused: backup not found: {args.backup}\n"
            "take it with the service stopped, then rerun",
            file=sys.stderr,
        )
        return 2

    objects_root = args.objects or args.db.parent / "objects"
    database = Database(args.db)
    # Runs the additive migration, including the component_token columns.
    database.initialize()
    store = ReviewStore(database)
    service = EvidenceReviewService(store, PaperStore(database), ObjectStore(objects_root))

    with database.connect() as connection:
        before = _published_by_scope(connection)
        before_status = _statuses(connection)
        papers = _admitted_papers(connection)

    if not args.apply:
        print(f"DRY RUN — {len(papers)} admitted papers, {len(before)} published scopes")
        print("rerun with --apply --backup <path> to write")
        return 0

    failures: list[tuple[str, str]] = []
    for paper_id in papers:
        try:
            service.auto_review_paper(paper_id, requested_by=args.reviewer)
        except Exception as exc:  # noqa: BLE001 — one paper must not stop the run
            failures.append((paper_id, str(exc)))

    with database.connect() as connection:
        after = _published_by_scope(connection)
        after_status = _statuses(connection)

    retired = [
        card_id
        for card_id, status in before_status.items()
        if status == "published" and after_status.get(card_id) == "stale"
    ]
    published = sorted(
        card_id for card_id, status in after_status.items() if status == "published"
    )
    # A scope reaches a patient iff it carries exactly one published card, which
    # is what the readers require. So a scope can lose service in two ways: it
    # ends with no card, or it ends with several and the reader refuses to pick.
    # Grouping the second case under "serving" would overstate the result.
    servable = sorted(key for key, cards in after.items() if len(cards) == 1)
    ambiguous = sorted(key for key, cards in after.items() if len(cards) > 1)
    dark = sorted(key for key in before if key not in after)

    report = {
        "papers_reviewed": len(papers),
        "failures": failures,
        "published_before": sum(len(v) for v in before.values()),
        "published_after": len(published),
        "retired_cards": retired,
        "scopes_serving_one_card": [list(key) for key in servable],
        "scopes_serving_before_no_card_after": [list(key) for key in dark],
        "scopes_with_several_components": [list(key) for key in ambiguous],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if dark:
        print(
            f"\n{len(dark)} scope(s) had a published card and now have none. "
            "This is expected where the old card pooled several interventions under "
            "one conclusion; the list above is what a human reviews.",
            file=sys.stderr,
        )
    if ambiguous:
        print(
            f"\n{len(ambiguous)} scope(s) now carry cards for several components and "
            "therefore serve nothing: how a patient should be shown more than one "
            "answer is undecided. These are not failures — the scopes that used to "
            "pool several interventions under one conclusion now have one body per "
            "intervention, and the presentation question is a product decision.",
            file=sys.stderr,
        )
    if failures:
        print(f"\n{len(failures)} paper(s) failed; rerun after diagnosing", file=sys.stderr)

    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
