# Radius EDI Central

Radius EDI Central provides a partner/capability workspace and a read-only observer for verified VAN folders, including the user-authorized LIVE Niagara outbound 856 folder. The existing inbound 850 REST creation adapter remains in place with its Sales Order then Shipping Request posting sequence. Other translators and Radius flat-file handoffs are draft-only until their contracts are verified. The supplied Radius flat-file specifications are indexed in [docs/RADIUS_EDI_FORMATS.md](docs/RADIUS_EDI_FORMATS.md). See [docs/ASSESSMENT.md](docs/ASSESSMENT.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/GAP_ANALYSIS.md](docs/GAP_ANALYSIS.md) for scope and blockers.

**Sales Order and Shipping Request updates are on hold** while the Radius price-group behavior is investigated. New order creation, preview, response logging, and saved Radius cross references remain available.

Use **Inspect inbound 850** at `/central/850-inspect` to inspect a fixed-width PO before customer setup, including incomplete files. The Niagara sample's header/customer and missing mappings/defaults are documented in [docs/NIAGARA_INBOUND_850.md](docs/NIAGARA_INBOUND_850.md). Inspection does not submit an order or replace missing source values.

**Radius 856 monitor** at `/central/856-monitor` now reads `PUB.PV_XLShippingNotes` as the primary 856 processing history, so records remain visible after source files move. TEST company 2 is configured through `Radius_NewLive` across all customers. The page filters Processed OK, Not processed, and Rejected, and retains observed changes. LIVE database access is separate and awaits confirmed connection settings. See [docs/RADIUS_856_DATABASE_MONITOR.md](docs/RADIUS_856_DATABASE_MONITOR.md).

This application has two inbound routes and one Radius posting workflow:

1. **Direct customer 850 (Phase 1):** an X12 004010 850 received through a verified AS2 gateway or controlled file drop. An AS2 route requires `inbox/<id>.edi` and its `<id>.edi.as2.json` verification receipt. A controlled file drop needs a restricted inbox writable only by the approved receiving service. The existing parser currently implements the supplied Niagara 850 rules. Each additional customer whose segment conventions differ needs its own validated parser mapping.
2. **VAN transposed 850 (Phase 2):** a fixed-width `EDI850Inbound` `.txt` file in the layout of `EDI850StandardFormat.xls`, such as `PEP0185080209743383.txt`. This route needs no AS2 sidecar. It uses `SOHEAD`/`SOLINE` and `SRHEAD`/`SRLINE` records, validates `TOTALS`, and joins shipping lines to sales lines by customer PO line reference.

Both routes create a Radius Sales Order (`XLinkAPISOrder`) followed by Shipping Requests (`XLinkAPIShipReq`). Each customer can use one Shipping Request for all lines or one per line; the PEP01 profile uses one per line. Files move to `processed/` only after the Sales Order and every Shipping Request are confirmed. Failed or uncertain files move to `failed/` for review; a timeout after a POST must be reconciled in Radius before retrying. Preview mode leaves files in `inbox/`.

## Setup

Install Python 3.10+ and `pip install -r requirements.txt`. The web app creates `config.json` when you save the first customer, or you can copy `config.example.json` to `config.json`. Set `environment` to `TEST`. `RADIUS_API_URL` overrides the configured REST endpoint. VAN address references always use the Radius ODBC lookup. Set `RADIUS_DB_PASS` and optionally `RADIUS_DSN` and `RADIUS_DB_USER` before previewing or submitting VAN files. Direct X12 routes can use their configured address map or the same ODBC lookup.

The supplied TEST ODBC setup is DSN `Radius_NewLive`, host `P-RAD-FSM-DB`, port `3500`, database `vision`, user `vision`. Supply `RADIUS_DB_PASS` as an environment secret or at the interactive prompt. The configured TEST REST endpoint is `http://P-RAD-FSM-EF.fsm.corp:8081/radadapter/radius/api`; ODBC is used only for address lookup, while Sales Orders and Shipping Requests use REST.

## Web application

On Windows, double-click `run-web.bat` from this folder. It loads `run-web.local.bat` when present. This ignored local file preserves the workstation's configured login and TEST ODBC settings without startup prompts. On a fresh clone, copy `run-web.local.example.bat` to `run-web.local.bat` and enter credentials locally, or supply environment variables. Keep its console window open while using the app. Install dependencies first with `python -m pip install -r requirements.txt`.

