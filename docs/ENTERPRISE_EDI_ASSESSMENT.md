# Radius EDI Central: application assessment

Reviewed 2026-10-15. This review is based on opened source files in this repository. The workspace has no `AGENTS.md`; `Radius_EDI_Central_AGENTS.md` is the available project instruction file. Existing notes in `ASSESSMENT.md`, `GAP_ANALYSIS.md`, and related docs provide context, but the findings below were checked against current code.

## A. Architecture and stack

Python 3.10+, Flask/Waitress, SQLite, `requests`, and `pyodbc` are used (`requirements.txt`, `niagara_edi/__main__.py`, `dashboard.py`). The CLI can process one file, poll an app-owned inbox, serve the web app, or run the independent central observer worker. `edi.py` and `fixed850.py` parse the supported inbound 850 variants. `service.py` validates and posts Sales Orders then Shipping Requests using `radius.py`; `audit.py` saves file status, events, and Radius request/response records. `customer_config.py` stores legacy profiles in JSON. `central.py` owns SQLite-backed partners, independent capabilities, folder observation, leases, artifacts, and events. `mapping_engine.py`, `mappings.py`, and `mapping_api.py` provide versioned mapping, sample preview, and the TEST direct API bridge. `outbound856.py` and `radius856_monitor.py` provide distinct 856 file parsing and Radius database monitoring.

SQLite has two overlapping histories: `files`/`file_events`/`radius_records` for the legacy posting path, and `central_artifacts`/`central_documents`/`central_events` for central observation. Migrations 001-004 add partners, capabilities, outbound 856 detail, Radius 856 monitoring, and mapping revisions/runs. There is no common transaction lifecycle covering all stages.

## B. Current screens and disposition

| Current screen / route | Current use | Disposition |
| --- | --- | --- |
| `/central` | Partner/capability summary and recent observed artifacts | Retain as Enterprise Overview; add verified enterprise metrics later |
| `/` | Legacy 850 counts, trend, recent files | Retain as Inbound 850 Overview; avoid presenting it as enterprise totals |
| `/central/transactions` | Central observed artifacts and document references | Retain and enhance into unified EDI Monitor |
| `/files/<id>` and `/#files` | Legacy posting audit and file history | Retain; link to unified monitoring when models converge |
| `/central/856-monitor` | Radius 856 processing records from configured source | Retain under EDI Monitor; status is Radius processing, not customer delivery |
| `/central/artifacts/<id>` | Observed file detail | Retain and enhance with lineage |
| `/central/partners/<id>` | Partner and per-message capability setup | Retain under Customers & Partners |
| `/customers` and `/customers/<kind>/<key>` | Legacy JSON customer profiles | Retain under Customers & Partners; reconcile with central partner model |
| `/central/capabilities/<id>` | Route/contract settings and observation controls | Retain under EDI Configuration |
| `/central/mappings` and editor | Versioned mapping rules, previews, draft/publish | Retain under Mapping Studio; master data tables still needed |
| `/central/850-inspect` | Diagnostic fixed 850 inspection | Relocate under Mapping Studio |
| `/central/856-preview` | Read-only outbound sample preview | Relocate under Mapping Studio |
| `/inbound` | Legacy manual 850 preview/TEST submission | Retain under EDI Operations |
| `/central/exceptions` | Observer exceptions | Retain under EDI Operations; merge with posting failures later |
| `/central/jobs` | Observer jobs/leases | Retain under EDI Operations; it is not a scheduler |

The layout is `templates/admin_base.html` plus `static/dashboard.css`. Before this increment, its left menu was a flat list of 12 links. All pages use the existing authenticated Flask app. There is no separate frontend framework.

## C. Implemented, partial, missing

| Area | Current state | Evidence / limit |
| --- | --- | --- |
| Multi-customer configuration | Partial | Central partners/capabilities are scoped; legacy posting still uses JSON profiles |
| Niagara/PEP inbound 850 | Implemented for supported fixed file and X12 subset | `fixed850.py`, `edi.py`, `service.py`, tests; Niagara sample mapping remains draft and incomplete |
| Radius posting | Implemented for TEST SO then SR REST flow | `radius.py`, `service.py`; updates are held, other document types not implemented |
| VAN observation | Implemented, read only | `central.py` stable scans, digest and leases; does not prove pickup or XLink result |
| Outbound 856 | Partial | Parser, preview, and Radius 856 monitor exist; no approved customer delivery adapter |
| Mapping | Partial | Versioned CSV/JSON/X12 subset/fixed 850 input and 856 preview; no general XML/Excel/EDIFACT adapters, no shared master data tables |
| Transaction monitoring | Partial | Central artifacts and legacy files are separate; no common document correlation or ack lifecycle |
| Operations | Partial | Manual 850, exceptions, observer jobs, replay protections; no durable cross-capability queue/scheduler |
| Security | Partial | HTTP Basic + CSRF and loopback default; no RBAC/SSO, environment-specific role policy, or credential vault |
| Reporting/alerting | Missing | No enterprise reporting, SLA, notifications, or verified delivery receipts |

## D. Reusable components and risks

Reuse the parsers, SO/SR payload builders, REST client, ODBC read-only resolver, audit/idempotency logic, central capability registry, observer leases, mapping rules/version proof, and existing templates. Keep legacy ingestion confined to app-owned inboxes: `__main__.py` moves submitted/rejected files and must never scan a Radius/VAN-owned folder. The central observer only reads files. A Radius outbound file appearing in a folder is not evidence of VAN pickup, customer delivery, or business acceptance.

Key risks: the two customer stores and two transaction histories can disagree; Basic auth cannot enforce the requested roles; Radius per-customer EDI/XLink settings and partner contracts remain unverified; direct customer intake and outbound delivery lack safe publication/receipt workflows. No LIVE Radius data or configuration was read or changed for this review.

## E-H. Direction, database, priority, dependencies

Use the existing Flask/SQLite application and consolidate lineage incrementally. Introduce a document-level correlation model linked to existing `files` and `central_artifacts`; add stage-specific evidence, attempts, acknowledgments, and outbound deliveries only as real adapters are built. Keep per-capability environment, direction, route class, path, format, and mapping independent. The immediate menu structure is in `MENU_REORGANIZATION_PLAN.md`; the source-grounded feature matrix and ordered roadmap are in `FEATURE_GAP_ANALYSIS.md` and `IMPLEMENTATION_ROADMAP.md`.

**Blockers to activation:** approved Radius message settings and XLink contract per capability; folder ownership and worker permissions; Niagara address/item/unit/date mappings; partner outbound format/delivery guides; enterprise identity and secret-management standard. Record evidence before enabling a processing path.
