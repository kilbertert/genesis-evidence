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
    status TEXT NOT NULL CHECK (status IN ('pending', 'admitted', 'rejected')),
    reviewer TEXT,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    candidate_text TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    locator TEXT NOT NULL,
    candidate_study_design TEXT,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'reviewed', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_reviews (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    corrected_text TEXT NOT NULL,
    corrected_study_design TEXT NOT NULL,
    grade TEXT NOT NULL CHECK (grade IN ('high', 'moderate', 'low', 'very_low')),
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_cards (
    id TEXT PRIMARY KEY,
    condition_code TEXT NOT NULL REFERENCES conditions(code),
    version TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('draft', 'in_review', 'approved', 'published', 'rejected', 'stale')),
    grade TEXT CHECK (grade IN ('high', 'moderate', 'low', 'very_low')),
    reviewer TEXT,
    reviewed_at TEXT,
    published_at TEXT,
    patient_visible_body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK (status <> 'published' OR (
        grade IS NOT NULL AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL
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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_files (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    file_index INTEGER NOT NULL CHECK (file_index >= 1),
    original_name TEXT NOT NULL,
    object_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page_count INTEGER,
    PRIMARY KEY (report_id, file_index)
);

CREATE TABLE IF NOT EXISTS report_observations (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    source_file_index INTEGER NOT NULL,
    source_page INTEGER,
    metric_code TEXT NOT NULL,
    original_name TEXT NOT NULL,
    model_value REAL NOT NULL,
    model_unit TEXT NOT NULL,
    reference_low REAL,
    reference_high REAL,
    evidence_text TEXT NOT NULL,
    extraction_status TEXT NOT NULL CHECK (extraction_status IN ('clear', 'ambiguous')),
    FOREIGN KEY (report_id, source_file_index)
        REFERENCES report_files(report_id, file_index)
);

CREATE TABLE IF NOT EXISTS observation_confirmations (
    observation_id TEXT PRIMARY KEY REFERENCES report_observations(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'corrected', 'excluded')),
    final_value REAL,
    final_unit TEXT,
    final_reference_low REAL,
    final_reference_high REAL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
    sorting_version TEXT NOT NULL,
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
