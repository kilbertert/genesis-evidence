"""Export a narrow, real subset of published knowledge cards for seeding.

Follows the foreign-key closure from the cards we want, so the emitted set is
self-consistent rather than a hand-listed guess at which tables are involved.
Emits JSON, not SQL text, so values containing quotes, newlines, or non-ASCII
text survive intact — the loader binds them as parameters.

Selection: published cards whose condition is COND_PREDIABETES on
fasting_glucose / hba1c. This is a curated subset, not a migration: the target
stays a new tenant with test-seeded content, not a copy of production.
"""

import json
import sqlite3

SRC = "var/genesis-evidence.sqlite3"
OUT = "/tmp/seed-cards.json"
SCOPES = ("metric:fasting_glucose", "metric:hba1c")
CONDITION = "COND_PREDIABETES"

# Tables reachable from the seed, in insert order (parents first).
CLOSURE = [
    "evidence_topics",
    "papers",
    "paper_extractions",
    "results",
    "claims",
    "evidence_profiles",
    "knowledge_cards",
    "card_claims",
]

src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row


def qmarks(n):
    return ",".join("?" * n)


def fetch(table, ids, key="id"):
    """Rows as dicts.

    dicts, not sqlite3.Row: membership on a Row tests *values*, not column
    names, so `"col" in row` is always False for a column test and silently
    drops rows.
    """
    if not ids:
        return []
    return [dict(r) for r in src.execute(
        f"SELECT * FROM {table} WHERE {key} IN ({qmarks(len(ids))})", sorted(ids)
    ).fetchall()]


cards = src.execute(
    """
    SELECT kc.* FROM knowledge_cards kc
    JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
    WHERE kc.status = 'published' AND kc.grade IN ('high','moderate','low')
      AND kc.condition_code = ? AND ep.scope_key IN (?,?)
    """,
    (CONDITION, *SCOPES),
).fetchall()

cards = [dict(c) for c in cards]
card_ids = {c["id"] for c in cards}
profile_ids = {c["evidence_profile_id"] for c in cards if c["evidence_profile_id"]}
card_claims = fetch("card_claims", card_ids, "card_id")
claim_ids = {r["claim_id"] for r in card_claims if r["claim_id"]}
claims = fetch("claims", claim_ids)
paper_ids = {r["paper_id"] for r in claims if r["paper_id"]}
papers = fetch("papers", paper_ids)
extraction_ids = {r["extraction_id"] for r in claims if r["extraction_id"]}
extractions = fetch("paper_extractions", extraction_ids)
result_ids = {r["result_id"] for r in claims if r["result_id"]}
results = fetch("results", result_ids)
# results reference studies, so they join the closure too.
study_ids = {r["study_id"] for r in results if r.get("study_id")}
studies = fetch("studies", study_ids)
profiles = fetch("evidence_profiles", profile_ids)
# evidence_profiles reference evidence_topics, which reference conditions.
topic_ids = {r["topic_id"] for r in profiles if r["topic_id"]}
topics = fetch("evidence_topics", topic_ids)
# Cards and profiles reference conditions, which the new tenant already seeds
# from code; only carry across any the target might lack.
condition_ids = ({c["condition_code"] for c in cards} |
                 {r["condition_code"] for r in profiles if r.get("condition_code")})
conditions = fetch("conditions", condition_ids, "code")

tables = {
    # Everything below is normalised to plain dicts so the payload serialises.
    "conditions": conditions,
    "studies": studies,
    "papers": papers,
    "paper_extractions": extractions,
    "results": results,
    "claims": claims,
    "evidence_topics": topics,
    "evidence_profiles": profiles,
    "knowledge_cards": cards,
    "card_claims": card_claims,
}

with open(OUT, "w") as fh:
    json.dump({"condition": CONDITION, "scopes": list(SCOPES), "tables": tables},
              fh, ensure_ascii=False, indent=1)

print({k: len(v) for k, v in tables.items()})
