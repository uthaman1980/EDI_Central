# Configuration guide

## Local TEST startup

Install `requirements.txt`. Supply `DASHBOARD_USER`, `DASHBOARD_PASSWORD`, and `RADIUS_DB_PASS` through an approved secret source or interactive prompt; do not commit them. Start `python -m niagara_edi --dashboard --config config.json --state state/orders.sqlite3`. Open `/central` for partner and capability setup. The existing `/customers` and `/inbound` pages remain the working 850 adapter.

## Partner and capability

Register a partner with Radius customer/company/plant and EDI identity. New partners start paused. Add a direction/message/environment/route capability as a draft. `VAN` derives `PASSTHROUGH_TRACK`; `DIRECT` derives translation strategy. Enter the proper Radius inbound or outbound folder separately from partner landing/delivery folders. Record the actual Radius format/version, source/destination format, transport, mapping version, and filename pattern. Do not use a placeholder path as a verified path.

For the first prioritized **VAN outbound 856** observer, confirm the Radius customer EDI 856 message is active, its outbound file location and naming pattern, the folder owner, the worker service account's read access, and Radius cleanup timing. Set `EDI_CENTRAL_ALLOWED_ROOTS` to the approved absolute parent folder, enable the partner, mark the Radius contract verified, then enable observation. Start `python -m niagara_edi --central-worker --state state/orders.sqlite3 --interval 10`. The worker polls per capability and records only stable files. `PICKUP_UNVERIFIED` means the Radius file was seen, not that the VAN received it.

The user explicitly authorized read-only LIVE observation for Niagara outbound 856 on 2026-10-14. Its settings are documented in [NIA01_856_MONITOR.md](NIA01_856_MONITOR.md). LIVE observation requires `EDI_CENTRAL_LIVE_OBSERVATION=1` on activation and every scan, plus allowed roots and the existing verification gates. This authorizes file reads only. Direct translation and outbound delivery remain unavailable. Existing 850 REST creation is configured separately in `config.json`; its web submission remains TEST-only. SO/SR update requests remain blocked pending Radius investigation.
