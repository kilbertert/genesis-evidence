import json
import sqlite3

import pytest

from genesis_evidence.core.store import Database


def test_schema_has_expected_tables_and_stays_under_budget(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    tables = set(database.table_names())
    assert len(tables) == 30
    assert {
        "conditions",
        "evidence_topics",
        "papers",
        "claims",
        "studies",
        "study_publications",
        "results",
        "paper_extractions",
        "paper_extraction_jobs",
        "claim_reviews",
        "evidence_profiles",
        "evidence_profile_results",
        "knowledge_cards",
        "card_claims",
        "reports",
        "report_files",
        "report_observations",
        "observation_confirmations",
        "assessments",
        "assessment_findings",
        "product_candidates",
        "product_candidate_sources",
        "product_recommendations",
        "product_review_audits",
    } <= tables


def test_initialization_seeds_only_the_first_twelve_conditions(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    database.initialize()

    conditions = database.list_conditions()
    assert len(conditions) == 12
    assert conditions[0].code == "COND_ANEMIA_PATTERN"


def test_evidence_profile_has_stable_outcome_scope_key(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.connect() as connection:
        column = next(
            row
            for row in connection.execute("PRAGMA table_info(evidence_profiles)")
            if row["name"] == "scope_key"
        )
    assert column["notnull"] == 1
    assert column["dflt_value"] == "''"


@pytest.mark.parametrize("identifier", ["doi", "pmid", "pmcid"])
def test_papers_are_deduplicated_by_stable_identifier(tmp_path, identifier) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            f"INSERT INTO papers(id, title, {identifier}, created_at) VALUES (?, ?, ?, ?)",
            ("paper-1", "First", "same-id", "2026-08-11T00:00:00Z"),
        )
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            f"INSERT INTO papers(id, title, {identifier}, created_at) VALUES (?, ?, ?, ?)",
            ("paper-2", "Duplicate", "same-id", "2026-08-11T00:00:00Z"),
        )


def test_published_card_requires_review_and_patient_content_in_database(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_cards(id, condition_code, version, status, created_at)
            VALUES (?, ?, ?, 'published', ?)
            """,
            ("card-1", "COND_DYSLIPIDEMIA", "1.0.0", "2026-08-11T00:00:00Z"),
        )


@pytest.mark.parametrize(
    ("source", "source_id", "expected"),
    [("europe_pmc", "MED:1", "formal"), ("europe_pmc", "PPR:1", "preprint")],
)
def test_existing_publication_status_is_backfilled_from_verified_source(
    tmp_path, source, source_id, expected
) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(id, title, publication_status, created_at)
            VALUES ('paper-1', 'Existing paper', 'unknown', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO paper_sources(paper_id, source, source_id, source_url)
            VALUES ('paper-1', ?, ?, 'https://example.test/paper-1')
            """,
            (source, source_id),
        )
    database.initialize()
    with database.connect() as connection:
        status = connection.execute(
            "SELECT publication_status FROM papers WHERE id = 'paper-1'"
        ).fetchone()[0]
    assert status == expected


def test_legacy_claim_grade_is_archived_and_published_card_is_staled(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE conditions (
                code TEXT PRIMARY KEY, name TEXT NOT NULL, metrics_json TEXT NOT NULL,
                department TEXT NOT NULL, recheck_direction TEXT NOT NULL
            );
            CREATE TABLE papers (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL DEFAULT '',
                doi TEXT, pmid TEXT, pmcid TEXT, year INTEGER, study_design_candidate TEXT,
                integrity_status TEXT NOT NULL DEFAULT 'unknown', created_at TEXT NOT NULL
            );
            CREATE TABLE paper_extractions (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, model TEXT NOT NULL,
                extraction_run_id TEXT NOT NULL, extraction_json TEXT NOT NULL,
                check_model TEXT NOT NULL, check_run_id TEXT NOT NULL,
                consistency_status TEXT NOT NULL, consistency_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE claims (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, extraction_id TEXT NOT NULL,
                candidate_text TEXT NOT NULL, evidence_text TEXT NOT NULL, locator TEXT NOT NULL,
                candidate_study_design TEXT, status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL
            );
            CREATE TABLE claim_reviews (
                claim_id TEXT PRIMARY KEY, decision TEXT NOT NULL, corrected_text TEXT,
                corrected_study_design TEXT, inference TEXT, grade TEXT, condition_code TEXT,
                reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_cards (
                id TEXT PRIMARY KEY, condition_code TEXT NOT NULL, version TEXT NOT NULL,
                status TEXT NOT NULL, grade TEXT, reviewer TEXT, reviewed_at TEXT,
                published_at TEXT, patient_visible_body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT INTO conditions VALUES ('COND_DYSLIPIDEMIA', '血脂异常', '[]', '内科', '复查');
            INSERT INTO papers(id, title, integrity_status, created_at)
                VALUES ('paper-1', 'Legacy paper', 'clear', '2026-08-11T00:00:00Z');
            INSERT INTO paper_extractions VALUES (
                'extraction-1', 'paper-1', 'model', 'run-1', '{}', 'model', 'run-2',
                'consistent', '{}', '2026-08-11T00:00:00Z'
            );
            INSERT INTO claims VALUES (
                'claim-1', 'paper-1', 'extraction-1', 'Legacy claim', 'Evidence', 'Results',
                'cohort_study', 'reviewed', '2026-08-11T00:00:00Z'
            );
            INSERT INTO claim_reviews VALUES (
                'claim-1', 'approved', 'Corrected claim', 'cohort_study', 'associational',
                'moderate', 'COND_DYSLIPIDEMIA', 'reviewer-1', '2026-08-11T01:00:00Z'
            );
            INSERT INTO knowledge_cards VALUES (
                'card-1', 'COND_DYSLIPIDEMIA', '1.0.0', 'published', 'moderate',
                'reviewer-1', '2026-08-11T02:00:00Z', '2026-08-11T03:00:00Z',
                'Legacy patient content', '2026-08-11T02:00:00Z'
            );
            """
        )

    database = Database(path)
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        claim = connection.execute("SELECT status FROM claims WHERE id = 'claim-1'").fetchone()
        review = connection.execute(
            "SELECT decision FROM claim_reviews WHERE claim_id = 'claim-1'"
        ).fetchone()
        card = connection.execute(
            "SELECT status FROM knowledge_cards WHERE id = 'card-1'"
        ).fetchone()
        events = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE action = 'legacy_claim_review_invalidated' AND entity_id = 'claim-1'
            """
        ).fetchall()
    assert claim["status"] == "candidate"
    assert review["decision"] == "rejected"
    assert card["status"] == "stale"
    assert len(events) == 1
    assert json.loads(events[0]["detail_json"])["grade"] == "moderate"


def test_initialization_splits_legacy_shared_results_and_stales_cards(tmp_path) -> None:
    path = tmp_path / "legacy-shared-result.sqlite3"
    database = Database(path)
    database.initialize()
    extraction = {
        "claims": [
            {
                "text": "Nutrition increased serum albumin.",
                "evidence": "Albumin and prealbumin increased after nutrition support.",
                "locator": "Results",
                "population": "Adults receiving dialysis",
                "baseline_nutrient_status": "Protein-energy wasting",
                "ingredient_name": "Oral nutrition supplement",
                "ingredient_form": "Powder",
                "dose": "Daily",
                "comparator": "Usual care",
                "outcome": "Serum albumin",
                "timepoint": "3 months",
                "effect_estimate": "Increased",
                "statistical_details": "p < 0.001",
            },
            {
                "text": "Nutrition increased serum prealbumin.",
                "evidence": "Albumin and prealbumin increased after nutrition support.",
                "locator": "Results",
                "population": "Adults receiving dialysis",
                "baseline_nutrient_status": "Protein-energy wasting",
                "ingredient_name": "Oral nutrition supplement",
                "ingredient_form": "Powder",
                "dose": "Daily",
                "comparator": "Usual care",
                "outcome": "Serum prealbumin",
                "timepoint": "3 months",
                "effect_estimate": "Increased",
                "statistical_details": "p < 0.001",
            },
        ]
    }
    with database.transaction() as connection:
        connection.executescript(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES (
                'topic', 'malnutrition', '1', 'COND_MALNUTRITION_RISK', 'locked',
                'Does nutrition support improve biomarkers?', '{}', '[]', '[]', '[]', '[]',
                '2026-08-01', 'reviewer', '2026-08-01T00:00:00Z',
                'reviewer', '2026-08-01T00:00:00Z'
            );
            INSERT INTO papers(id, title, publication_status, integrity_status, created_at)
            VALUES ('paper', 'Nutrition trial', 'formal', 'clear', '2026-08-01T00:00:00Z');
            INSERT INTO studies(id, study_design, created_at)
            VALUES ('study', 'randomized_controlled_trial', '2026-08-01T00:00:00Z');
            INSERT INTO study_publications(study_id, paper_id) VALUES ('study', 'paper');
            """
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES (
                'extraction', 'paper', 'model-a', 'run-a', ?, 'model-b', 'run-b', ?,
                'checker', 'run-check', 'consistent', '{}', '2026-08-01T00:00:00Z'
            )
            """,
            (json.dumps(extraction), json.dumps(extraction)),
        )
        connection.executescript(
            """
            INSERT INTO results(
                id, study_id, paper_id, extraction_id, population,
                baseline_nutrient_status, ingredient_name, ingredient_form,
                dose, comparator, outcome, timepoint, effect_estimate,
                statistical_details, evidence_text, locator, status, created_at
            ) VALUES (
                'shared-result', 'study', 'paper', 'extraction', 'Adults receiving dialysis',
                'Protein-energy wasting', 'Oral nutrition supplement', 'Powder', 'Daily',
                'Usual care', 'Serum albumin', '3 months', 'Increased', 'p < 0.001',
                'Albumin and prealbumin increased after nutrition support.', 'Results',
                'candidate', '2026-08-01T00:00:00Z'
            );
            INSERT INTO claims(
                id, paper_id, extraction_id, result_id, candidate_text,
                evidence_text, locator, candidate_claim_type, candidate_study_design,
                status, created_at
            ) VALUES
                ('claim-albumin', 'paper', 'extraction', 'shared-result',
                    'Nutrition increased serum albumin.',
                    'Albumin and prealbumin increased after nutrition support.', 'Results',
                    'intervention_effect', 'randomized_controlled_trial', 'reviewed',
                    '2026-08-01T00:00:00Z'),
                ('claim-prealbumin', 'paper', 'extraction', 'shared-result',
                    'Nutrition increased serum prealbumin.',
                    'Albumin and prealbumin increased after nutrition support.', 'Results',
                    'intervention_effect', 'randomized_controlled_trial', 'reviewed',
                    '2026-08-01T00:00:00Z');
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, scope_key, version, ingredient_name,
                ingredient_form, population, baseline_nutrient_status, dose, comparator,
                outcome, timepoint, estimate_target, evidence_body_complete, certainty,
                certainty_rationale, evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES (
                'profile', 'topic', 'COND_MALNUTRITION_RISK', 'metric:albumin', '1.0.0',
                'Oral nutrition supplement', 'Powder', 'Adults receiving dialysis',
                'Protein-energy wasting', 'Daily', 'Usual care', 'Serum albumin', '3 months',
                'Change from baseline', 1, 'low', 'Single trial', '2026-08-01', 'reviewer',
                '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z'
            );
            INSERT INTO evidence_profile_results(profile_id, result_id, interpretation)
            VALUES ('profile', 'shared-result', 'supports');
            INSERT INTO knowledge_cards(
                id, condition_code, version, status, grade, evidence_profile_id, reviewer,
                reviewed_at, published_at, patient_visible_body, created_at
            ) VALUES (
                'card', 'COND_MALNUTRITION_RISK', '1.0.0', 'published', 'low', 'profile',
                'reviewer', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z',
                'Reviewed context.', '2026-08-01T00:00:00Z'
            );
            INSERT INTO card_claims(card_id, claim_id, evidence_text, locator) VALUES
                ('card', 'claim-albumin',
                    'Albumin and prealbumin increased after nutrition support.', 'Results');
            """
        )

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.result_id, r.study_id, r.outcome, r.status, r.created_at
            FROM claims c JOIN results r ON r.id = c.result_id ORDER BY c.id
            """
        ).fetchall()
        profile_results = connection.execute(
            "SELECT count(*) FROM evidence_profile_results WHERE profile_id = 'profile'"
        ).fetchone()[0]
        card_status = connection.execute(
            "SELECT status FROM knowledge_cards WHERE id = 'card'"
        ).fetchone()[0]
        old_result = connection.execute(
            "SELECT count(*) FROM results WHERE id = 'shared-result'"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action = 'legacy_result_identity_split'"
        ).fetchone()[0]

    assert len({row["result_id"] for row in rows}) == 2
    assert {row["id"]: row["outcome"] for row in rows} == {
        "claim-albumin": "Serum albumin",
        "claim-prealbumin": "Serum prealbumin",
    }
    assert {row["study_id"] for row in rows} == {"study"}
    assert {row["status"] for row in rows} == {"candidate"}
    assert {row["created_at"] for row in rows} == {"2026-08-01T00:00:00Z"}
    assert profile_results == 1
    assert card_status == "stale"
    assert old_result == 0
    assert events == 1
