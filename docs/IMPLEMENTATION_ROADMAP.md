# Radius EDI Central implementation roadmap

This is the ordered checklist for the enterprise enhancement. Existing `MIGRATION_PLAN.md` covers legacy migration detail; this file tracks platform phases. Completion means code and relevant tests are present, not partner or Radius production sign-off.

| Phase | Status on 2026-10-15 | Exit evidence |
| --- | --- | --- |
| 0. Discovery and baseline | Complete for repository review | Source-grounded assessment, screen inventory, feature gaps, regression suite; Radius deployment facts remain blockers |
| 1. Navigation | Implemented | Collapsible grouped menu, existing routes retained, authenticated page test |
| 2. Customer/capability management | Partial | Existing registry and guarded observer activation; add unified customer identity, validated route setup, UAT and approval |
| 3. Monitoring foundation | Partial | Link legacy and central histories; file/document correlation, stage evidence, business reference search |
| 4. Mapping/master data | Partial | Existing versioned TEST mapper; shared verified cross references, activation pointer, additional approved adapters |
| 5. Operations/automation | Partial | Existing manual 850 and observer leases; durable queue, scheduler, reconciliation and safe retries |
| 6. Integration expansion | Planned | Contract-tested Radius XLink and direct outbound/delivery/ack adapters per approved customer |
| 7. Enterprise hardening | Planned | Backend RBAC, secrets, audit, health, alerting, reports, deployment/UAT gates |

## Next reviewable increments

1. Define a single customer identity and read model across central partners and legacy profiles. Preserve old keys and histories. Add migration and isolation tests.
2. Add document-level transaction IDs and explicit stage evidence linked to both existing artifact stores; build a unified search screen without inventing missing results.
3. Add configuration preflight for supported modes, including Radius-side setting evidence and environment/folder checks. Keep unverified capabilities disabled.
4. Complete Niagara's missing mapping values, validate a TEST payload, then perform a scoped UAT order only with verified source and business approval.
5. Build a durable direct intake worker and outbound publisher only after ownership, file contracts and delivery evidence are approved.

## Blocking decisions

Radius EDI/XLink configuration per customer and message; company/plant master-data verification; source/Radius/VAN folder ownership; Niagara date/address/item/UOM/price basis; partner format guides and acknowledgments; identity/secret hosting standard. Do not claim an unverified adapter or report observed files as delivered transactions.
