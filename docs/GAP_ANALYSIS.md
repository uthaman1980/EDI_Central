# Gap analysis and delivery gates

| Area | Current evidence | Enterprise target / gate |
| --- | --- | --- |
| Partner master | JSON profiles split between fixed 850 and X12 850 | Versioned DB partner identity, company/plant, route-specific capabilities |
| Capability catalog | Only two inbound 850 parser routes | Typed inbound/outbound catalog; unverified formats stay planned/draft |
| Route ownership | One app-owned inbox, processed, failed folders | Separate partner landing, Radius inbound/outbound, customer delivery, work/archive/quarantine paths and explicit ownership |
| VAN pass-through | Current fixed file is parsed and posted through REST | New observer must not move or translate Radius/VAN-owned files; Radius outcome requires independent evidence |
| Direct middleware | Direct X12 maps to REST JSON | Verified customer-specific Radius flat-file writer, atomic publish, Radius result reconciliation are **BLOCKED** on deployed format/operation contract |
| Outbound | Radius format workbooks and one local outbound 856 sample are available; no reader, serializer, or delivery adapter | Direct outbound translation **BLOCKED** on installed format/version confirmation, partner guides, destination and trigger contracts; VAN 856 observation still needs verified Radius folder and activation |
| Work orchestration | Single polling loop with no leases or stable-file check | Per-capability schedule, lease, file stability, scoped worker runs, disabled-state enforcement |
| Audit/transactions | File and Radius records, no separate document entity | Immutable artifact, document, processing event, delivery and acknowledgment lineage |
| Authentication | Single HTTP Basic identity | Enterprise SSO and role-based server authorization; **TO VERIFY** hosting standard |
| Secrets | Batch file contains fixed credentials | Remove embedded credentials, use environment/secret references and access controls |
| Updates | UI offered SO/SR update; Radius price-group rejection reproduced | Put update functions on hold; resume only after Radius owner validates semantics |

Implementation should first establish DB migrations, typed partner/capability setup, read-only observer behavior, file/transaction trace, and a safe worker. Migrate the proven REST 850 creation path behind the capability boundary without changing its mapping or Radius POST sequence. Do not claim an outbound translator or Radius flat-file workflow is live until its contract tests and customer samples exist.
