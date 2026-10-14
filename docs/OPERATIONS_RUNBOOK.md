# Operations runbook

## Start and health

Start the web process and the optional central worker separately with the same `--state` database. Check `/central` for capability state, last scan, last error, and observed artifacts. The worker should run under a managed service account with read-only access to Radius/VAN folders and write access only to local state/log paths. Multiple workers use per-capability leases but multi-node behavior is **TO VERIFY** before enterprise deployment.

## VAN outbound 856

Register the partner and outbound 856 VAN capability. Verify Radius message activation and exact outbound path/name contract with the Radius owner; record approved root in `EDI_CENTRAL_ALLOWED_ROOTS`. A file appears first as a stability candidate, then as a `central_artifacts` record with `PICKUP_UNVERIFIED`. Never move/delete that Radius file as part of observation. To establish pickup or acknowledgment, obtain a separate provider receipt and implement a validated correlation adapter; no such adapter exists yet.

## Legacy 850 exceptions

Open file history for exact SO/SR POST responses. `state/radius-debug.log` contains decoded request/response exchanges if debug is enabled. A definitive SO rejection leaves no created SO in the application's current workflow; a transport timeout or partial SR creation needs manual Radius reconciliation before retry. Update submissions are held. A create-new resubmission can create duplicate business orders and must be deliberately selected.

## Backup and recovery

Back up `config.json`, the SQLite state file (with a consistent SQLite backup), archived original files, and restricted debug logs according to retention policy. Restore into a TEST copy before validation. Do not copy production customer data into public fixtures. On inaccessible Radius/VAN folders, pause the capability, correct service-account/path access, then resume; the observer never consumes the source file. For a schema upgrade, back up state and retain the previous app package for rollback.
