"""Reject unsafe claims from patient-visible source files."""

from __future__ import annotations

from pathlib import Path

from genesis_evidence.core.patient_copy import FORBIDDEN_PATIENT_TERMS

ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence" / "portal"
ADDITIONAL_PATHS = (
    Path(__file__).parents[1] / "src" / "genesis_evidence" / "products" / "recommendations.py",
    Path(__file__).parents[1] / "src" / "genesis_evidence" / "products" / "mapping_drafts.py",
)
TEXT_SUFFIXES = {".py", ".html", ".js", ".jsx", ".ts", ".tsx", ".vue"}


def main() -> None:
    violations: list[str] = []
    paths = list(ROOT.rglob("*")) if ROOT.exists() else []
    paths.extend(ADDITIONAL_PATHS)
    for path in paths:
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PATIENT_TERMS:
            if phrase in text:
                violations.append(f"{path.name}: {phrase}")
    if violations:
        raise SystemExit("Forbidden patient copy found:\n" + "\n".join(violations))


if __name__ == "__main__":
    main()
