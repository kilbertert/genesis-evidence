"""Fail CI when frozen product surfaces enter the active package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"
FROZEN_IDENTIFIERS = {
    "care_task",
    "electronic_signature",
    "followup",
    "intervention_plan",
    "nutrition_product",
    "organization",
    "professional_note",
    "rbac",
    "record_share",
    "service_event",
    "supplier_product",
    "supplement_recommendation",
    "test_plan",
}
TEXT_SUFFIXES = {".py", ".html", ".js", ".css", ".toml"}


def main() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8").casefold()
        for identifier in FROZEN_IDENTIFIERS:
            if identifier in text or identifier in path.as_posix().casefold():
                violations.append(f"{path.relative_to(ROOT)}: {identifier}")
    if violations:
        raise SystemExit("Frozen scope identifiers found:\n" + "\n".join(violations))


if __name__ == "__main__":
    main()
