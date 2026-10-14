# Radius EDI flat-file format references

The reference set supplied for this application is `RadiusEDIFormatFiles/`. These are Radius standard **flat-file** layouts, not X12 partner implementation guides. Positions in the workbooks are one-based. Keep the original workbooks as the source of field definitions; this index records which contract to consult for each message and direction.

| Workbook in `RadiusEDIFormatFiles/` | Radius direction | Sections |
| --- | --- | --- |
| `EDI830StandardFormat.xls` | Inbound 830 | Control header, header, line, totals |
| `EDI850StandardFormat.xls` | Inbound 850 | Control header, PO header/line, shipping request header/line, totals |
| `EDI856StandardInFormat.xls` | Inbound 856 | Control header, header, line, breakdown, totals |
| `EDI810StandardFormat.xls` | Outbound 810 | Control header, two header sections, three line sections, totals |
| `EDI850StandardOutFormat.xls` | Outbound 850 | Control header, two header sections, four line sections, totals |
| `EDI855StandardFormat.xls` | Outbound 855 | Control header, three header sections, two line sections, totals |
| `EDI856StandardOutFormat.xls` | Outbound 856 | Control header, BOL, header, header 2, line, pick, breakdown, totals |
| `EDI858StandardOutFormat.xls` | Outbound 858 | Control header, BOL, header, header 2, line, pick, breakdown, totals |

## Outbound 856 sample comparison

`856NIA01000471.txt` is a local Radius outbound 856 sample. Its control header begins `EDI856OutBound`; the record family matches `EDI856StandardOutFormat.xls`: `SNBOL`, `SNHEAD`, `SNHED2`, `SNLINE`, `SNPICK`, `SNBRAK`, and `TOTALS`. The sample has 3 BOL records, 6 shipping-note headers, 6 second headers, 6 line records, 92 pick records, 733 breakdown records, and 848 records total. The footer reports 3 BOLs, 6 shipping notes, and 848 total records, so these counts reconcile. This supports the sample's **format family**, but does not prove its Radius customer configuration, format option, outbound directory, or VAN pickup.

The new folder copy of `EDI856StandardOutFormat.xls` is different from the older workbook at the repository root. In the folder copy, `SNHEAD` adds 20-character `BillLegacyCustNum` and `ShipLegacyCustNum` fields at positions 5109 and 5129 and marks their old 8-character positions unused. It also documents message-format-dependent SSCC and reference fields after position 226 on `SNPICK` and after position 403 on `SNBRAK`. The supplied sample has `SNHEAD` length 5108 and `SNPICK` length 226, so those appended fields are absent. Its `SNBRAK` records are 403 characters long; the older root workbook ends at position 401. `SNBOL`, `SNHED2`, and `SNLINE` lengths match both workbook copies. The 14-character control header omits the two fields the workbook calls unused. **TO VERIFY:** which installed Radius format/version/options produced the sample and whether optional or trailing blank fields are intentionally omitted. The format 1 reference extractor reads the common BOL/SR/SO/PO positions and validates totals and hierarchy. It does not require the appended format 3 fields or validate every business field; it preserves source bytes and does not enforce full record length from the newer workbook. The Radius screenshot supplied later confirms that Niagara selects message format 1.

The folder copy of `EDI850StandardFormat.xls` also differs from the root copy: the two old 8-character legacy customer-number positions are now marked unused, and 20-character replacements appear at the end of the PO header (positions 3147 and 3167). The existing inbound 850 parser and REST creation flow use the established file fields; do not silently remap it to the new workbook without a fixture comparison and Radius owner confirmation.

## Use in future capabilities

Choose the workbook by **message type and direction**, then confirm the Radius-side format option, installed release, actual file layout, and customer setting. Use a representative TEST file plus an approved partner guide before implementing a direct translator. A VAN outbound observer needs only the verified Radius folder and filename contract to record a file; it must not imply that Radius or the VAN accepted the business document. Preserve the supplied workbooks and samples unchanged. Production-like samples remain outside test fixtures and should not be copied into logs or committed as test data.
