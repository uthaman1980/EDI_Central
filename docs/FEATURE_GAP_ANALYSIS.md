# Enterprise feature gap analysis

Source review: `ENTERPRISE_EDI_ASSESSMENT.md`. Status means code exists in this repository, not Radius or partner production acceptance.

| Requirement | Status | Next evidence or work |
| --- | --- | --- |
| Independent partners and capabilities | Partial | Central registry supports company/plant, type, direction, route and folders; reconcile legacy JSON customer profiles |
| VAN inbound/outbound monitoring | Partial | Read-only stable-file observer exists; correlate verified Radius XLink and VAN pickup evidence |
| Direct inbound 850 | Partial | TEST manual API bridge exists; Niagara mapping blocked by source/master data; no approved unattended intake |
| Direct outbound 856 | Partial | Parser and CSV/JSON preview; customer format and delivery contract not approved |
| Other EDI types | Planned | Candidate catalog only; obtain Radius/partner layouts and test each adapter |
| Unified transaction monitoring | Partial | Legacy files and central artifacts are distinct; add document correlation and stage evidence |
| X12 control numbers/997/999 | Missing | Persist interchange/group/set IDs and actual acknowledgment artifacts when approved |
| Shared master data translation | Partial | Mapping rule lookups and legacy item/unit maps; add verified effective-dated reference tables |
| Capability activation validation | Partial | Folder, mode, Radius active/contract flags checked for observation; broader Radius and adapter checks needed |
| Queue, scheduler, retry, alerting | Missing | Observer loop and leases are not an enterprise scheduler or durable job queue |
| Reporting and exports | Missing | Define canonical transaction counts and time zones before statistics |
| RBAC, SSO, secrets | Missing | Basic auth and CSRF only; backend roles and secret store required before enterprise rollout |
| UAT/LIVE isolation | Partial | Central schema supports TEST/LIVE; UAT is absent; LIVE observation is gated, posting bridge TEST only |

Duplicate concepts: legacy `customers`/`x12_partners` in `config.json` versus `central_partners`, and `files` versus `central_artifacts`. Preserve both until a migration links records and regression/UAT proves parity. Hardcoded scope is concentrated in the legacy inbound 850 path; the central observer and mapping registry are partner-scoped. Do not report file observation as business posting or pickup.
