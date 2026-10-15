# Middleware format contracts

## Implemented

- Versioned direct-customer inbound mappings support CSV/JSON, the existing X12 subset, and existing fixed-850 sources. The default target is the current Radius SO/SR REST service, with manual TEST preview/submission and persisted lineage; no intermediate Radius 850 file is required. Outbound 856 supports configured CSV/JSON preview/download. Full cXML/X12 outbound adapters and transport activation remain separate verified contracts. See [MAPPING_AND_ONBOARDING.md](MAPPING_AND_ONBOARDING.md).

- `EDI850Inbound` fixed-width parser follows local `EDI850StandardFormat.xls` for SOHEAD, SOLINE, SRHEAD, SRLINE, and TOTALS. The existing REST mapping creates an SO and SRs, not a native Radius flat-file import.
- Direct X12 004010 850 parser covers the currently tested Niagara segment profile. Its AS2 receipt is an input from a gateway; this app does not itself perform AS2 cryptography or MDN exchange.
- The central VAN observer stores checksum, size, path, time, and state. The explicitly selected `RADIUS_856_OUT_V1` profile reads Standard Outbound 856 format 1 references from the file's fixed positions, verifies record hierarchy and totals, and indexes BOL/SR/SO/PO references with customer/company/plant checks. It does not infer document identity from a filename. Other profiles remain opaque. Parsing status is separate from pickup status. The supplied Radius format reference set and the local outbound 856 sample are indexed in [RADIUS_EDI_FORMATS.md](RADIUS_EDI_FORMATS.md).

## Required before activating new translators

For each direction/customer/message: reconcile the supplied Radius workbook with the installed Radius version and a redacted approved sample, obtain a partner implementation guide/sample for direct routes, required cross references, filename/sequence rules, validation and acknowledgment rules, source/destination folder ownership, and TEST expected output. Build a versioned parser/serializer with fixture and contract tests when translation is required. A draft capability record alone cannot make an unknown format operational.
