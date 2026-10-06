"""Rebuild the published evidence bodies under the component axis (ADR 0007 D).

Dry run by default, and the dry run is genuinely read-only: it opens a throwaway
copy rather than the database it was pointed at. That matters because
``Database.initialize()`` is not read-only — it runs the retirement and legacy-split
migrations — so "just opening the database to preview" would already have
retired published cards. Working on a copy is also what lets the preview report
the forecast instead of a bare count.

The rebuild itself does **not** re-search, re-fetch or re-extract: it re-derives
profiles and cards from the results the database already holds, because the
evidence gate forbids adding evidence or relaxing a threshold to make a build
succeed.

    scripts/rebuild_evidence_bodies.py --db var/genesis-evidence.sqlite3
    scripts/rebuild_evidence_bodies.py --db ... --apply --backup /var/backups/...

What the run has to tell the operator, and therefore prints:

- which published cards the rebuild retires;
- the scopes a patient can still be served from, the scopes that went dark, and
  the scopes now carrying several components. The last two are not failures: a
  card that pooled ten different interventions is *supposed* to lose its body,
  and a scope with several components has no single answer to give. The point is
  that the operator reads the list rather than discovering it from patient
  reports;
- per paper, whether the rebuild completed, produced some cards, or produced
  none — with the reason. "Produced none" is usually a topic with no matching
  PICOTS scope or an unclosed screening ledger, which is a state rather than a
  defect. Only a raised exception is a failure and sets a non-zero exit.

The write path refuses to run without a backup, because the only way to undo this
is to restore one. Stop the service before taking that backup: a SQLite database
in WAL mode can be copied mid-transaction, and a backup taken while the service
runs may hold a transaction that only lives in the `-wal` file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

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

    # A dry run must not touch the database it was pointed at. `initialize()` is
    # not read-only — it runs `retire_ungoverned_profile_cards` and
    # `_migrate_legacy_shared_results`, so merely opening a database can already
    # retire published cards. A preview that writes is worse than no preview, so
    # the preview runs the whole rebuild against a throwaway copy instead, which
    # is also what lets it report the retirement forecast rather than a count.
    with TemporaryDirectory() as scratch:
        target = args.db
        if not args.apply:
            target = Path(scratch) / args.db.name
            # The sidecars carry committed transactions when the database is in
            # WAL mode; copying the main file alone would preview a stale state.
            for suffix in ("", "-wal", "-shm"):
                source = Path(f"{args.db}{suffix}")
                if source.is_file():
                    shutil.copy(source, f"{target}{suffix}")
        report, failures = _run(target, objects_root, args.reviewer, write=args.apply)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.apply:
        print(
            "\nDRY RUN on a copy — nothing was written to "
            f"{args.db}. Rerun with --apply --backup <path> to write.",
            file=sys.stderr,
        )
    return 1 if failures else 0

def _run(db_path: Path, objects_root: Path, reviewer: str, *, write: bool):
    database = Database(db_path)
    # The additive migration, including the component_token columns.
    database.initialize()
    service = EvidenceReviewService(
        ReviewStore(database), PaperStore(database), ObjectStore(objects_root)
    )

    with database.connect() as connection:
        before = _published_by_scope(connection)
        before_status = _statuses(connection)
        papers = _admitted_papers(connection)

    # A raised exception is a failure. A paper that ends without a published card
    # is not: its topic may legitimately have no matching PICOTS scope, or its
    # screening ledger may still be open, and calling those failures would send an
    # operator hunting for a defect that is a state. They are reported, not failed.
    failures: list[tuple[str, str]] = []
    unbuilt: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    for paper_id in papers:
        try:
            outcome = service.auto_review_paper(paper_id, requested_by=reviewer)
        except Exception as exc:  # noqa: BLE001 — one paper must not stop the run
            failures.append((paper_id, str(exc)))
            continue
        kind, why = _outcome_kind(outcome)
        if kind == "none":
            unbuilt.append((paper_id, why))
        elif kind == "partial":
            partial.append((paper_id, why))

    with database.connect() as connection:
        after = _published_by_scope(connection)
        after_status = _statuses(connection)

    retired = [
        card_id
        for card_id, status in before_status.items()
        if status == "published" and after_status.get(card_id) == "stale"
    ]
    # A scope reaches a patient iff it carries exactly one published card, which
    # is what the readers require. So a scope can lose service in two ways: it
    # ends with no card, or it ends with several and the reader refuses to pick.
    # Grouping the second case under "serving" would overstate the result.
    servable = sorted(key for key, cards in after.items() if len(cards) == 1)
    ambiguous = sorted(key for key, cards in after.items() if len(cards) > 1)
    dark = sorted(key for key in before if key not in after)

    report = {
        "applied": write,
        "papers_reviewed": len(papers),
        "failures": failures,
        "rebuilt_completely": len(papers) - len(failures) - len(unbuilt) - len(partial),
        "rebuilt_partially": partial,
        "did_not_rebuild": unbuilt,
        "published_before": sum(len(v) for v in before.values()),
        "published_after": len(
            [card_id for card_id, status in after_status.items() if status == "published"]
        ),
        "retired_cards": retired,
        "scopes_serving_one_card": [list(key) for key in servable],
        "scopes_serving_before_no_card_after": [list(key) for key in dark],
        "scopes_with_several_components": [list(key) for key in ambiguous],
    }
    return report, failures


def _outcome_kind(outcome: object) -> tuple[str, str]:
    """Classify one paper's rebuild as complete, partial, or none.

    "Not every card published" is not the same as "the rebuild failed". A paper
    covering several topics legitimately has topics with no matching PICOTS
    scope, and it ends with some cards published and others not. Calling that a
    failure would be as wrong as calling it a success — which is the mistake this
    replaced. Only a paper that ends with **no** published card did not rebuild.
    """

    if not isinstance(outcome, dict):
        return "none", "automatic review returned no result"
    if outcome.get("status") == "attention_required":
        return "none", f"attention_required: {outcome.get('reason', '')}"
    cards = outcome.get("cards")
    if not isinstance(cards, list) or not cards:
        return "none", "automatic review produced no card at all"
    statuses = [str(card.get("status")) for card in cards if isinstance(card, dict)]
    published = sum(1 for status in statuses if status == "published")
    if published == len(statuses):
        return "complete", ""
    if published:
        return "partial", f"{published}/{len(statuses)} cards published: {sorted(set(statuses))}"
    return "none", f"no card published: {sorted(set(statuses))}"


if __name__ == "__main__":
    raise SystemExit(main())
