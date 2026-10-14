# Radius 856 database monitor

`PUB.PV_XLShippingNotes` is the primary source for 856 **Radius processing history**. The user confirmed that processed files are moved away from the message folder and supplied the table field definitions. Folder observation remains supplemental evidence for source bytes and BOL/SO reference extraction; it cannot reconstruct files already collected by another process.

## Verified connection and fields

The user confirmed `Radius_NewLive` is **TEST**. A read-only connection to that DSN verified all projected fields on 2026-10-14:

`TableRecId`, `Direction`, `CreationDate`, `CreationTime`, `RecordType`, `RecordStatus`, `ErrorMessage`, `CompNum`, `PlantCode`, `CustCode`, `ShipAddrNum`, `ShipReqNum`, `ShippingType`, `FileName`, `ActualShipDate`.

The configured source is TEST company 2, across all its customers and plants. Queries explicitly require `Direction=0` (export) and `ShippingType=0` (EDI), excluding imports, fax, and email. The installed driver accepted a read-only ODBC connection. It does not implement the optional connection query-timeout attribute, so the reader does not set that attribute. Connection establishment has a 15-second timeout; a query may run longer depending on Radius/database availability.

The initial historical sweep indexed **12,926 records across 20 customers**. All had status 1, matching a separately queried TEST source count. This is a point-in-time verification, not a permanently fixed expected count. No LIVE SQL connection was used. LIVE database monitoring remains unconfigured until its DSN and credentials are confirmed; the previously authorized LIVE Niagara folder observer remains separate.

| RecordStatus | Display |
| --- | --- |
| 0 | Not processed |
| 1 | Processed OK |
| 9 | Rejected, with `ErrorMessage` |
| Other | Unknown status plus original value |

`Processed OK` is the Radius table status. It is not independent proof that a VAN collected a file or that a customer accepted it. The table fields verified here contain the SR and filename, but no direct BOL or SO fields; those references are shown only when an independently observed and parsed file matches environment, company, plant, customer, SR, and filename.

## Polling, history, and changes

`radius856_monitor.py` uses SELECT only on Radius. Each cycle reads up to 500 recent records and 500 historical records with keyset pagination by `TableRecId`. Recent means created in the last 30 days. Completed pages advance separate recent/history cursors; after reaching each range's end, its cursor resets so subsequent sweeps re-read existing records and detect status changes. This avoids assuming creation ID alone captures later updates. A historical sweep may take multiple polling cycles; older status changes are visible after their page is revisited. It does not provide instantaneous change capture.

Local SQLite retains a unique source/record-ID snapshot, first and last observation times, and an append-only event for each observed snapshot change. Unchanged polls do not duplicate events. Source deletion or file removal does not delete local history. The application cannot recover status transitions that occurred between polls or before first observation. A source lease prevents overlapping syncs. Failed reads preserve the previous snapshots and cursors, save an actionable error, and release the lease. Failed or out-of-scope batches are rolled back locally. Source credentials are not stored in SQLite.

The source is independent of partner capability activation because this is cross-customer historical monitoring. Pause it explicitly on the monitor page to stop database reads; pausing a trading partner only pauses that partner's capability jobs.

## Operator use

Restart `run-web.bat` and open `/central/856-monitor` through **Radius 856 monitor** in the sidebar. Filter by environment/company source, customer, status, creation date, SR, filename, or table record ID. Open an SR to inspect its Radius status, exact error, file path, observed changes, and any matching parsed files. The page shows 100 records per page. **Refresh view** reloads indexed data; **Sync now** reads the next bounded pages from Radius. Scheduled polling runs while the web launcher with `--observe` remains open, or through `--central-worker`.

The TEST source is enabled with a 60-second poll interval. The launcher sets `RADIUS_TEST_MONITOR_DSN=Radius_NewLive`, `RADIUS_TEST_MONITOR_USER=vision`, and the user-provided TEST password. Managed deployments can provide these variables separately. LIVE requires its own `RADIUS_LIVE_MONITOR_DSN`, `RADIUS_LIVE_MONITOR_USER`, and `RADIUS_LIVE_MONITOR_PASS`, plus explicit `EDI_CENTRAL_LIVE_OBSERVATION=1` authorization. LIVE never falls back to the TEST password or DSN.

Additional sources can be created paused for other companies/environments. Enabling a source requires configured connection settings. Its first sync verifies every projected field against the installed table before indexing records. This feature does not call Radius REST, XLink execution, or any Radius SQL write.
