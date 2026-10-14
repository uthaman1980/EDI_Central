# Niagara Water inbound 850 onboarding

The user supplied `NIA85080470250957.txt` on 2026-10-14 and stated that PepsiCo and Niagara PO files arrive from SFTP with customer-initial filename prefixes. The user then confirmed destination Radius customer `NIA01` and SFTP landing folder `\\P-RAD-FSM-AP\Radius\EDI\NIA01\850`. The folder is accessible from this machine and had no TXT files at discovery. Do not move files from this feed or enable posting before ownership, complete mappings, and handling are confirmed.

The original customer file and its business values are retained locally and excluded from Git. Identifiers and pricing below are redacted.

## Source evidence

The file is a Radius `EDI850Inbound` fixed-width file containing one PO, one sales line, one shipping header and one shipping line. Six records reconcile with the footer, which reports two order headers (SO and SR). Its structural pairing, customer/plant agreement, PO-line association, and ordered/shipping quantities pass inspection.

| Field | Source value / finding |
| --- | --- |
| Filename | `NIA85080470250957.txt` |
| PO | `[local sample PO]` |
| Source customer | `BXZALLST`, not `NIA01`; filename prefix is not customer-routing authority |
| Company / plant | `2` / `2` |
| Order date | 2026-10-13 |
| Bill-to reference | Blank |
| Ship-to reference | `[local sample ship-to reference]` |
| Currency | Blank |
| Required dates | Customer/header and line required dates blank |
| Ship date | Blank |
| Customer item | `[local sample customer-item reference]` |
| Quantity / order unit | [sample quantity] / `IM` |
| Source unit price / price unit | [local sample price] / blank; price basis requires confirmation |
| Source terms code | `1% 10 NE`, which differs from verified Radius customer terms `1%10, 30`; requires an explicit code mapping or corrected source before submission |

## Verified TEST reads

Read-only ODBC checks used `Radius_NewLive` (TEST). Metadata confirmed the fields queried in `PV_Customer`, `PV_CustAddress`, and `PV_ItemCustomers`.

- Customer `BXZALLST` was not found in company 2. `NIA01` exists as Niagara Bottling LLC with currency `USD`; its default bill and ship address numbers are both zero, so those defaults do not resolve this file's missing billing destination.
- No company-2 `PV_CustAddress.AddressRef` exactly matches `[local sample ship-to reference]`.
- No company-2 `PV_ItemCustomers.CustItemRef` or `CustItemRef2` exactly matches `[local sample customer-item reference]`.

These are TEST findings only. Do not assume LIVE has the same missing references. Do not substitute the known Niagara customer or USD until the source-to-Radius customer mapping is confirmed. Existing customer profiles already support a source routing code different from the destination Radius customer code.

The user subsequently confirmed `NIA01` and supplied a Radius customer screenshot showing USD currency, terms `1%10, 30`, a blank ordinary Price Group Code, and Special Price Group Code `NIA01`. The two price-group fields are distinct; the screenshot does not justify sending `NIA01` as a line PriceGroupCode. Currency and terms are recorded as verified customer references, but no blank dates, addresses, units, or item cross references have been invented.

A disabled legacy source profile `BXZALLST` now points to destination `NIA01`; its order and price unit maps remain empty until confirmed. It appears in Customers and in the source inspection's profile match. A separate TEST inbound 850 VAN capability is saved as a paused draft under the existing central Niagara partner. It records the user-provided folder as the source landing folder and `NIA850*.txt` as its discovery mask. It does not assert that native Radius 850 activation is enabled or use that source folder as a verified Radius import destination. The existing LIVE 856 observer remains separate. Configuration was backed up before adding the draft.

## Implemented inspection and posting boundary

**Inspect inbound 850** at `/central/850-inspect` reads the uploaded sample in memory, verifies record structure, and shows the source customer, current profile match, PO, line values, missing fields, and price-unit warning. It works before any customer profile exists. No Radius call or file publication occurs during this inspection.

The optional incomplete parsing mode is confined to diagnostic inspection. Normal preview/mapping and submissions still use strict parsing, which now identifies missing currency/order-date/ship-date fields by name. The existing SO and Shipping Request payload creation and submission logic is preserved. The received file is unchanged, and no order was submitted from this sample. The existing past-date policy applies to dates actually present; it does not authorize inventing blank required/ship dates.

## Inputs needed to complete onboarding

1. Destination `NIA01` is confirmed; retain source `BXZALLST` in the draft alias and source audit. Confirm any provider-side correction before changing the source routing code.
2. Provide the correct billing reference/address and a verified mapping for ship-to `[local sample ship-to reference]` in TEST. Source address corrections or approved customer-specific defaults must be explicit; do not modify Radius master data automatically.
3. Currency USD and customer terms are confirmed. Still need required/ship dates or an explicit blank-date policy, and the order and price unit mappings for `IM` and the [local sample price] source price.
4. Confirm the Radius customer-item cross reference for `[local sample customer-item reference]` or provide the corrected source reference.
5. The source folder is confirmed; ownership, retention and TEST/LIVE feed handling still need confirmation before automatic processing. Filename masks may distinguish customers for discovery, but the file header still validates routing. Preserve the provider feed if Radius or another process already consumes it.

Until the remaining values are confirmed, keep the source-customer alias disabled, preserve blank dates/addresses, and leave automatic posting off. Next acceptance step is a complete, verified TEST preview, followed by a scoped TEST creation using the established SO/SR logic and saved cross references.
