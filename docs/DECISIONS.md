# Decisions and blockers

| Date | Decision | Evidence / remaining work |
| --- | --- | --- |
| 2026-10-14 | Preserve current REST SO then SR creation adapter | Existing TEST successes and regression tests; do not refactor its payload sequence during central foundation work |
| 2026-10-14 | Hold SO/SR update path | Radius TEST status 9 on SO 261383 price group `PEPSI`, including an omitted-field POST; backend and UI block updates until Radius owner resolves it |
| 2026-10-14 | Prioritize VAN outbound 856 observation | User selected this as first new capability; current implementation is read-only and labels pickup unverified |
| 2026-10-14 | Keep unverified translators draft-only | Radius standard-format workbooks and a local 856 sample are available; Niagara's screenshot confirms Standard Outbound 856 option 1. Partner translation contracts remain unverified |
| 2026-10-14 | Index supplied Radius format workbooks | Eight inbound/outbound workbooks are in `RadiusEDIFormatFiles/`; the new 850 and 856 workbooks differ from root copies, so version drift is documented in `RADIUS_EDI_FORMATS.md` |
| 2026-10-14 | Use versioned SQLite migration beside legacy tables | Existing app already uses SQLite; target enterprise database standard remains **TO VERIFY** |
| 2026-10-14 | Make XLShippingNotes the primary 856 processing history | User confirmed files move after processing; supplied fields were verified through TEST DSN Radius_NewLive using SELECT only. Status 1 is Processed OK; provider pickup remains independent |
| 2026-10-14 | Inspect Niagara 850 before enabling its route | Supplied NIA-prefixed sample contains source customer BXZALLST and missing dates/currency/billing/price basis. TEST address and customer-item references do not match. Diagnostic inspection is implemented; posting waits for verified mappings/defaults and the missing SFTP path |

**Niagara VAN outbound 856 activated for observation:** user supplied the active `NIA01` configuration screenshot and explicitly authorized LIVE read-only monitoring. The approved share is accessible, and the first scan found no matching files. See [NIA01_856_MONITOR.md](NIA01_856_MONITOR.md) for exact settings, provenance, and remaining pickup-evidence gaps. No LIVE REST or XLink write is authorized by this monitoring permission.

**BLOCKER — enterprise deployment:** need SSO/RBAC standard, managed service account/secret vault, backup/retention policy, target database/hosting, Radius owner approval and LIVE change-control process.

**BLOCKER — direct translation:** need partner guide and sample, Radius flat-file version/sample, cross references, destination, and acknowledgment policy for each route. No path or schema is inferred from the tutorial alone.