For a managed, noninteractive start, set administrator credentials and run:

```powershell
$env:DASHBOARD_USER = 'edi-admin'
$env:DASHBOARD_PASSWORD = '<strong unique password>'
$env:RADIUS_DB_PASS = '<Radius ODBC password>'
python -m niagara_edi --dashboard --config config.json --state state/orders.sqlite3
```

Open `http://127.0.0.1:8088/central` for the partner/capability workspace; the existing 850 overview remains at `http://127.0.0.1:8088/`. **Customers** maintains each customer's input format, arrival method, partner identifiers, unit mappings, and Shipping Request mode. Direct X12 routes also have item and optional address maps. A direct customer X12 route uses the ISA sender and either a verified AS2 receipt or a controlled file drop. A VAN transposed route uses `SOHEAD.CustCode` and resolves the file's billing and shipping location codes through Radius ODBC. Save profiles as disabled drafts until the mappings are complete. **Process a file** can preview a file or submit it to Radius TEST; AS2 routes also require the gateway's verified `.as2.json` receipt. Web submission refuses a `LIVE` environment.

The web app binds to loopback by default and uses HTTP Basic authentication and a form token. For remote operators, place it behind an HTTPS reverse proxy with network access controls. The watcher reloads `config.json` every poll, so customer edits take effect without a restart. Preview and manual submission files stage outside the watched inbox.

For each VAN customer, add a `customers` entry keyed by the file's `SOHEAD.CustCode`. The profile supplies the Radius `cust_code`, `comp_num`, `plant_code`, and order and price unit mappings. The supplied PEP01 file routes to Radius customer PEP01; other customer profiles can use a different Radius code when required. The application reads `SOHEAD.BillAddressRef` and `SOHEAD.ShipAddressRef` and looks each up in `PUB.PV_CustAddress.AddressRef`, filtered by `CompNum` and the Radius `CustCode`. It requires exactly one matching address for each reference; no VAN address map is needed. The PEP01 sample uses `US01` for billing and `1K15` for shipping. Customer item references are read from the received file. The application sends each value as `CustItemRef` on the Sales Order line; Radius resolves its `ItemCode`. Its `EA` order unit and `THOUS` price unit map to those same Radius codes in the PEP example profile. Blank unit mappings prevent posting.

For each direct X12 partner, add an `x12_partners` entry keyed by ISA sender. Its customer profile supplies the Radius customer, company, plant, item qualifier, item mapping, and unit mappings. Set ISA identifiers and the arrival method. AS2 routes require a gateway receipt with verified authentication, decryption status, MIC, and SHA-256 digest. The gateway is responsible for signature verification, decryption, and MDN handling.

## Run

To run approved read-only VAN folder observers, configure `EDI_CENTRAL_ALLOWED_ROOTS` as a semicolon-separated list of absolute folders accessible to the worker account. In EDI Central, register a partner, add a VAN capability, enter the Radius inbound or outbound folder, record verified Radius activation and folder contract, enable the partner, then enable observation. Start a separate worker:

```powershell
$env:EDI_CENTRAL_ALLOWED_ROOTS = 'D:\ApprovedRadiusTest;\\test-server\radius-edi'
python -m niagara_edi --central-worker --state state/orders.sqlite3 --interval 10
```

The observer takes two stable scans before recording a file. It never moves, rewrites, sends, or translates Radius/VAN files. An outbound observation remains `PICKUP_UNVERIFIED`. User-authorized LIVE Niagara 856 monitoring is configured as described in [docs/NIA01_856_MONITOR.md](docs/NIA01_856_MONITOR.md). `run-web.bat` starts its observer alongside the dashboard. Direct translation remains unavailable. The sidebar's **Preview outbound 856** page can read a sample without creating a monitoring record; observed files show indexed BOL/SR/SO/PO references, and Transactions search includes those references.

```powershell
python -m niagara_edi PEP0185080209743383.txt --config config.json
python -m niagara_edi --watch inbox --config config.json --submit
```

