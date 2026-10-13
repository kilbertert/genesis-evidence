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
    _api_key_from_file,
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


def _job_failure(database: Database, job_id: str) -> tuple[str, str] | None:
    """Why *this* job left un-finished, if it did.

    `LiteratureExtractionWorker.run_job` catches every exception, records it on the
    job and **returns normally** — right for a long-running worker, wrong for a
    harness whose whole purpose is to report what the provider did. Without this,
    a provider failure surfaces as "no extraction was written", which is the
    harness hiding the exact thing it exists to measure.

    Looked up by `job_id`, not by `paper_id`: a paper can carry more than one job
    (a retry after a fix), and "the newest job for this paper" would report a
    different run's error the moment an older failed job sorts later.
    """

    with database.connect() as connection:
        row = connection.execute(
            "SELECT error_class, error_message FROM paper_extraction_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None or row["error_class"] is None:
        return None
    return str(row["error_class"]), str(row["error_message"] or "")


def _slug(text: str) -> str:
    """A model name is a path segment, so keep it to characters that are safe there."""

    return "".join(c if c.isalnum() or c in "._-" else "-" for c in text).strip("-") or "run"


def _key_present() -> bool:
    """Whether a key is configured, without validating it.

    A preview only reports this; `_resolve_key` is what actually reads the file.
    Kept separate so "is it configured" cannot throw, and "give me the key" can.
    """

    for name in ("PAPER_AI_API_KEY_FILE", "ARK_API_KEY_FILE", "PAPER_AI_API_KEY", "ARK_API_KEY"):
        if os.getenv(name, "").strip():
            return True
    return False


def _resolve_key() -> str:
    """The provider key, from the same places `ArkPaperAnalyzer.from_env` reads.

    Reusing that resolution rather than hand-rolling it is the point: the working
    configuration on this host uses a **key file** (`PAPER_AI_API_KEY_FILE`, a CSV
    with an `apiKey` column), and a harness that only understood inline keys would
    report "key missing" for a machine that is in fact configured.
    """

    key_file = (
        os.getenv("PAPER_AI_API_KEY_FILE", "").strip() or os.getenv("ARK_API_KEY_FILE", "").strip()
    )
    if key_file:
        return _api_key_from_file(key_file)
    return os.getenv("PAPER_AI_API_KEY", "").strip() or os.getenv("ARK_API_KEY", "").strip()


def _copy_corpus(source: Path, destination: Path) -> None:
    """Copy a SQLite database without racing a concurrent writer.

    `shutil.copy2` is a filesystem copy: it takes no database read transaction and
    ignores the rollback journal and WAL, so a copy taken while another process is
    writing can combine pages from before and after a commit. Every change here is
    read off a copy, so a torn one would make the batch report failures that never
    happened — or miss ones that did.

    `VACUUM INTO` runs inside a read transaction and writes a consistent snapshot,
    and it is the one form SQLite provides for exactly this.
    """

    import sqlite3

    if destination.exists():
        destination.unlink()
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        connection.execute("VACUUM INTO ?", (str(destination),))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="model id to send to the provider")
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="papers to extract (small!). Must be positive: SQLite reads LIMIT -1 as "
        "unlimited, so a negative value would send every eligible paper to the provider.",
    )
    parser.add_argument("--apply", action="store_true", help="run; default is a preview")
    parser.add_argument(
        "--database",
        default=os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"),
    )
    parser.add_argument("--objects", default=os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects"))
    parser.add_argument("--timeout", type=float, default=900.0, help="per provider call")
    parser.add_argument(
        "--keep", action="store_true", help="keep the throwaway corpus for inspection"
    )
    parser.add_argument(
        "--report",
        default=None,
        help="where the JSON result goes; kept even when the throwaway corpus is "
        "removed. Defaults to a per-run filename, so re-running a model does not "
        "overwrite the run you are comparing against.",
    )
    parser.add_argument(
        "--report-dir",
        default="var/local-extraction-reports",
        help="directory for the default per-run report filename",
    )
    parser.add_argument(
        "--queued-jobs",
        action="store_true",
        help="run the jobs already queued in the corpus, instead of unextracted papers",
    )
    args = parser.parse_args()

    if args.report is None:
        # Resolved once, here, rather than at write time. The write happens after
        # a batch that may run for an hour; a name minted there could collide with
        # a batch finishing in the same second, and the later write would win.
        # Microseconds make that a non-issue for real runs, and the exclusive
        # create below makes it a non-issue for the adversarial case too.
        args.report = str(
            Path(args.report_dir) / f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"
            f"-{time.time_ns() % 1_000_000:06d}Z-{_slug(args.model)}.json"
        )

    if args.limit < 1:
        # SQLite reads `LIMIT -1` as "no limit", so a negative value would not mean
        # "none" — it would send every eligible paper to the provider.
        parser.error(f"--limit must be at least 1 (got {args.limit})")

    endpoint = os.getenv("PAPER_AI_BASE_URL", "").strip() or os.getenv("ARK_BASE_URL", "").strip()
    if not args.apply:
        # A preview must not need a working key: resolving one here would make a
        # dry run fail on a machine whose key is absent or misconfigured, which is
        # a reason to run the preview in the first place.
        key_state = "present" if _key_present() else "absent (needed only with --apply)"
        print(f"model      : {args.model}")
        print(f"endpoint   : {endpoint or '(not set — needed only with --apply)'}")
        print(f"key        : {key_state}")
        print(f"limit      : {args.limit} paper(s)")
        print()
        print("preview only: nothing copied, nothing run. Re-run with --apply.")

    work = Path(tempfile.mkdtemp(prefix="genesis-extract-"))
    corpus = work / "genesis-evidence.sqlite3"
    objects = work / "objects"
    _copy_corpus(Path(args.database), corpus)
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

    # Only now is the key actually read — and reading it can fail (missing file, no
    # apiKey field), which is a real failure worth raising rather than reporting as
    # "no key configured".
    key = _resolve_key()
    if not key:
        shutil.rmtree(work, ignore_errors=True)
        raise SystemExit(
            "no provider key: set PAPER_AI_API_KEY_FILE / ARK_API_KEY_FILE to a CSV with "
            "an api_key column, or PAPER_AI_API_KEY / ARK_API_KEY"
        )
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
            # `run_job` swallows the exception, so the reason is on the job row.
            failure = _job_failure(database, job_id)
            if failure is not None:
                # NOT `provider_error`: the worker also fails on parsing, object
                # loading and persistence, and calling all of those a provider fault
                # would send the reader to the wrong layer.
                status, detail = "job_error", f"{failure[0]}: {failure[1][:140]}"
            else:
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
        print(
            f"wall time: {sum(o.seconds for o in outcomes):.0f}s total, "
            f"{sum(o.seconds for o in outcomes) / len(outcomes):.0f}s per paper"
        )
    if ok:
        print(f"claims written: {sum(o.claims for o in ok)} across {len(ok)} paper(s)")

    # Written OUTSIDE `work`: the report is the durable result of a batch, and
    # deleting the throwaway corpus must not take it along.
    #
    # Resolved from `args.report` captured *before the run*, not invented here: a
    # name minted after the run with second resolution collides for two batches
    # that finish in the same second, and `write_text` would then silently keep
    # only the later one. Uniqueness is by construction rather than by luck.
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation: the explicit `--report` path is the caller's to
        # choose, but it must not quietly eat a report that is already there.
        with report.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps([o.__dict__ for o in outcomes], ensure_ascii=False, indent=2))
    except FileExistsError:
        print(f"report: {report} already exists — pass a different --report (not overwriting)")
        raise SystemExit(1) from None
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
