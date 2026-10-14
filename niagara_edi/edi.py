from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation


class EDIError(ValueError):
    pass


@dataclass
class Line:
    po_line: str
    quantity: Decimal
    uom: str
    price: Decimal
    identifiers: dict[str, str]
    shipment_number: str = ""
    ship_no_later: str = ""
    promised_ship: str = ""
    price_uom: str = ""
    radius_line_num: int = 0
    ship_quantity: Decimal | None = None


@dataclass
class Order:
    interchange_control: str
    group_control: str
    transaction_control: str
    sender: str
    receiver: str
    po_number: str = ""
    po_type: str = ""
    release: str = ""
    po_date: str = ""
    currency: str = ""
    ship_to_id: str = ""
    bill_to_id: str = ""
    receiving_company: str = ""
    vendor_site: str = ""
    lines: list[Line] = field(default_factory=list)
    comp_num: str = ""
    plant_code: str = ""
    bill_addr_num: str = ""
    ship_addr_num: str = ""
    shipping_date: str = ""
    source_format: str = "x12"

    @property
    def business_key(self):
        return f"{self.sender}|{self.po_number}|{self.release}"


def _date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise EDIError(f"Invalid X12 date: {value}") from exc


def _decimal(value: str, name: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise EDIError(f"Invalid {name}: {value}") from exc
    if not number.is_finite() or number < 0:
        raise EDIError(f"Invalid {name}: {value}")
    return number


def _get(parts, index):
    return parts[index] if len(parts) > index else ""


def parse_850(data: bytes) -> list[Order]:
    """Parse an X12 004010 interchange. One ST/SE becomes one Order."""
    raw = data.decode("ascii").lstrip("\ufeff\r\n\t ")
    if not raw.startswith("ISA") or len(raw) < 106:
        raise EDIError("Missing fixed-width ISA envelope")
    element, component, terminator = raw[3], raw[104], raw[105]
    isa = raw[:105].split(element)
    if len(isa) != 17 or isa[0] != "ISA":
        raise EDIError("Malformed ISA envelope")
    if isa[12] != "00401":
        raise EDIError(f"Unsupported ISA version {isa[12]}")
    segments = [s.strip("\r\n ") for s in raw.split(terminator) if s.strip("\r\n ")]
    if not segments or not segments[-1].startswith("IEA" + element):
        raise EDIError("Missing IEA trailer")
    sender, receiver, icn = isa[6].strip(), isa[8].strip(), isa[13]
    orders = []
    group = None
    order = None
    current_line = None
    n1_party = None
    group_count = 0
    transaction_count = 0
    segment_count = 0
    for segment in segments:
        p = segment.split(element)
        tag = p[0]
        if order is not None:
            segment_count += 1
        if tag == "GS":
            if group is not None or order is not None or _get(p, 1) != "PO" or _get(p, 8) != "004010":
                raise EDIError("Invalid or unsupported GS group")
            group = _get(p, 6)
            group_count += 1
            transaction_count = 0
        elif tag == "ST":
            if not group or order is not None or _get(p, 1) != "850":
                raise EDIError("Unexpected transaction set")
            order = Order(icn, group, _get(p, 2), sender, receiver)
            current_line = None
            n1_party = None
            transaction_count += 1
            segment_count = 1
        elif tag == "BEG" and order:
            if _get(p, 1) != "00" or _get(p, 2) not in {"SA", "DS", "RL", "BK"}:
                raise EDIError("Unsupported BEG purpose or PO type")
            order.po_type, order.po_number, order.release = _get(p, 2), _get(p, 3), _get(p, 4)
            order.po_date = _date(_get(p, 5))
            if not order.po_number or (order.po_type == "RL" and not order.release):
                raise EDIError("Missing PO number or release")
        elif tag == "CUR" and order:
            order.currency = _get(p, 2)
        elif tag == "N1" and order:
            n1_party = _get(p, 1)
            if n1_party == "ST":
                if _get(p, 3) != "92" or not _get(p, 4):
                    raise EDIError("Missing Niagara ship-to location ID")
                order.ship_to_id = _get(p, 4)
            elif n1_party == "BT":
                order.bill_to_id = _get(p, 4) if _get(p, 3) == "92" else ""
        elif tag == "PO1" and order:
            n1_party = None
            if not all(_get(p, i) for i in (1, 2, 3, 4)):
                raise EDIError("Incomplete PO1 line")
            ids = {}
            if (len(p) - 6) % 2:
                raise EDIError("Unpaired PO1 identifier qualifier")
            for i in range(6, len(p), 2):
                if p[i] and p[i + 1]:
                    ids[p[i]] = p[i + 1]
            current_line = Line(_get(p, 1), _decimal(_get(p, 2), "quantity"), _get(p, 3).upper(), _decimal(_get(p, 4), "price"), ids)
            if current_line.quantity <= 0 or any(line.po_line == current_line.po_line for line in order.lines):
                raise EDIError("Nonpositive quantity or duplicate PO line")
            order.lines.append(current_line)
        elif tag == "REF" and order:
            qualifier, value = _get(p, 1), _get(p, 2)
            if current_line:
                if qualifier == "SS":
                    current_line.shipment_number = value
            elif qualifier == "8N":
                order.receiving_company = value
        elif tag == "N9" and order and _get(p, 1) == "VR":
            order.vendor_site = _get(p, 2)
        elif tag == "DTM" and order and current_line:
            if _get(p, 1) == "038":
                current_line.ship_no_later = _date(_get(p, 2))
            elif _get(p, 1) == "079":
                current_line.promised_ship = _date(_get(p, 2))
        elif tag == "CTT" and order:
            if _get(p, 1) and int(_get(p, 1)) != len(order.lines):
                raise EDIError("CTT line count mismatch")
        elif tag == "SE":
            if not order or _get(p, 2) != order.transaction_control or int(_get(p, 1) or 0) != segment_count:
                raise EDIError("SE control or segment count mismatch")
            if not order.po_number or not order.lines or not order.currency:
                raise EDIError("Missing PO header, currency, or lines")
            orders.append(order)
            order = None
            current_line = None
        elif tag == "GE":
            if order or not group or _get(p, 2) != group or int(_get(p, 1) or 0) != transaction_count:
                raise EDIError("GE control or transaction count mismatch")
            group = None
        elif tag == "IEA":
            if group or _get(p, 2) != icn or int(_get(p, 1) or 0) != group_count:
                raise EDIError("IEA control or group count mismatch")
    if not orders or order or group:
        raise EDIError("Incomplete interchange")
    return orders
