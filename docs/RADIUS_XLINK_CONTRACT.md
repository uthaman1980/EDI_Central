# Radius integration contract and unknowns

## Verified in this application

The TEST REST adapter wraps JSON in Base64 under `efiRadiusRequest`, sends `XLinkAPISOrder` and then `XLinkAPIShipReq` to the configured TEST endpoint, decodes `efiRadiusResponse`, requires `statusCode == 1`, and records nested errors. Current confirmed SO and SR responses contain Radius numbers and line records. `AddressResolver` reads `PUB.PV_CustAddress.AddressRef` with company/customer filters through ODBC; it does not write Radius tables. `SOSourceCode` is `EDI` and `SOrderDate` is omitted on new orders. The new-order creation logic remains in place.

Sales Order and Shipping Request **update submissions are held**. In TEST, updating SO 261383 with `PriceGroupCode` omitted or with `PEPSI` returned status 9, `LnePriceGroup-Invalid Price Group Code`. An omitted code on one line did not prove that Radius would clear a code stored on other lines. No update behavior is assumed for LIVE.

The 856 database monitor now reads `PUB.PV_XLShippingNotes` using a separate read-only ODBC connection. The supplied field contract was verified in TEST through `Radius_NewLive`. Export/EDI records are scoped by company, direction 0, and delivery type 0. RecordStatus 0/1/9 maps to Not processed/Processed OK/Rejected. This is Radius processing evidence, independent of folder retention or provider receipts; see [RADIUS_856_DATABASE_MONITOR.md](RADIUS_856_DATABASE_MONITOR.md).

## Native Radius EDI format references and remaining blocker

The supplied Radius standard flat-file workbooks are indexed in [RADIUS_EDI_FORMATS.md](RADIUS_EDI_FORMATS.md). The local 856 sample matches the outbound 856 record family, with version-dependent length differences noted there. The repository still has no verified 2026.3 flat-file writer/reader, `pxlupdate.r` invocation, XLink staging API, outbound export feed, master-data price-group lookup, or receipt/pickup integration. Obtain the installed format/version selection, customer EDI setup read contract, active update codes/options, execution owner, service account, and TEST results from the Radius owner. Never invoke a company-only XLink update or write Radius business tables directly. No central observer calls Radius XLink; it records file evidence only.