The first command generates a Sales Order preview and makes no API call. The watcher accepts `.txt` VAN files and `.edi` X12 files. It processes all orders in a file and moves the file to `processed/` only when all Sales Orders and Shipping Requests succeed. Use a managed service for continuous polling. The web app and watcher must use the same `--state` SQLite file.

In the web app, a preview can be submitted directly from the preview result without uploading again. Rejected files can be retried from file history when no Radius POST remains uncertain. For a completed PO, **Create new Radius records** requests new numbers. The update code is preserved but blocked in the UI and backend pending Radius investigation. A unique attempt token makes a repeated new-record submission return its saved result. An uncertain attempt requires reconciliation before another submission.

To inspect a Radius rejection, run the watcher or a one-file submission with `--debug`. The log is written to `state/radius-debug.log` by default; `--debug-log PATH` chooses another location and also enables DEBUG mode. `run-web.bat` enables this mode for web submissions. Each exchange has a matching request ID and shows the decoded POST payload, decoded Radius response, and all nested error messages. Logs rotate at 10 MB with three backups and can contain customer order and address data, so keep the log directory restricted.

## Mapping used for VAN files

The positions below are one-based, as in `EDI850StandardFormat.xls`. Dates are `DDMMYYYY` in the file and ISO dates in Radius payloads.

| Record | Source | Radius field |
| --- | --- | --- |
| SOHEAD | `SOrderDate` 30, `CustOrderDate` 38, `CustCode` 46, `CustRef` 157, `TermsCode` 214, `CustReqDate` 222, `CurrCode` 230, `AddtCustRef` 537 | Customer order and required dates, customer profile, PO reference, terms and currency; `SOrderDate` is omitted so Radius defaults it to today. The Sales Order payload sends `SOSourceCode` as `EDI`. |
| SOHEAD | `BillAddressRef` 73, `ShipAddressRef` 87 | Look up each `AddressRef` in Radius and send the resulting `BillAddrNum`, `ShipAddrNum` |
| SOLINE | `CustItemRef` 68, `OrderedQty` 98, `OrderUnitCode` 110, `SOrderLineNum` 850 | `CustItemRef`, `OrderedQty`, `OrderUnitCode`; each API line also carries `CompNum`, `PlantCode`, and new-order `SOrderNum`; sequential Radius line number when blank; Radius resolves `ItemCode` |
| SOLINE | `FCUnitPrice` 120, `PriceUnitCode` 133, `ReqDate` 60, `CustPOLineRef` 862 | Price, price unit, required date, customer PO line (10, 20, 30, 40, 50 in the sample) |
| SRHEAD | `ShipDate` 45 | Shipping Request `ShipDate` |
| SRLINE | `ShipQty` 170, `CustRef` 455, `ShipCustPOLineNum` 528 | Shipping quantity and reference matched to its SO line; the Shipping Request uses the resolved `ItemCode` returned by Radius |

The sample file has one PEP01 order, five lines, and a ship date of October 7, 2026. `TOTALS.NumOrders` counts SO and SR headers together. The parser checks record count, matching customer and address references, line identity, positive quantities, and ship quantity no greater than order quantity. For previews and new Radius submissions, past customer required, line required, and shipping dates become the processing server's current local date in the Radius payload. Set the server's time zone to Central Time for this workflow. Each replacement is recorded in file history with its original and new date. The received EDI file is retained unchanged for audit and digest verification; future dates and historical order dates remain as supplied.

## Posting and recovery

SQLite stores the business key, input digest, posting state, and all Radius numbers. Each confirmed Sales Order and Shipping Request POST also saves its request payload, decoded response record, PO line reference when available, and Radius numbers. Open the file detail page from a submission result or file history to inspect these records; file history searches PO, Sales Order, and Shipping Request numbers. Completed replays return their existing numbers. A changed PO or any uncertain result after posting starts is held for manual reconciliation to avoid a duplicate Sales Order or Shipping Request. A definite Radius Sales Order rejection clears its pending attempt so a corrected file can be submitted; a transport error remains pending for reconciliation. For one-request-per-line customers, each confirmed request number is saved immediately; a later failure leaves the order in `ship_pending` for reconciliation. The Radius client decodes the Base64 response payload before reporting validation errors, including errors on individual lines. The watcher moves a rejected file to `failed/`; after correcting configuration or reconciling Radius state, reintroduce the file through a controlled operator process.
