CREATE TABLE central_mapping_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id INTEGER NOT NULL REFERENCES central_partners(id),
    name TEXT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('TEST','LIVE')),
    direction TEXT NOT NULL CHECK(direction IN ('INBOUND','OUTBOUND')),
    message_type TEXT NOT NULL,
    source_format TEXT NOT NULL,
    target_format TEXT NOT NULL,
    shipping_request_mode TEXT NOT NULL DEFAULT 'combined' CHECK(shipping_request_mode IN ('combined','per_line')),
    created_at TEXT NOT NULL,
    UNIQUE(partner_id,environment,name)
);
CREATE TABLE central_mapping_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES central_mapping_profiles(id),
    revision INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','PUBLISHED')),
    rules_json TEXT NOT NULL,
    rules_digest TEXT NOT NULL,
    edit_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    published_by TEXT,
    UNIQUE(profile_id,revision)
);
CREATE TABLE central_mapping_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL REFERENCES central_mapping_revisions(id),
    rules_digest TEXT NOT NULL,
    scope_digest TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    output_digest TEXT,
    row_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL
);
CREATE INDEX idx_mapping_profiles_partner ON central_mapping_profiles(partner_id,environment,direction);
CREATE INDEX idx_mapping_runs_revision ON central_mapping_runs(revision_id,id DESC);
CREATE TABLE central_mapping_file_links (
    file_id INTEGER PRIMARY KEY REFERENCES files(id),
    revision_id INTEGER NOT NULL REFERENCES central_mapping_revisions(id),
    rules_digest TEXT NOT NULL,
    scope_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);
