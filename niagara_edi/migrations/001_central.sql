CREATE TABLE IF NOT EXISTS central_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS central_partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    radius_customer_code TEXT NOT NULL,
    company_number INTEGER NOT NULL CHECK(company_number > 0),
    plant_code TEXT NOT NULL,
    edi_identity TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS central_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id INTEGER NOT NULL REFERENCES central_partners(id),
    environment TEXT NOT NULL CHECK(environment IN ('TEST','LIVE')),
    direction TEXT NOT NULL CHECK(direction IN ('INBOUND','OUTBOUND')),
    message_type TEXT NOT NULL,
    route_class TEXT NOT NULL CHECK(route_class IN ('VAN','DIRECT')),
    strategy TEXT NOT NULL CHECK(strategy IN ('PASSTHROUGH_TRACK','TRANSLATE_TO_RADIUS','TRANSLATE_FROM_RADIUS')),
    operating_mode TEXT NOT NULL DEFAULT 'OBSERVE_ONLY' CHECK(operating_mode IN ('OBSERVE_ONLY','DRY_RUN','ACTIVE')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    radius_message_active INTEGER CHECK(radius_message_active IN (0,1)),
    radius_contract_verified INTEGER NOT NULL DEFAULT 0 CHECK(radius_contract_verified IN (0,1)),
    radius_format TEXT NOT NULL DEFAULT '',
    source_format TEXT NOT NULL DEFAULT '',
    destination_format TEXT NOT NULL DEFAULT '',
    format_version TEXT NOT NULL DEFAULT '',
    mapping_version TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'FOLDER',
    radius_inbound_folder TEXT NOT NULL DEFAULT '',
    radius_outbound_folder TEXT NOT NULL DEFAULT '',
    partner_inbound_folder TEXT NOT NULL DEFAULT '',
    partner_outbound_folder TEXT NOT NULL DEFAULT '',
    working_folder TEXT NOT NULL DEFAULT '',
    archive_folder TEXT NOT NULL DEFAULT '',
    quarantine_folder TEXT NOT NULL DEFAULT '',
    filename_pattern TEXT NOT NULL DEFAULT '*',
    poll_seconds INTEGER NOT NULL DEFAULT 60 CHECK(poll_seconds >= 5),
    last_scan_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(partner_id,environment,direction,message_type,route_class)
);

CREATE TABLE IF NOT EXISTS central_observation_candidates (
    capability_id INTEGER NOT NULL REFERENCES central_capabilities(id),
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY(capability_id,path)
);

CREATE TABLE IF NOT EXISTS central_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER NOT NULL REFERENCES central_capabilities(id),
    source_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    digest TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(capability_id,source_path,digest)
);

CREATE TABLE IF NOT EXISTS central_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id INTEGER NOT NULL REFERENCES central_artifacts(id),
    partner_document_id TEXT,
    radius_so_number INTEGER,
    radius_shipreq_number INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS central_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER REFERENCES central_capabilities(id),
    artifact_id INTEGER REFERENCES central_artifacts(id),
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS central_worker_leases (
    capability_id INTEGER PRIMARY KEY REFERENCES central_capabilities(id),
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_central_capabilities_enabled ON central_capabilities(enabled,environment,strategy);
CREATE INDEX IF NOT EXISTS idx_central_artifacts_capability ON central_artifacts(capability_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_central_events_capability ON central_events(capability_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_central_documents_artifact ON central_documents(artifact_id);
