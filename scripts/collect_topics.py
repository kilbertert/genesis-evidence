"""Create and lock catalog topics, then run one collection per topic.

Run this on the host that holds the database. It is the entry point the repo did
not have: topic creation and the collection run were only reachable from tests, so
the "start a search" step had no versioned, reviewable procedure.

    # what would it do, without touching anything
    genesis-evidence-collect --conditions COND_OVERWEIGHT_OBESITY

    # create + lock the topic, then run the search
    genesis-evidence-collect --conditions COND_OVERWEIGHT_OBESITY --apply

    # several topics, still one at a time
    genesis-evidence-collect --conditions COND_OVERWEIGHT_OBESITY COND_METABOLIC_SYNDROME --apply

A topic that already exists for a condition is reused — matched on its code and
reported — rather than created twice, so re-running after a failure does not
leave two ledgers for one question.

**--limit is deliberately small by default.** The standing decision is single-topic
low-rate acceptance: every paper admitted here becomes extraction calls and a
screening ledger to close, and a large batch was what forced the worker to be
stopped in August. Raise it only with that in mind.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis_evidence.core.store import Database, ObjectStore, PaperStore  # noqa: E402
from genesis_evidence.literature.connectors.europe_pmc import EuropePmcConnector  # noqa: E402
from genesis_evidence.literature.downloader import FullTextDownloader  # noqa: E402
from genesis_evidence.literature.http import HttpClient  # noqa: E402
from genesis_evidence.literature.ingestion import LiteratureIngestionService  # noqa: E402
from genesis_evidence.literature.integrity import (  # noqa: E402
    CrossrefIntegrityProvider,
    EuropePmcIntegrityProvider,
    PublicationIntegrityChecker,
)
from genesis_evidence.literature.policy import SourcePolicyRegistry  # noqa: E402
from genesis_evidence.literature.topic_plans import full_query, plans_for  # noqa: E402


def _build_service(store: PaperStore, objects: ObjectStore, http: HttpClient):
    policy = SourcePolicyRegistry()
    return LiteratureIngestionService(
        store=store,
        objects=objects,
        downloader=FullTextDownloader(
            http,
            policy,
            max_download_bytes=int(
                os.getenv("GENESIS_EVIDENCE_FULL_TEXT_MAX_BYTES", 25 * 1024 * 1024)
            ),
        ),
        integrity=PublicationIntegrityChecker(
            (EuropePmcIntegrityProvider(http), CrossrefIntegrityProvider(http))
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--conditions",
        nargs="+",
        required=True,
        help="condition codes to collect, e.g. COND_OVERWEIGHT_OBESITY",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="records to read per topic (low by default; see the module docstring)",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="override every plan's search query (mostly for a targeted re-run)",
    )
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    parser.add_argument(
        "--database",
        default=os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"),
    )
    parser.add_argument("--objects", default=os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects"))
    args = parser.parse_args()

    plans = plans_for(args.conditions)

    print(f"conditions requested : {len(args.conditions)}")
    print(f"plans found          : {len(plans)}")
    print(f"per-topic record cap : {args.limit}")
    print(f"mode                 : {'APPLY' if args.apply else 'DRY RUN'}")
    print()
    cutoff = os.getenv("GENESIS_EVIDENCE_EVIDENCE_CUTOFF", "2026-10-09")
    for plan in plans:
        query = args.query or full_query(plan, cutoff=cutoff)
        print(f"  {plan.condition_code}")
        print(f"    topic code  : {plan.code}")
        print(f"    question    : {plan.review_question}")
        print(f"    query       : {query}")
    print()

    if not args.apply:
        print("dry run: nothing written. Re-run with --apply to create and collect.")
        return

    reviewer = os.getenv("GENESIS_EVIDENCE_REVIEWER_ID", "").strip()
    if not reviewer:
        raise SystemExit(
            "GENESIS_EVIDENCE_REVIEWER_ID is required: topic creation and locking are audited"
        )

    database = Database(Path(args.database))
    database.initialize()
    store = PaperStore(database)
    objects = ObjectStore(args.objects)

    existing = {str(topic["code"]): topic for topic in store.list_topics()}
    with HttpClient(user_agent="GenesisEvidence/0.1 topic-collect") as http:
        service = _build_service(store, objects, http)
        connector = EuropePmcConnector(http, SourcePolicyRegistry())
        for plan in plans:
            topic = existing.get(plan.code)
            if topic is None:
                topic_id = store.create_topic(
                    code=plan.code,
                    version="1",
                    condition_code=plan.condition_code,
                    review_question=plan.review_question,
                    picots=plan.picots,
                    eligible_study_designs=plan.eligible_study_designs,
                    inclusion_criteria=plan.inclusion_criteria,
                    exclusion_reasons=plan.exclusion_reasons,
                    required_search_streams=plan.required_search_streams,
                    evidence_cutoff_date=os.getenv(
                        "GENESIS_EVIDENCE_EVIDENCE_CUTOFF", "2026-10-09"
                    ),
                    reviewer=reviewer,
                )
                store.lock_topic(topic_id, reviewer=reviewer)
                print(f"{plan.code}: created and locked topic {topic_id}")
            else:
                topic_id = str(topic["id"])
                print(f"{plan.code}: topic already exists ({topic_id}); not creating another")
            summary = service.collect(
                topic_id=topic_id,
                connector=connector,
                query=args.query or full_query(plan, cutoff=cutoff),
                limit=args.limit,
                search_stream=plan.required_search_streams[0],
            )
            print(
                f"{plan.code}: run {summary.run_id} — discovered {summary.discovered}, "
                f"full texts {summary.downloaded_full_texts}, "
                f"queued extractions {summary.queued_extractions}, "
                f"skipped {summary.skipped_full_texts}, failed {summary.failed_full_texts}"
            )


if __name__ == "__main__":
    main()
