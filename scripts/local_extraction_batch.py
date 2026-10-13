"""Run a small extraction batch locally, end to end, against a throwaway database.

This is the local acceptance harness for the paper pipeline's most expensive
stage. It exists because the extraction worker has never been deployed to the
service host and its three stages (extraction A, extraction B, consistency) were
last exercised in August — the checks that matter here are the ones a unit test
cannot make: does a real model, over a real full text, produce an extraction the
code accepts, a second one it can compare, and a consistency verdict — and what
does one paper cost in tokens and wall time.

    # what would run, and the projected cost, without touching anything
    scripts/local_extraction_batch.py --model deepseek/deepseek-v4.1-flash

    # run N papers on a copy of the corpus
    scripts/local_extraction_batch.py --model ... --limit 3 --apply

**It never points at the working database.** The corpus is copied to a temp
directory first, because `Database.initialize()` writes (the retirement and
legacy-split migrations), so "open it read-only to preview" would already have
changed it. The extraction chain also enqueues jobs and writes claims — the copy
is what makes a batch run, a failure, or an interrupted run safe to do.

The API key and endpoint come from the environment, never from a flag, so a key
never lands in a shell history or a process listing. `--model` overrides the
model only; the endpoint and key are read from `PAPER_AI_*`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis_evidence.core.store import Database, ObjectStore, PaperStore  # noqa: E402
from genesis_evidence.core.store.review import ReviewStore  # noqa: E402
from genesis_evidence.literature.ai_extraction import (  # noqa: E402
    ArkPaperAnalyzer,
    PaperAnalysisError,
)
from genesis_evidence.literature.extraction_worker import LiteratureExtractionWorker  # noqa: E402
from genesis_evidence.review.service import EvidenceReviewService  # noqa: E402


@dataclass
class PaperOutcome:
    paper_id: str
    title: str
    seconds: float
    status: str
    detail: str
    claims: int = 0


def _queued_papers(database: Database, limit: int) -> list[dict[str, object]]:
    """Papers with a stored full text and no extraction, in a stable order.

    Ordered by id rather than by insertion: a re-run of the same `--limit` must
    cover the same papers, or "the second run passed the first run's papers" is
    not a statement about anything.
    """

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.title
            FROM papers p
            JOIN full_texts ft ON ft.paper_id = p.id
            WHERE ft.processed_at IS NULL
            ORDER BY p.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _queued_jobs(database: Database, limit: int) -> list[dict[str, object]]:
    """Jobs already queued in the corpus, in the order they were created.

    The counterpart to `_queued_papers` for the case that actually matters on the
    production corpus: the papers are already enqueued, and running *those* is the
    question. Selecting by "no extraction yet" would answer a different one.
    """

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.id AS job_id, j.paper_id, p.title
            FROM paper_extraction_jobs j
            JOIN papers p ON p.id = j.paper_id
            WHERE j.status = 'queued'
            ORDER BY j.created_at, j.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _already_extracted(database: Database, paper_id: str) -> bool:
    with database.connect() as connection:
        return (
            connection.execute(
                "SELECT 1 FROM paper_extractions WHERE paper_id = ? LIMIT 1", (paper_id,)
            ).fetchone()
            is not None
        )


def _claim_count(database: Database, paper_id: str) -> int:
    with database.connect() as connection:
        return int(
            connection.execute(
                "SELECT count(*) FROM claims WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="model id to send to the provider")
    parser.add_argument("--limit", type=int, default=3, help="papers to extract (small!)")
    parser.add_argument("--apply", action="store_true", help="run; default is a preview")
    parser.add_argument(
        "--database",
        default=os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"),
    )
    parser.add_argument(
        "--objects", default=os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")
    )
    parser.add_argument("--timeout", type=float, default=900.0, help="per provider call")
    parser.add_argument(
        "--keep", action="store_true", help="keep the throwaway corpus for inspection"
    )
    parser.add_argument(
        "--queued-jobs",
        action="store_true",
        help="run the jobs already queued in the corpus, instead of unextracted papers",
    )
    args = parser.parse_args()

    key = (
        os.getenv("PAPER_AI_API_KEY", "").strip()
        or os.getenv("ARK_API_KEY", "").strip()
    )
    endpoint = (
        os.getenv("PAPER_AI_BASE_URL", "").strip()
        or os.getenv("ARK_BASE_URL", "").strip()
    )
    if not args.apply:
        print(f"model      : {args.model}")
        print(f"endpoint   : {endpoint or '(not set — needed only with --apply)'}")
        print(f"key present: {'yes' if key else 'no (needed only with --apply)'}")
        print(f"limit      : {args.limit} paper(s)")
        print()
        print("preview only: nothing copied, nothing run. Re-run with --apply.")

    work = Path(tempfile.mkdtemp(prefix="genesis-extract-"))
    corpus = work / "genesis-evidence.sqlite3"
    objects = work / "objects"
    shutil.copy2(args.database, corpus)
    shutil.copytree(args.objects, objects)
    print(f"throwaway corpus: {work}")
    print()

    database = Database(corpus)
    database.initialize()
    store = PaperStore(database)
    if args.queued_jobs:
        selected = _queued_jobs(database, args.limit)
        print(f"jobs already queued in the corpus: {len(selected)} selected")
        for row in selected:
            print(f"  {str(row['job_id'])[:8]}  {str(row['title'])[:60]}")
    else:
        selected = _queued_papers(database, args.limit)
        print(f"papers with a full text and no extraction: {len(selected)} selected")
        for row in selected:
            print(f"  {str(row['id'])[:8]}  {str(row['title'])[:64]}")
    print()

    if not args.apply:
        print("preview only: the corpus copy above is discarded; nothing was extracted.")
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        return

    if not key:
        shutil.rmtree(work, ignore_errors=True)
        raise SystemExit("PAPER_AI_API_KEY (or ARK_API_KEY) is required with --apply")
    if not endpoint:
        shutil.rmtree(work, ignore_errors=True)
        raise SystemExit("PAPER_AI_BASE_URL (or ARK_BASE_URL) is required with --apply")

    analyzer = ArkPaperAnalyzer(
        api_key=key,
        endpoint=endpoint,
        model=args.model,
        timeout_seconds=args.timeout,
        max_tokens=int(os.getenv("PAPER_AI_MAX_TOKENS", "32768")),
    )
    worker = LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(objects),
        analyzer=analyzer,
        auto_reviewer=EvidenceReviewService(ReviewStore(database), store, ObjectStore(objects)),
        requested_by=os.getenv("GENESIS_EVIDENCE_REVIEWER_ID", "local-batch"),
    )

    outcomes: list[PaperOutcome] = []
    for row in selected:
        paper_id = str(row["paper_id"] if args.queued_jobs else row["id"])
        started = time.monotonic()
        status, detail = "completed", ""
        try:
            # Drive a *named* job rather than "the next one": the worker's
            # `claim_next` is global, and the corpus already holds queued jobs from
            # earlier runs, so a per-paper loop built on it would extract someone
            # else's paper and report the mismatch as this one's failure.
            if args.queued_jobs:
                job_id = str(row["job_id"])
            else:
                job_id = store.enqueue_extraction(paper_id, collection_run_id=None)
            worker.run_job_id(job_id)
        except PaperAnalysisError as exc:
            status, detail = "provider_error", str(exc)[:160]
        except Exception as exc:  # noqa: BLE001 - the report is the point
            status, detail = "error", f"{type(exc).__name__}: {str(exc)[:140]}"
        elapsed = time.monotonic() - started
        extracted = _already_extracted(database, paper_id)
        claims = _claim_count(database, paper_id)
        if status == "completed" and not extracted:
            status, detail = "no_extraction_written", "worker returned without persisting one"
        outcomes.append(
            PaperOutcome(
                paper_id=paper_id,
                title=str(row["title"])[:64],
                seconds=elapsed,
                status=status,
                detail=detail,
                claims=claims,
            )
        )
        print(f"  {paper_id[:8]}  {elapsed:6.1f}s  {status:22} claims={claims}  {detail}")

    print()
    ok = [o for o in outcomes if o.status == "completed"]
    print(f"completed: {len(ok)}/{len(outcomes)}")
    if outcomes:
        print(f"wall time: {sum(o.seconds for o in outcomes):.0f}s total, "
              f"{sum(o.seconds for o in outcomes) / len(outcomes):.0f}s per paper")
    if ok:
        print(f"claims written: {sum(o.claims for o in ok)} across {len(ok)} paper(s)")

    report = work / "batch-report.json"
    report.write_text(
        json.dumps([o.__dict__ for o in outcomes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"report: {report}")

    if args.keep:
        print(f"kept the throwaway corpus at {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)

    # A batch where nothing completed is a failure, not an empty success.
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
