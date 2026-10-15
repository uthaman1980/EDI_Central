# EDI database design

Current schema is in `audit.py` and migrations `001_central.sql` through `004_mappings.sql`. `files`, `file_events`, `radius_records`, and `orders` support legacy posting. `central_partners`, `central_capabilities`, `central_artifacts`, `central_documents`, `central_document_lines`, `central_events`, `central_worker_leases`, Radius 856 monitor tables, and mapping profiles/revisions/runs support the newer workspace. Do not replace these tables in place or discard history.

## Planned additive model

| Entity | Key and relationship | Required constraint/evidence |
| --- | --- | --- |
| `edi_transactions` | Correlation ID; FK to capability and partner; optional FK to source `files` or `central_artifacts` | Environment, direction, EDI type, route, document identity and separate lifecycle statuses |
| `edi_artifact_links` | FK transaction and source/generated artifact | Role, digest, immutable path/size/time; one source file may link many documents |
| `edi_stage_events` | FK transaction; ordered event ID | Stage, outcome, timestamp, actor/worker, evidence reference; append only |
| `edi_control_numbers` | FK transaction | ISA13, GS06, ST02 and partner/environment/direction scoped uniqueness where applicable |
| `edi_delivery_attempts` | FK transaction | Destination, attempt token, status, receipt proof and idempotency key |
| `edi_acknowledgments` | FK transaction | Type 997/999/partner receipt, control-number correlation and actual artifact proof |
| `edi_master_xrefs` | FK partner/capability scope | Source code, verified target, effective interval, active state, uniqueness, audit |
| `edi_jobs`/`edi_runs` | FK capability, claim owner and lease | Schedule, attempt, backoff, result, crash recovery and unique claim |
| `edi_users`/`edi_roles`/`edi_audit` | Identity and authorization | Backend permission check and immutable sensitive-operation record |

Make migrations additive with foreign keys and indexes on customer, environment, type, direction, status, PO, Radius references, control numbers, and timestamps. Define UTC storage and client time-zone rendering. Do not populate business references from a filename or unverified Radius output. Store secret references, never plaintext credentials. Migrate legacy rows by linkage with provenance; do not synthesize missing document or delivery results. No Radius schema changes or direct Radius business-table writes are part of this plan.
