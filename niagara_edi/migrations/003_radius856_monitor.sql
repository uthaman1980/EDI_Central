CREATE TABLE central_radius_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL CHECK(environment IN ('TEST','LIVE')),
    company_number INTEGER NOT NULL CHECK(company_number>0),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    poll_seconds INTEGER NOT NULL DEFAULT 60 CHECK(poll_seconds>=10),
    history_cursor INTEGER,
    recent_cursor INTEGER,
    history_sweeps INTEGER NOT NULL DEFAULT 0,
    last_scan_at TEXT,
    last_error TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    UNIQUE(environment,company_number)
);
CREATE TABLE central_radius_856_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES central_radius_sources(id),
    table_rec_id INTEGER NOT NULL,
    creation_date TEXT,
    creation_time TEXT,
    record_type INTEGER NOT NULL,
    record_status INTEGER NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    company_number INTEGER NOT NULL,
    plant_code TEXT NOT NULL,
    customer_code TEXT NOT NULL,
    ship_address_number INTEGER,
    shipreq_number INTEGER,
    file_name TEXT NOT NULL DEFAULT '',
    actual_ship_date TEXT,
    snapshot_digest TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    UNIQUE(source_id,table_rec_id)
);
CREATE TABLE central_radius_856_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES central_radius_856_records(id),
    observed_at TEXT NOT NULL,
    record_status INTEGER NOT NULL,
    error_message TEXT NOT NULL,
    file_name TEXT NOT NULL
);
CREATE INDEX idx_radius_856_customer ON central_radius_856_records(source_id,customer_code,creation_date);
CREATE INDEX idx_radius_856_status ON central_radius_856_records(record_status,changed_at);
CREATE INDEX idx_radius_856_shipreq ON central_radius_856_records(shipreq_number);
CREATE INDEX idx_radius_856_events ON central_radius_856_events(record_id,id);
