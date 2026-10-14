"""Parser for the Radius EDI850Inbound fixed-width format.

Positions are one-based in EDI850StandardFormat.xls.  This module uses
zero-based slices through ``field`` and validates the SO/SR pairing.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from .edi import EDIError, Line, Order


def field(record: str, position: int, length: int) -> str:
    return record[position - 1:position - 1 + length].strip()


def date(record: str, position: int) -> str:
    value = field(record, position, 8)
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%d%m%Y").date().isoformat()
    except ValueError as exc:
        raise EDIError(f"Invalid DDMMYYYY date {value!r}") from exc


def quantity(record: str, position: int, label: str) -> Decimal:
    value = field(record, position, 12)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise EDIError(f"Invalid {label}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise EDIError(f"Invalid {label}: {value!r}")
    return number


def parse_fixed_850(data: bytes, *, require_complete: bool = True) -> list[Order]:
    try:
        records = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise EDIError("Fixed-width 850 must be ASCII") from exc
    if len(records) < 5 or field(records[0], 1, 6) != "EDI850" or field(records[0], 7, 8) != "Inbound":
        raise EDIError("Missing EDI850Inbound control header")
    if field(records[-1], 1, 6) != "TOTALS":
        raise EDIError("Missing TOTALS record")
    total = field(records[-1], 13, 6)
    if not total.isdigit() or int(total) != len(records):
        raise EDIError("TOTALS record count mismatch")
    orders = []
    order = None
    shipping_lines = []
    shipping_header = None
    for record in records[1:-1]:
        tag = field(record, 1, 6)
        if tag not in {"SOHEAD", "SOLINE", "SRHEAD", "SRLINE"}:
            raise EDIError(f"Unexpected record type {tag!r}")
        if field(record, 7, 1) != "0":
            raise EDIError(f"Only add action is supported for {tag}")
        if tag == "SOHEAD":
            if order is not None:
                _finish(order, shipping_header, shipping_lines, require_complete=require_complete)
                orders.append(order)
            customer = field(record, 46, 8)
            po = field(record, 157, 30)
            if not customer or not po:
                raise EDIError("SOHEAD requires CustCode and CustRef")
            order = Order("", "", "", customer, "", po_number=po,
                          po_date=date(record, 38) or date(record, 30),
                          currency=field(record, 230, 4),
                          bill_to_id=field(record, 73, 10),
                          ship_to_id=field(record, 87, 10),
                          source_format="radius_fixed")
            order.comp_num = field(record, 8, 6)
            order.plant_code = field(record, 14, 8)
            order.bill_addr_num = field(record, 567, 7)
            order.ship_addr_num = field(record, 574, 7)
            order.sorder_date = date(record, 30)
            order.cust_req_date = date(record, 222)
            order.addt_cust_ref = field(record, 537, 30)
            order.price_terms = field(record, 214, 8)
            shipping_header = None
            shipping_lines = []
        elif order is None:
            raise EDIError(f"{tag} before SOHEAD")
        elif tag == "SOLINE":
            if shipping_header is not None:
                raise EDIError("SOLINE after SRHEAD")
            ref = field(record, 862, 20) or field(record, 850, 4)
            item = field(record, 8, 25)
            customer_item = field(record, 68, 30)
            if not ref or not ref.isdigit() or not (item or customer_item):
                raise EDIError("SOLINE requires numeric CustPOLineRef and item reference")
            if any(line.po_line == ref for line in order.lines):
                raise EDIError(f"Duplicate SOLINE reference {ref}")
            price_text = field(record, 120, 13)
            try:
                price = Decimal(price_text)
            except InvalidOperation as exc:
                raise EDIError(f"Invalid FCUnitPrice {price_text!r}") from exc
            if not price.is_finite() or price < 0:
                raise EDIError(f"Invalid FCUnitPrice {price_text!r}")
            line = Line(ref, quantity(record, 98, "OrderedQty"), field(record, 110, 10),
                        price, {"IC": item, "CI": customer_item},
                        promised_ship=date(record, 60))
            line.price_uom = field(record, 133, 10)
            line.radius_line_num = int(field(record, 850, 4) or 0)
            order.lines.append(line)
        elif tag == "SRHEAD":
            if shipping_header is not None or not order.lines:
                raise EDIError("SRHEAD requires preceding SOLINE records")
            if field(record, 22, 8) != order.sender:
                raise EDIError("SRHEAD customer does not match SOHEAD")
            if field(record, 8, 6) != order.comp_num or field(record, 14, 8) != order.plant_code:
                raise EDIError("SRHEAD company or plant does not match SOHEAD")
            shipping_header = record
            order.shipping_date = date(record, 45)
            if field(record, 62, 10) != order.bill_to_id or field(record, 76, 10) != order.ship_to_id:
                raise EDIError("SRHEAD address references do not match SOHEAD")
        elif tag == "SRLINE":
            if shipping_header is None:
                raise EDIError("SRLINE before SRHEAD")
            shipping_lines.append(record)
    if order is None:
        raise EDIError("No purchase orders")
    _finish(order, shipping_header, shipping_lines, require_complete=require_complete)
    orders.append(order)
    count = field(records[-1], 7, 6)
    if not count.isdigit() or int(count) != 2 * len(orders):
        raise EDIError("TOTALS order header count mismatch")
    return orders


def _finish(order: Order, shipping_header: str | None, shipping_lines: list[str], *, require_complete=True) -> None:
    if not shipping_header or not order.lines:
        raise EDIError(f"Incomplete SO/SR for PO {order.po_number}")
    if require_complete:
        missing = []
        if not order.po_date:
            missing.append("SOHEAD.CustOrderDate/SOrderDate")
        if not order.currency:
            missing.append("SOHEAD.CurrCode")
        if not order.shipping_date:
            missing.append("SRHEAD.ShipDate")
        if missing:
            raise EDIError(f"PO {order.po_number} is missing required source fields: {', '.join(missing)}")
    if len(shipping_lines) != len(order.lines):
        raise EDIError(f"SRLINE count does not match SOLINE count for PO {order.po_number}")
    by_ref = {line.po_line: line for line in order.lines}
    seen = set()
    for record in shipping_lines:
        ref = field(record, 528, 20) or field(record, 508, 20)
        if ref not in by_ref or ref in seen:
            raise EDIError(f"Unknown or duplicate SRLINE reference {ref!r}")
        if field(record, 28, 30) != order.po_number:
            raise EDIError("SRLINE PO reference mismatch")
        line = by_ref[ref]
        source_line_num = str(line.radius_line_num) if line.radius_line_num else ref
        if field(record, 24, 4) != source_line_num:
            raise EDIError(f"SRLINE sales line number mismatch for {ref}")
        customer_item = field(record, 83, 30)
        item = field(record, 58, 25)
        if customer_item and customer_item != line.identifiers["CI"]:
            raise EDIError(f"SRLINE customer item mismatch for {ref}")
        if item and item != line.identifiers["IC"]:
            raise EDIError(f"SRLINE item mismatch for {ref}")
        ship_qty = quantity(record, 170, "ShipQty")
        if ship_qty > line.quantity:
            raise EDIError(f"ShipQty exceeds OrderedQty for line {ref}")
        line.ship_quantity = ship_qty
        line.shipment_number = field(record, 455, 30)
        seen.add(ref)


def inspect_fixed_850(data: bytes) -> list[dict]:
    """Inspect structurally valid files without inventing missing business values.

    Only this diagnostic path permits incomplete source fields. Posting and
    mapping continue to use parse_fixed_850's strict default.
    """
    reports = []
    for order in parse_fixed_850(data, require_complete=False):
        missing = []
        for name, value in (("SOHEAD.BillAddressRef", order.bill_to_id),
                            ("SOHEAD.ShipAddressRef", order.ship_to_id),
                            ("SOHEAD.CustOrderDate/SOrderDate", order.po_date),
                            ("SOHEAD.CurrCode", order.currency),
                            ("SRHEAD.ShipDate", getattr(order, "shipping_date", ""))):
            if not value:
                missing.append(name)
        lines = []
        for index, line in enumerate(order.lines, 1):
            if not line.promised_ship:
                missing.append(f"SOLINE[{index}].ReqDate")
            if not line.uom:
                missing.append(f"SOLINE[{index}].OrderUnitCode")
            lines.append({"po_line": line.po_line, "customer_item": line.identifiers.get("CI", ""),
                          "quantity": str(line.quantity), "order_unit": line.uom, "price": str(line.price),
                          "price_unit": line.price_uom, "required_date": line.promised_ship,
                          "shipping_quantity": str(line.ship_quantity)})
        reports.append({"customer_code": order.sender, "company": order.comp_num, "plant": order.plant_code,
                        "po": order.po_number, "order_date": order.po_date, "currency": order.currency,
                        "terms_code": getattr(order, "price_terms", ""),
                        "bill_reference": order.bill_to_id, "ship_reference": order.ship_to_id,
                        "shipping_date": getattr(order, "shipping_date", ""),
                        "missing_fields": missing, "lines": lines,
                        "warnings": ["Blank price unit requires a verified price-unit mapping; order-unit fallback must not be assumed correct."]
                        if any(not line.price_uom for line in order.lines) else []})
    return reports
