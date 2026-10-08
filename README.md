# Niagara 850 to Radius middleware

This application consumes an **AS2 gateway verified** X12 004010 Niagara 850, creates a Radius Sales Order with lines, then creates a Shipping Request with matching lines. It uses the Xlink REST endpoint supplied for TEST or LIVE. The default mode previews the Sales Order payload; `--submit` enables API calls.

## AS2 boundary

Deploy an AS2 gateway (for example, the organization's existing AS2 service) to receive HTTPS AS2 messages, verify Niagara's signing certificate, decrypt with the local private key when encrypted, verify the MIC, and generate the signed or unsigned MDN required by the Niagara trading agreement. The gateway must write the verified business payload to a restricted, local `inbox/<message-id>.edi` file and atomically write `inbox/<message-id>.edi.as2.json` after verification. The JSON must contain:

```json
{
  "as2_sender": "NIAGARA_AS2_ID",
  "as2_receiver": "OUR_AS2_ID",
  "authentication_status": "verified",
  "decryption_status": "verified",
  "mic_status": "verified",
  "sha256": "hex SHA-256 of the exact .edi bytes"
}
```

For unencrypted but verified AS2 messages, use `"decryption_status": "not_encrypted"` only when the trading agreement permits it. Protect the inbox and receipt so only the gateway service identity can write them. The receipt is a local gateway assertion, not an AS2 verifier; the gateway must perform cryptographic verification. Do not point the watcher at a general upload directory. Gateway product configuration, Niagara AS2 identifiers, certificates, MDN requirements, and HTTPS endpoint are trading partner inputs and are not in the 850 PDF.

## Configuration

1. Install Python 3.10+ and `pip install -r requirements.txt`.
2. Copy `config.example.json` to `config.json` and set item mappings, unit mappings, and allowed AS2/ISA identifiers. Company `2`, plant `2`, and customer `NIA01` are prefilled from the supplied information. The sample `IM`/`TH` unit codes are placeholders until confirmed in Radius. `VN` is the default source item qualifier; switch to `BP` if Niagara's part number is the Radius item key. Every source item must map explicitly.
3. Set `environment` to `TEST` or `LIVE`. `RADIUS_API_URL` can override the URL. The supplied TEST/LIVE ODBC DSN and user settings are used for read-only lookup of `PV_CustAddress.AddressRef` to `AddressNum`; API requests create the Sales Order and Shipping Request. Set `RADIUS_DB_PASS` in the process environment. `RADIUS_DSN` and `RADIUS_DB_USER` may override the ODBC defaults.
4. Run a preview: `python -m niagara_edi inbox/example.edi --config config.json`. The adjacent AS2 receipt is required even in preview.
5. After reviewing the payload, run `python -m niagara_edi --watch inbox --config config.json --submit`. Run as a managed background service. Use TEST and a controlled test PO before LIVE.

## Admin dashboard

The read-only dashboard shows today's file and completion counts, open exceptions, a seven-day activity chart, searchable file history, Radius Sales Order and Shipping Request numbers, and the full event timeline and latest error for each file. It refreshes every 30 seconds. All timestamps shown in the UI are UTC. The file list is paginated and searchable across the full history.

Set `DASHBOARD_USER` and `DASHBOARD_PASSWORD` in the dashboard process environment, then start it separately from the file watcher:

```powershell
$env:DASHBOARD_USER = 'edi-admin'
$env:DASHBOARD_PASSWORD = '<strong unique password>'
python -m niagara_edi --dashboard --state state/orders.sqlite3
```

Open `http://127.0.0.1:8088/` locally. The dashboard binds to loopback by default and uses HTTP Basic authentication. To expose it to other administrators, run it behind an HTTPS reverse proxy with network access controls; do not expose the HTTP listener directly to the Internet. Both the watcher and dashboard must use the same `--state` path. The dashboard has no posting or retry controls.

The watcher records AS2 receipt, EDI parsing, mapping, Sales Order, Shipping Request, and error events in SQLite. Files without a gateway receipt stay in the inbox as **Awaiting AS2 receipt**. Successful files move to `archive/`; rejected or uncertain files move to `failed/` for operator review. This prevents automatic reposting of an uncertain order. Manual one-file runs also appear in the dashboard.

The SQLite state file `state/orders.sqlite3` stores the PO business key, input digest, posting state, and Radius numbers. Completed replays return the existing numbers. A changed PO under the same PO/release or a failure after posting begins is held for operator reconciliation, since blindly retrying a timed-out POST could create a duplicate. The same applies when Sales Order creation succeeds and Shipping Request creation does not: inspect the Radius Sales Order and shipping requests, then reconcile the state before reprocessing. Preserve the original EDI and gateway receipt for audit.

## Mapping details

- `BEG03` -> `CustRef`; `BEG03/BEG04` -> `AddtCustRef`; `BEG05` -> `CustOrderDate`/`SOrderDate`.
- `N1*BT` and `N1*ST` location IDs (`N104`) -> `PV_CustAddress.AddressRef` under company `2`/customer `NIA01` -> `AddressNum` for `BillAddrNum` and `ShipAddrNum`; `CUR02` -> `CurrCode`. Lookup requires exactly one match per ID. The selected Radius address in the provided screenshot has a blank Address Reference, so address references must be populated before its location can resolve. The sample Niagara 850 in the specification omits `N1*BT` N104; this app holds such orders until an agreed fallback is configured.
- `PO101` -> customer PO line number; `PO102` -> ordered and ship quantity; `PO103` -> configured Radius order/price units; `PO104` -> `FCUnitPrice`; configured `VN` or `BP` identifier -> Radius `ItemCode`.
- `DTM*079` per line, falling back to `DTM*038`, -> Sales Order line `ReqDate`; the earliest line date -> Shipping Request `ShipDate`.
- Blanket (`BK`) orders are blocked by default because the Niagara spec allows no ship-to or line shipment date on them. Enable only after an agreed blanket fulfillment rule is defined.

The Xlink guide documents `XLSOrders.XLSOrder[].XLSOrderLine[]` on PDF pages 193-197 and `XLShipReqs.XLShipReq[].XLShipReqLine[]` on pages 210-213. This application checks Radius `statusCode`, record and line `Action`/`ErrorMessage`, and generated numbers.

## Known site decisions

The PDFs do not specify the item cross references, unit conversions, prices when PO price units differ from order units, or AS2 credentials. Configure and confirm these with the Radius and Niagara owners before posting. `PriceUnitCode` and `FCUnitPrice` assume the PO1 unit price applies to the configured price unit without conversion. If that assumption is false, add an explicit conversion rule before using `--submit`.
