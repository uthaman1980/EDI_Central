"""Read-only reference extraction for Radius Standard Outbound 856, format 1.

Positions come from RadiusEDIFormatFiles/EDI856StandardOutFormat.xls.
No partner translation, Radius updates, or pickup confirmation is performed.
"""

PROFILE = "RADIUS_856_OUT_V1"
PARSER_VERSION = "radius-856-out-v1.1"


def parse_outbound_856(data, *, customer=None, company=None, plant=None):
    records = data.decode("cp1252").splitlines()
    if not records or records[0][:14].upper() != "EDI856OUTBOUND":
        raise ValueError("Expected EDI856OutBound control header")
    if len(records) < 3 or records[-1][:6] != "TOTALS":
        raise ValueError("Missing final TOTALS record")

    def field(record, start, size):
        if len(record) < start + size - 1:
            raise ValueError(f"Truncated {record[:6].strip()} field at position {start}")
        return record[start - 1:start + size - 1].strip()

    def number(record, start, size):
        value = field(record, start, size)
        if not value.isdigit():
            raise ValueError(f"Invalid {record[:6].strip()} numeric field at position {start}")
        return int(value)

    documents, bols, seen_notes = [], [], set()
    current = None
    current_bol = None
    bol_identity = None
    have_line = False
    for index, record in enumerate(records[1:-1], 2):
        tag = record[:6].strip()
        if tag == "SNBOL":
            current_bol = number(record, 22, 8)
            bol_identity = (number(record, 8, 6), field(record, 14, 8))
            bols.append(current_bol)
            current = None
            have_line = False
        elif tag == "SNHEAD":
            comp = number(record, 8, 6)
            site = field(record, 14, 8)
            cust = field(record, 30, 8)
            bol = number(record, 101, 8)
            sr = number(record, 22, 8)
            if current_bol is None or bol != current_bol:
                raise ValueError(f"SNHEAD BOL does not match its BOL record at row {index}")
            if (comp, site) != bol_identity:
                raise ValueError(f"SNHEAD company/plant does not match its BOL record at row {index}")
            if ((customer is not None and cust != customer) or
                    (company is not None and comp != int(company)) or
                    (plant is not None and site != plant)):
                raise ValueError(f"856 customer/company/plant does not match capability at row {index}")
            if (comp, site, sr) in seen_notes:
                raise ValueError(f"Duplicate shipping note at row {index}")
            seen_notes.add((comp, site, sr))
            current = {"company": comp, "plant": site, "customer": cust,
                       "bol": bol, "shipreq": sr, "lines": []}
            documents.append(current)
            have_line = False
        elif tag == "SNHED2":
            if current is None:
                raise ValueError(f"SNHED2 has no shipping note at row {index}")
        elif tag == "SNLINE":
            if current is None:
                raise ValueError(f"SNLINE has no shipping note at row {index}")
            line = {"shipreq_line": number(record, 7, 4), "so": number(record, 19, 8),
                    "so_line": number(record, 27, 4), "po": field(record, 113, 30)}
            if any(x["shipreq_line"] == line["shipreq_line"] for x in current["lines"]):
                raise ValueError(f"Duplicate shipping note line at row {index}")
            current["lines"].append(line)
            have_line = True
        elif tag in {"SNPICK", "SNBRAK"}:
            if not have_line:
                raise ValueError(f"{tag} has no shipping note line at row {index}")
        else:
            raise ValueError(f"Unsupported 856 record type at row {index}")
    totals = records[-1]
    if (number(totals, 7, 6), number(totals, 13, 6), number(totals, 19, 6)) != (
            len(bols), len(documents), len(records)):
        raise ValueError("856 TOTALS counts do not match BOLs, shipping notes, and records")
    if not documents or any(not doc["lines"] for doc in documents):
        raise ValueError("856 shipping notes must contain lines")
    return documents
