"""The complete first-stage SQLite schema."""

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conditions (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    department TEXT NOT NULL,
    recheck_direction TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id TEXT PRIMARY KEY,
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    source TEXT NOT NULL,
    search_stream TEXT NOT NULL DEFAULT 'effect'
        CHECK (search_stream IN (
            'effect', 'requirement', 'bioavailability', 'safety',
            'registration', 'regulatory', 'citation'
        )),
    query_version TEXT NOT NULL DEFAULT '1',
    query TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    doi TEXT,
    pmid TEXT,
    pmcid TEXT,
    year INTEGER,
    publication_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (publication_status IN ('formal', 'preprint', 'unknown')),
    study_design_candidate TEXT,
    integrity_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (integrity_status IN (
            'clear', 'updated', 'corrected', 'expression_of_concern', 'retracted', 'unknown'
        )),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS papers_doi_unique
ON papers(doi) WHERE doi IS NOT NULL AND doi <> '';

CREATE UNIQUE INDEX IF NOT EXISTS papers_pmid_unique
ON papers(pmid) WHERE pmid IS NOT NULL AND pmid <> '';

CREATE UNIQUE INDEX IF NOT EXISTS papers_pmcid_unique
ON papers(pmcid) WHERE pmcid IS NOT NULL AND pmcid <> '';

CREATE TABLE IF NOT EXISTS paper_sources (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license TEXT,
    PRIMARY KEY (paper_id, source, source_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS paper_sources_identity_unique
ON paper_sources(source, source_id);

CREATE TABLE IF NOT EXISTS collection_papers (
    run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (run_id, paper_id)
);

CREATE TABLE IF NOT EXISTS full_texts (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    rights_status TEXT NOT NULL
        CHECK (rights_status IN (
            'redistributable', 'internal_tdm_only', 'metadata_only', 'unknown'
        )),
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_admissions (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'internally_admitted', 'rejected')),
    condition_codes_json TEXT NOT NULL DEFAULT '[]',
    reviewer TEXT,
    reviewed_at TEXT,
    consistency_resolution TEXT
);

CREATE TABLE IF NOT EXISTS studies (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'provisional'
        CHECK (status IN ('provisional', 'verified', 'merged')),
    study_design TEXT,
    registration_ids_json TEXT NOT NULL DEFAULT '[]',
    reviewer TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_publications (
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'primary'
        CHECK (role IN (
            'primary', 'protocol', 'statistical_analysis_plan', 'follow_up',
            'subgroup', 'combined_report', 'correction', 'retraction', 'other'
        )),
    reviewer TEXT,
    reviewed_at TEXT,
    PRIMARY KEY (study_id, paper_id)
);

CREATE TABLE IF NOT EXISTS paper_extractions (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    extraction_run_id TEXT NOT NULL,
    extraction_json TEXT NOT NULL,
    second_model TEXT NOT NULL,
    second_run_id TEXT NOT NULL,
    second_extraction_json TEXT NOT NULL,
    check_model TEXT NOT NULL,
    check_run_id TEXT NOT NULL,
    consistency_status TEXT NOT NULL
        CHECK (consistency_status IN ('consistent', 'needs_review')),
    consistency_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (paper_id, extraction_run_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    extraction_id TEXT NOT NULL REFERENCES paper_extractions(id) ON DELETE CASCADE,
    result_id TEXT REFERENCES results(id),
    candidate_text TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    locator TEXT NOT NULL,
    candidate_claim_type TEXT NOT NULL DEFAULT 'other'
        CHECK (candidate_claim_type IN (
            'association', 'intervention_effect', 'prevalence', 'mechanism', 'safety', 'other'
        )),
    candidate_study_design TEXT,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'reviewed', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id),
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    extraction_id TEXT NOT NULL REFERENCES paper_extractions(id) ON DELETE CASCADE,
    population TEXT NOT NULL,
    baseline_nutrient_status TEXT NOT NULL,
    ingredient_name TEXT NOT NULL,
    ingredient_form TEXT NOT NULL,
    dose TEXT NOT NULL,
    comparator TEXT NOT NULL,
    outcome TEXT NOT NULL,
    timepoint TEXT NOT NULL,
    effect_estimate TEXT NOT NULL,
    statistical_details TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    locator TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'reviewed', 'rejected', 'not_reported')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_reviews (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    corrected_text TEXT,
    corrected_study_design TEXT CHECK (corrected_study_design IN (
        'randomized_controlled_trial', 'systematic_review_meta_analysis',
        'cohort_study', 'case_control_study', 'cross_sectional_study',
        'controlled_feeding_metabolic_study', 'bioavailability_pharmacokinetic_study',
        'biomarker_validation_study', 'non_randomized_controlled_study',
        'natural_experiment', 'ecological_study', 'animal_study', 'in_vitro_study',
        'case_series', 'case_report', 'guideline', 'other', 'uncertain'
    )),
    inference TEXT CHECK (inference IN ('causal', 'associational', 'descriptive')),
    risk_of_bias_json TEXT,
    applicability TEXT,
    condition_code TEXT REFERENCES conditions(code),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    CHECK (decision = 'rejected' OR (
        corrected_text IS NOT NULL AND corrected_study_design IS NOT NULL
        AND trim(corrected_text) <> '' AND trim(corrected_study_design) <> ''
        AND inference IS NOT NULL AND risk_of_bias_json IS NOT NULL
        AND applicability IS NOT NULL AND trim(applicability) <> ''
        AND condition_code IS NOT NULL
    ))
);

CREATE TABLE IF NOT EXISTS evidence_profiles (
    id TEXT PRIMARY KEY,
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    version TEXT NOT NULL,
    ingredient_name TEXT NOT NULL,
    ingredient_form TEXT NOT NULL,
    population TEXT NOT NULL,
    baseline_nutrient_status TEXT NOT NULL,
    dose TEXT NOT NULL,
    comparator TEXT NOT NULL,
    outcome TEXT NOT NULL,
    timepoint TEXT NOT NULL,
    estimate_target TEXT NOT NULL,
    evidence_body_complete INTEGER NOT NULL CHECK (evidence_body_complete = 1),
    certainty TEXT NOT NULL CHECK (certainty IN ('high', 'moderate', 'low', 'very_low')),
    certainty_rationale TEXT NOT NULL,
    evidence_cutoff_date TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (condition_code, version)
);

CREATE TABLE IF NOT EXISTS evidence_profile_results (
    profile_id TEXT NOT NULL REFERENCES evidence_profiles(id) ON DELETE CASCADE,
    result_id TEXT NOT NULL REFERENCES results(id),
    interpretation TEXT NOT NULL
        CHECK (interpretation IN (
            'supports', 'does_not_support', 'mixed', 'uncertain', 'not_reported'
        )),
    PRIMARY KEY (profile_id, result_id)
);

CREATE TABLE IF NOT EXISTS knowledge_cards (
    id TEXT PRIMARY KEY,
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    version TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('draft', 'in_review', 'approved', 'published', 'rejected', 'stale')),
    grade TEXT CHECK (grade IN ('high', 'moderate', 'low', 'very_low')),
    evidence_profile_id TEXT REFERENCES evidence_profiles(id),
    reviewer TEXT,
    reviewed_at TEXT,
    published_at TEXT,
    patient_visible_body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK (status <> 'published' OR (
        grade IS NOT NULL AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL
        AND evidence_profile_id IS NOT NULL
        AND published_at IS NOT NULL AND trim(patient_visible_body) <> ''
    )),
    UNIQUE (condition_code, version)
);

CREATE TABLE IF NOT EXISTS card_claims (
    card_id TEXT NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    evidence_text TEXT NOT NULL,
    locator TEXT NOT NULL,
    PRIMARY KEY (card_id, claim_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    access_token_hash TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN (
            'uploaded', 'extracted', 'pending_confirmation',
            'confirmed', 'assessed', 'abandoned'
        )),
    subject_consistency TEXT
        CHECK (subject_consistency IN ('same', 'uncertain', 'different')),
    extraction_provider TEXT,
    extraction_model TEXT,
    extraction_run_id TEXT,
    extraction_warnings_json TEXT NOT NULL DEFAULT '[]',
    inferred_age INTEGER,
    inferred_sex TEXT CHECK (inferred_sex IN ('male', 'female', 'unknown')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_files (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    file_index INTEGER NOT NULL CHECK (file_index >= 1),
    original_name TEXT NOT NULL,
    object_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT '',
    page_count INTEGER,
    PRIMARY KEY (report_id, file_index)
);

CREATE TABLE IF NOT EXISTS report_observations (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    source_file_index INTEGER NOT NULL,
    source_page INTEGER NOT NULL CHECK (source_page >= 1),
    original_name TEXT NOT NULL,
    model_value REAL NOT NULL,
    model_unit TEXT NOT NULL,
    reference_low REAL,
    reference_high REAL,
    model_flag TEXT NOT NULL CHECK (model_flag IN ('high', 'low', 'normal', 'unknown')),
    evidence_text TEXT NOT NULL,
    extraction_status TEXT NOT NULL CHECK (extraction_status IN ('clear', 'ambiguous')),
    default_decision TEXT NOT NULL CHECK (default_decision IN ('pending', 'excluded')),
    validation_issues_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (report_id, source_file_index)
        REFERENCES report_files(report_id, file_index)
);

CREATE TABLE IF NOT EXISTS observation_confirmations (
    observation_id TEXT PRIMARY KEY REFERENCES report_observations(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'corrected', 'excluded')),
    final_metric_code TEXT,
    final_value REAL,
    final_unit TEXT,
    final_reference_low REAL,
    final_reference_high REAL,
    confirmed_at TEXT NOT NULL,
    CHECK (
        (decision = 'excluded' AND final_metric_code IS NULL AND final_value IS NULL
            AND final_unit IS NULL AND final_reference_low IS NULL
            AND final_reference_high IS NULL)
        OR
        (decision <> 'excluded' AND final_metric_code IS NOT NULL
            AND final_value IS NOT NULL AND final_unit IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
    sorting_version TEXT NOT NULL,
    unmatched_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessment_findings (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    card_id TEXT NOT NULL REFERENCES knowledge_cards(id),
    card_version TEXT NOT NULL,
    source_observation_ids_json TEXT NOT NULL,
    urgency TEXT NOT NULL,
    abnormality_severity INTEGER NOT NULL,
    evidence_strength TEXT NOT NULL,
    needs_recheck INTEGER NOT NULL,
    department TEXT NOT NULL,
    epidemiology_background TEXT NOT NULL DEFAULT '',
    sort_position INTEGER NOT NULL,
    sorting_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
