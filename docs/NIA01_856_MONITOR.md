# Niagara outbound 856 observer

**Primary monitoring now uses Radius table history:** the user confirmed that files move after processing. [RADIUS_856_DATABASE_MONITOR.md](RADIUS_856_DATABASE_MONITOR.md) describes the cross-customer table monitor, verified in TEST through `Radius_NewLive`. The LIVE folder observer below remains supporting evidence; its database connection has not been confirmed.

On 2026-10-14, the user supplied the Radius customer EDI setup screenshot and confirmed it is LIVE, explicitly authorizing read-only monitoring of that folder.

| Setting | Recorded value / evidence |
| --- | --- |
| Customer | `NIA01`, Niagara Bottling LLC, from Radius screenshot |
| Environment | LIVE, confirmed by user |
| Company / plant | `2` / `2`, from the supplied outbound 856 sample; parser rejects mismatches |
| Message | Outbound 856, active |
| Radius test mode | No, shown in screenshot; not altered by this application |
| Output level | Complete Shipment |
| Radius folder | `\\fsmradius\EFIRadius\EDI\VisionII` |
| Basename / extension | `856NIA01` / `txt` |
| Numbering | Unique filenames, six digits; screenshot next sequence `000473` is informational and is not managed by the application |
| Filename mask | `856NIA01[0-9][0-9][0-9][0-9][0-9][0-9].txt` |
| Radius format | `1 — Standard Outbound 856` |
| Application parser profile | `RADIUS_856_OUT_V1` |
| Polling | Every 30 seconds; two stable scans required |
| Pickup evidence | Unavailable; retain `PICKUP_UNVERIFIED` |

The partner and LIVE capability are saved in `state/orders.sqlite3`. A SQLite backup was made under `state/backups/` before migration. The initial read-only folder scan succeeded and found no matching 856 files; this does not establish whether the VAN collected earlier files. The application does not copy the local sample into the LIVE share to manufacture a detection.

`run-web.bat` sets the allowed root and explicit LIVE observation flag and starts the web application with `--observe`. Restart that launcher to load the new code. Monitoring runs while the launcher remains open. In the webpage, open EDI Central > Niagara Bottling LLC > OUTBOUND 856, then an observed file to see BOL, Shipping Request/line, Sales Order/line, and PO references. Transactions search includes these references. Format failures appear in Exceptions. Use **Preview outbound 856** to upload `856NIA01000471.txt` for an in-memory reference preview while the live folder is empty. That preview does not create a LIVE observation record.

Source files remain owned by Radius and its external pickup process. The observer lists and reads matching files, checks stability, and writes metadata/references only to the application's local SQLite state. It does not rename, move, edit, delete, translate, or send source files and does not call Radius REST/XLink. Filename masks reduce cross-customer discovery; parsed customer/company/plant checks prevent foreign references being indexed. No pickup or acceptance is inferred from file disappearance. Provider receipt integration and cleanup timing remain to be confirmed.

Pause the capability to stop scanning. Disable the partner to pause all its capabilities. Removing `EDI_CENTRAL_LIVE_OBSERVATION=1` blocks LIVE scans even if the capability is enabled. This setting does not permit LIVE order creation or updates.
