"""Create review-pending product mappings from the functional-category workbook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from genesis_evidence.core.store import Database
from genesis_evidence.products.mapping_drafts import create_mapping_drafts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--actor", default="ai:offline-mapper")
    args = parser.parse_args(argv)
    database = Database(args.database)
    database.initialize()
    summary = create_mapping_drafts(
        database,
        args.workbook,
        actor=args.actor,
        source_ref=args.workbook.name,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
