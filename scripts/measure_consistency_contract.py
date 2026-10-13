#!/usr/bin/env python3
"""Measure what the verbatim-evidence contract changes, on the real corpus.

Run:
    scripts/measure_consistency_contract.py --corpus PATH --limit 20 [--apply]

Why this exists. `_source_adjudication` is deterministic: it decides an issue only when
the issue's quoted `evidence` attests against the stored full text, and it declines
otherwise. Before the contract in `ai_extraction._require_verbatim_evidence`,
`_CONSISTENCY_PROMPT` asked for `evidence` in free prose -- so the checker could answer
with a summary, and the adjudicator would decline an issue that was never given a chance
to pass. The measured effect, on the stored corpus, was 43 of 80 material issues declined
for exactly that reason.

This script re-runs **only the consistency stage**, over extraction A/B that are already
stored, and reports the adjudicator's disposition before and after. It does not re-extract:
the extractions are already the input, and re-running them would answer a different
question at several times the cost.

It calls the provider, so it needs a key -- the same one the extraction worker uses
(`PAPER_AI_API_KEY_FILE` / `ARK_API_KEY_FILE`, or `PAPER_AI_API_KEY` / `ARK_API_KEY`).

Without `--apply` it reports which papers it would use and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis_evidence.core.source_excerpt import (  # noqa: E402
    Excerpt,
    _source_evidence_fragments,
    attest,
    segments_from_document,
)
from genesis_evidence.core.store import Database, ObjectStore, PaperStore  # noqa: E402
from genesis_evidence.core.store.review import _critical_issue  # noqa: E402
from genesis_evidence.literature.ai_extraction import (  # noqa: E402
    ArkPaperAnalyzer,
    PaperAnalysisError,
    PaperExtraction,
)
from genesis_evidence.literature.jats import JatsParser  # noqa: E402


@dataclass(frozen=True)
class Disposition:
    """What the deterministic adjudicator would do with one issue."""

    field: str
    severity: str
    kind: str


def _adjudication_disposition(issue: dict[str, object], segments: tuple[str, ...]) -> Disposition:
    """Classify one issue the way `_source_adjudication` decides it.

    Mirrors only the quote gate, which is the branch this measurement is about: the
    adjudicator returns `None` for an issue whose fragments do not attest, and proceeds
    otherwise. Structural checks beyond that are policy the caller owns.
    """

    field, severity = str(issue.get("field")), str(issue.get("severity"))
    fragments = _source_evidence_fragments(issue.get("evidence"))
    if not fragments:
        return Disposition(field, severity, "no_quote")
    unattested = attest(
        segments,
        [Excerpt(field="fragment", text=fragment) for fragment in fragments],
        tolerance="strict",
    ).missing
    return Disposition(field, severity, "quote_not_verbatim" if unattested else "adjudicable")


def _material(issues: list[dict[str, object]], claims: list[dict[str, object]]) -> list[dict]:
    return [issue for issue in issues if _critical_issue(issue, claims)]


def _analyzer() -> ArkPaperAnalyzer:
    return ArkPaperAnalyzer.from_env(
        model=os.getenv("PAPER_AI_MODEL", "deepseek/deepseek-v4.1-flash"),
        second_model=os.getenv("PAPER_AI_SECOND_MODEL", os.getenv("PAPER_AI_MODEL", "")),
        timeout=float(os.getenv("PAPER_AI_TIMEOUT_SECONDS", "600")),
        max_tokens=int(os.getenv("PAPER_AI_MAX_TOKENS", "32768")),
    )


def _key_present() -> bool:
    for name in ("PAPER_AI_API_KEY_FILE", "ARK_API_KEY_FILE", "PAPER_AI_API_KEY", "ARK_API_KEY"):
        if os.getenv(name, "").strip():
            return True
    return False


def _candidates(database: Database, topic: str | None) -> list[dict[str, object]]:
    """Papers whose stored extractions let the consistency stage be replayed.

    Scoped to one topic when asked. A cross-topic batch is what paused the extraction
    worker in the first place, so a replay follows the same one-topic-at-a-time rule.
    """

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT pe.paper_id, pe.extraction_json,
                   pe.second_extraction_json, pe.consistency_json
            FROM paper_extractions pe
            WHERE pe.id = (
                    SELECT latest.id FROM paper_extractions latest
                    WHERE latest.paper_id = pe.paper_id
                    ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
                )
                AND pe.extraction_json IS NOT NULL
                AND pe.second_extraction_json IS NOT NULL
                AND (
                    ? IS NULL OR EXISTS (
                        SELECT 1 FROM collection_papers cp
                        JOIN collection_runs cr ON cr.id = cp.run_id
                        JOIN evidence_topics et ON et.id = cr.topic_id
                        WHERE cp.paper_id = pe.paper_id AND et.code = ?
                    )
                )
            ORDER BY pe.paper_id
            """,
            (topic, topic),
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="path to the sqlite corpus to copy")
    parser.add_argument(
        "--objects", default=None, help="object store; default <corpus dir>/objects"
    )
    parser.add_argument("--limit", type=int, default=10, help="papers to replay")
    parser.add_argument(
        "--topic", default=None, help="restrict to one topic code, as the worker rule requires"
    )
    parser.add_argument("--apply", action="store_true", help="call the provider; otherwise preview")
    parser.add_argument("--report", default=None, help="where the JSON result goes")
    parser.add_argument(
        "--model", default=os.getenv("PAPER_AI_MODEL", "deepseek/deepseek-v4.1-flash")
    )
    parser.add_argument("--keep", action="store_true", help="keep the throwaway corpus")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error(f"--limit must be at least 1 (got {args.limit})")

    work = Path(tempfile.mkdtemp(prefix="genesis-consistency-"))
    corpus = work / "genesis-evidence.sqlite3"
    objects = work / "objects"
    source_objects = Path(args.objects) if args.objects else Path(args.corpus).parent / "objects"

    # A copy, not the original: DB.initialize() writes, so even a read-only intent must not
    # point at the live file. `VACUUM INTO` rather than a byte copy, because the source may
    # be a live database and a page-level copy can land mid-write.
    source = Path(args.corpus)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin:
        origin.execute("VACUUM INTO ?", (str(corpus),))
    shutil.copytree(source_objects, objects)

    database = Database(corpus)
    database.initialize()
    store = PaperStore(database)
    object_store = ObjectStore(objects)
    jats = JatsParser()

    rows = _candidates(database, args.topic)[: args.limit]
    print(f"corpus   : {source} (copied to {corpus})")
    print(f"topic    : {args.topic or '(all)'}")
    print(f"model    : {args.model}")
    print(f"key      : {'present' if _key_present() else 'absent'}")
    print(f"papers with a replayable consistency stage: {len(rows)} selected")
    for row in rows[:10]:
        print(f"  {str(row['paper_id'])[:8]}")

    if not args.apply:
        print("\npreview only: nothing run. Re-run with --apply.")
        shutil.rmtree(work, ignore_errors=True)
        return
    if not _key_present():
        raise SystemExit(
            "no provider key: set PAPER_AI_API_KEY_FILE / ARK_API_KEY_FILE "
            "or PAPER_AI_API_KEY / ARK_API_KEY"
        )

    analyzer = _analyzer()
    outcomes: list[dict[str, object]] = []
    for row in rows:
        paper_id = str(row["paper_id"])
        before: list[Disposition] = []
        after: list[Disposition] = []
        status, detail = "completed", ""
        try:
            _, object_key = store.get_analysis_source(paper_id)
            document = jats.parse(object_store.read(object_key)).to_dict()
            segments = segments_from_document(document)
            extraction = PaperExtraction.model_validate(json.loads(row["extraction_json"]))
            second = PaperExtraction.model_validate(json.loads(row["second_extraction_json"]))
            with database.connect() as connection:
                claims = [
                    dict(claim)
                    for claim in connection.execute(
                        "SELECT * FROM claims WHERE paper_id = ?", (paper_id,)
                    )
                ]
            old = json.loads(row["consistency_json"]) if row["consistency_json"] else {"issues": []}
            paper = store.get_analysis_source(paper_id)[0]
            before = [
                _adjudication_disposition(issue, segments)
                for issue in _material(old.get("issues") or [], claims)
            ]
            report, _ = analyzer.check(paper, document, extraction, second)
            after = [
                _adjudication_disposition(issue.model_dump(mode="json"), segments)
                for issue in _material(
                    [issue.model_dump(mode="json") for issue in report.issues], claims
                )
            ]
        except PaperAnalysisError as exc:
            status, detail = "provider_error", str(exc)[:200]
        except Exception as exc:  # noqa: BLE001 - the report is the point
            status, detail = "error", f"{type(exc).__name__}: {str(exc)[:160]}"
        outcomes.append(
            {
                "paper_id": paper_id,
                "status": status,
                "detail": detail,
                "before": [d.__dict__ for d in before],
                "after": [d.__dict__ for d in after],
            }
        )
        print(
            f"  {paper_id[:8]}  {status:14} before: {_tally(before)}  after: {_tally(after)}"
            + (f"  {detail}" if detail else "")
        )

    print()
    print("=== totals over material issues ===")
    head_rows = [d for o in outcomes for d in _as_dispositions(o["before"])]
    print(f"  before: {_tally(head_rows)}")
    tail_rows = [d for o in outcomes for d in _as_dispositions(o["after"])]
    print(f"  after : {_tally(tail_rows)}")

    report_path = (
        Path(args.report)
        if args.report
        else Path(f"var/consistency-contract-{args.model.replace('/', '-')}.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {report_path}")

    if args.keep:
        print(f"kept the throwaway corpus at {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)


def _as_dispositions(rows: list[dict[str, object]]) -> list[Disposition]:
    return [Disposition(**row) for row in rows]  # type: ignore[arg-type]


def _tally(dispositions: list[Disposition]) -> str:
    counts: dict[str, int] = {}
    for disposition in dispositions:
        counts[disposition.kind] = counts.get(disposition.kind, 0) + 1
    return ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())) or "none"


if __name__ == "__main__":
    main()
