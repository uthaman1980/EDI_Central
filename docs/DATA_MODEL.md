# Data model

`niagara_edi/migrations/001_central.sql` is the first versioned central migration; `central_schema_migrations` records applied versions. The migration engine is idempotent and applies on web or worker startup. Existing 850 tables are left intact.

| Table | Purpose and key |
| --- | --- |
| `central_partners` | Radius customer/company/plant and partner identity; unique partner code; starts paused |
| `central_capabilities` | Partner + environment + direction + message + route; typed strategy/mode, distinct folder roles, verified status, schedule and errors |
| `central_observation_candidates` | First size/mtime sample for stable-file detection, keyed by capability and path |
| `central_artifacts` | Read-only observed file metadata/checksum/state, unique by capability + path + digest |
| `central_documents` | Parsed outbound 856 shipping notes with BOL, SR, Radius customer/company/plant, and source artifact |
| `central_document_lines` | Parsed shipping-note lines with SO/line and customer PO reference |
| `central_events` | Append-only central configuration/observation events |
| `central_worker_leases` | Scoped lease with owner and expiry to prevent overlapping observers |
| `central_radius_sources` | Per-environment/company read-only database monitor; enabled state, polling, sweep cursors, lease and errors |
| `central_radius_856_records` | Durable XLShippingNotes snapshots, unique source/TableRecId, across customers regardless of file retention |
| `central_radius_856_events` | Append-only observed snapshot changes with Radius processing status and error |

The legacy `files`, `file_events`, `orders`, and `radius_records` tables retain the working 850 file, error, SO/SR, and decoded POST/response history. A file artifact is not a document; an observed file is not proof of Radius posting, VAN pickup, or partner acceptance. Migration `002_outbound856.sql` adds independent artifact parsing status/error/version and 856 reference fields. The `RADIUS_856_OUT_V1` profile populates documents only after structure, totals, and configured customer/company/plant checks pass. Other format profiles stay opaque. Multiple SOs are recorded at line level, rather than assigning an arbitrary SO to a shipping note.

Retention and backups are **TO VERIFY** with operations. Do not purge existing `state/` or Radius debug logs during migration. SQLite must be backed up consistently using its backup API or a stopped service, including WAL files if WAL is enabled later.

Migration `003_radius856_monitor.sql` adds the database-backed 856 history. Its source ID includes environment and company; it is separate from file artifacts and does not manufacture a source file or checksum when only a Radius table row exists. See [RADIUS_856_DATABASE_MONITOR.md](RADIUS_856_DATABASE_MONITOR.md) for verified fields and polling behavior.
