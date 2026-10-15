import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from .audit import Audit, utc_now
from .edi import parse_850
from .fixed850 import parse_fixed_850
from .radius import AddressResolver, RadiusClient, RadiusError, RadiusRejected, build_sales_order, build_shipping_request


class DuplicateOrder(ValueError):
    pass


def current_date() -> date:
    return date.today()


def update_past_dates(order, today: date) -> list[str]:
    """Use today's date for past Radius delivery dates and describe each change."""
    changes = []
    replacement = today.isoformat()
    fields = [(order, "cust_req_date", "customer required date"),
              (order, "shipping_date", "shipping date")]
    for line in order.lines:
        fields.extend(((line, "promised_ship", f"line {line.po_line} required date"),
                       (line, "ship_no_later", f"line {line.po_line} ship-no-later date")))
    for target, attribute, label in fields:
        original = getattr(target, attribute, "")
        if original and date.fromisoformat(original) < today:
            setattr(target, attribute, replacement)
            changes.append(f"{label}: {original} -> {replacement}")
    return changes


def initialize(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        business_key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
        so_number INTEGER, shipreq_number INTEGER, shipreq_numbers TEXT NOT NULL DEFAULT '[]', error TEXT
    )""")
    if "shipreq_numbers" not in {row[1] for row in conn.execute("PRAGMA table_info(orders)")}:
        conn.execute("ALTER TABLE orders ADD COLUMN shipreq_numbers TEXT NOT NULL DEFAULT '[]'")
    conn.commit()
    return conn


def save_radius_record(conn, file_id, order, entity, request, response, *, so_number=None,
                       shipreq_number=None, po_line=None):
    conn.execute("""INSERT INTO radius_records
        (file_id,business_key,entity,po_line,so_number,shipreq_number,request_json,response_json,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (file_id, order.business_key, entity, po_line, so_number, shipreq_number,
         json.dumps(request, ensure_ascii=False), json.dumps(response, ensure_ascii=False), utc_now()))


def apply_saved_price_groups(conn, business_key: str, so_number: int, payload: dict) -> None:
    """Preserve each line's last acknowledged Radius price group on SO update."""
    row = conn.execute("""SELECT response_json FROM radius_records
        WHERE business_key=? AND entity='XLinkAPISOrder' AND so_number=?
        ORDER BY id DESC LIMIT 1""", (business_key, so_number)).fetchone()
    if not row:
        raise ValueError(f"No saved Sales Order response for {so_number}; reconcile its price groups before updating")
    saved_lines = json.loads(row[0]).get("XLSOrderLine") or []
    requested_lines = payload["XLSOrders"]["XLSOrder"][0]["XLSOrderLine"]
    by_number = {}
    for line in saved_lines:
        number = line.get("SOrderLineNum")
        if number in by_number or number is None:
            raise ValueError(f"Saved Sales Order {so_number} has ambiguous line numbers; reconcile before updating")
        by_number[number] = line
    if len(by_number) != len(requested_lines):
        raise ValueError(f"Saved Sales Order {so_number} lines do not match the update; reconcile before updating")
    for requested in requested_lines:
        saved = by_number.get(requested["SOrderLineNum"])
        if (saved is None or "PriceGroupCode" not in saved
                or not isinstance(saved["PriceGroupCode"], str)
                or (saved.get("CustItemRef") and saved["CustItemRef"] != requested.get("CustItemRef"))):
            raise ValueError(f"No matching saved price group for Sales Order {so_number} line "
                             f"{requested['SOrderLineNum']}; reconcile before updating")
        requested["PriceGroupCode"] = saved["PriceGroupCode"]


def validate_transport(meta: dict, cfg: dict):
    if meta.get("authentication_status") != "verified" or meta.get("decryption_status") not in ("verified", "not_encrypted"):
        raise ValueError("AS2 signature/decryption verification is required")
    if meta.get("mic_status") != "verified":
        raise ValueError("AS2 MIC verification is required")
    for key, field in (("allowed_as2_sender", "as2_sender"), ("allowed_as2_receiver", "as2_receiver")):
        if not cfg.get(key) and not cfg.get("x12_partners"):
            raise ValueError(f"Configure {key} before ingestion")
        if cfg.get(key) and cfg[key] != meta.get(field):
            raise ValueError(f"AS2 {field} mismatch")


def process_file(edi_path: Path, meta_path: Path, cfg: dict, db_path: Path, *, submit: bool = False,
                 client=None, resolver=None, resubmit_mode=None, resubmission_token=None,
                 target_so_number=None, mapping_revision_id=None):
    """Process an X12 AS2 handoff or a Radius EDI850Inbound file."""
    if resubmit_mode == "update":
        raise ValueError("Radius Sales Order and Shipping Request updates are on hold pending Radius investigation")
    if resubmit_mode not in (None, "new", "update") or (resubmit_mode and (not submit or not resubmission_token)):
        raise ValueError("Invalid resubmission request")
    audit = Audit(db_path)
    file_id, expected_digest = audit.start(edi_path, cfg["environment"])
    try:
        return _process_file_impl(edi_path, meta_path, cfg, db_path, audit, file_id, expected_digest,
                                  submit=submit, client=client, resolver=resolver,
                                  resubmit_mode=resubmit_mode, resubmission_token=resubmission_token,
                                  target_so_number=target_so_number, mapping_revision_id=mapping_revision_id)
    except Exception as exc:
        stage = audit.status(file_id)["stage"]
        attention = stage in {"Sales Order", "Shipping Request", "Reconciliation"}
        if stage == "Sales Order" and isinstance(exc, RadiusRejected):
            attention = False  # Radius explicitly rejected this SO; no order number was created.
        audit.update(file_id, "attention" if attention else "rejected", stage,
                     str(exc), level="error", error=str(exc)[:2000])
        raise


def _process_file_impl(edi_path, meta_path, cfg, db_path, audit, file_id, expected_digest,
                       *, submit, client, resolver, resubmit_mode, resubmission_token, target_so_number, mapping_revision_id=None):
    data = edi_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_digest:
        raise ValueError("Input file changed during processing")
    fixed = data.lstrip().startswith(b"EDI850Inbound")
    mapped_customer_cfg = None
    if mapping_revision_id is not None:
        from .mapping_api import prepare_orders
        audit.update(file_id, "processing", "Mapping", "Validating published direct-customer mapping")
        orders, mapped_customer_cfg, mapping_label = prepare_orders(data, db_path, file_id, mapping_revision_id, cfg["environment"])
        audit.update(file_id, "processing", "Mapping", mapping_label + "; direct Radius API route")
    elif fixed:
        audit.update(file_id, "processing", "EDI", "Parsing fixed-width EDI850Inbound")
        orders = parse_fixed_850(data)
    else:
        audit.update(file_id, "processing", "EDI", "Parsing direct X12 850")
        orders = parse_850(data)
        if cfg.get("allowed_isa_sender") and any(o.sender != cfg["allowed_isa_sender"] for o in orders):
            raise ValueError("ISA sender mismatch")
        if cfg.get("allowed_isa_receiver") and any(o.receiver != cfg["allowed_isa_receiver"] for o in orders):
            raise ValueError("ISA receiver mismatch")
        profiles = cfg.get("x12_partners", {})
        if profiles:
            for order in orders:
                profile = profiles.get(order.sender)
                if not profile:
                    raise ValueError(f"No X12 partner profile for ISA sender {order.sender!r}")
                if profile.get("enabled") is False:
                    raise ValueError(f"X12 partner {order.sender!r} is disabled")
                if not profile.get("isa_receiver") or profile["isa_receiver"] != order.receiver:
                    raise ValueError(f"X12 partner {order.sender!r} ISA receiver mismatch or missing")
        needs_receipt = not profiles or any(profiles[o.sender].get("transport", "as2") == "as2" for o in orders)
        if needs_receipt:
            audit.update(file_id, "processing", "AS2", "Checking AS2 gateway receipt")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            validate_transport(meta, cfg)
            if meta.get("sha256", "").lower() != digest:
                raise ValueError("AS2 handoff digest mismatch")
            for order in orders:
                profile = profiles.get(order.sender, {})
                if profile.get("transport", "as2") != "as2":
                    raise ValueError("Cannot mix AS2 and controlled file drop orders in one interchange")
                for key in ("as2_sender", "as2_receiver"):
                    if profile and (not profile.get(key) or profile[key] != meta.get(key)):
                        raise ValueError(f"X12 partner {order.sender!r} {key} mismatch or missing")
            audit.update(file_id, "processing", "AS2", "AS2 receipt verified",
                         as2_sender=meta.get("as2_sender"), as2_receiver=meta.get("as2_receiver"))
        else:
            audit.update(file_id, "processing", "Transport", "Controlled file drop profile selected")
    audit.update(file_id, "processing", "Mapping", f"Parsed {len(orders)} purchase order(s)",
                 po_count=len(orders), po_refs=json.dumps([o.po_number for o in orders]))
    address_resolver = resolver
    previews = []
    for order in orders:
        customer_cfg = dict(cfg)
        if mapped_customer_cfg is not None:
            customer_cfg.update(mapped_customer_cfg)
        elif fixed:
            profiles = cfg.get("customers", {})
            if order.sender not in profiles:
                raise ValueError(f"No customer profile for {order.sender!r}")
            customer_cfg.update(profiles[order.sender])
            if customer_cfg.get("enabled") is False:
                raise ValueError(f"Customer {order.sender!r} is disabled")
            if str(customer_cfg.get("comp_num")) != order.comp_num or str(customer_cfg.get("plant_code")) != order.plant_code:
                raise ValueError(f"Company or plant mismatch for customer {order.sender!r}")
        elif cfg.get("x12_partners"):
            partners = cfg["x12_partners"]
            if order.sender not in partners:
                raise ValueError(f"No X12 partner profile for ISA sender {order.sender!r}")
            customer_cfg.update(partners[order.sender])
        if not customer_cfg.get("cust_code"):
            raise ValueError("Missing Radius customer code")
        if mapped_customer_cfg is not None and not resubmit_mode:
            from .mapping_api import existing_order_from_other_route
            existing_so=existing_order_from_other_route(db_path,order,customer_cfg,cfg["environment"])
            if existing_so:
                message=f"PO {order.po_number} already has Sales Order {existing_so} from another route; reconcile before using this mapping"
                audit.update(file_id,"attention","Reconciliation",message,level="error")
                raise DuplicateOrder(message)
        if not order.bill_to_id or not order.ship_to_id:
            raise ValueError(f"PO {order.po_number} lacks Bill To or Ship To address reference")
        mapped_cfg = customer_cfg
        if not fixed and customer_cfg.get("address_map"):
            addresses = customer_cfg["address_map"]
            try:
                mapped_cfg["bill_addr_num"] = int(addresses[order.bill_to_id])
                mapped_cfg["ship_addr_num"] = int(addresses[order.ship_to_id])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Missing Radius address mapping for PO {order.po_number}") from exc
        else:
            if address_resolver is None:
                address_resolver = AddressResolver(cfg["environment"])
            mapped_cfg["bill_addr_num"] = address_resolver.resolve(customer_cfg["comp_num"], customer_cfg["cust_code"], order.bill_to_id, "BT")
            mapped_cfg["ship_addr_num"] = address_resolver.resolve(customer_cfg["comp_num"], customer_cfg["cust_code"], order.ship_to_id, "ST")
        if not all(line.promised_ship or line.ship_no_later for line in order.lines):
            raise ValueError(f"PO {order.po_number} has a line without shipping date")
        if customer_cfg.get("shipping_request_mode", "combined") not in {"combined", "per_line"}:
            raise ValueError(f"Invalid shipping request creation mode for {order.sender!r}")
        changes = update_past_dates(order, current_date())
        previews.append((order, mapped_cfg, build_sales_order(order, mapped_cfg), changes))
    if not submit:
        for order, _, _, changes in previews:
            for change in changes:
                audit.update(file_id, "processing", "Dates", f"PO {order.po_number}: preview uses {change}")
        audit.update(file_id, "previewed", "Preview", "Payload preview generated; no Radius request sent")
        return [{"business_key": o.business_key, "sales_order_payload": p,
                 "shipping_request_count": len(o.lines) if c.get("shipping_request_mode") == "per_line" else 1,
                 "file_id": file_id}
                for o, c, p, _ in previews]
    radius = client or RadiusClient(cfg["environment"])
    conn = initialize(db_path)
    results = []
    try:
        for order, mapped_cfg, payload, changes in previews:
            base_key = order.business_key
            key = base_key
            previous_so = None
            previous_ships = []
            if resubmit_mode:
                if target_so_number is None:
                    original = conn.execute("SELECT digest,state,so_number,shipreq_number,shipreq_numbers FROM orders WHERE business_key=?", (base_key,)).fetchone()
                else:
                    original = conn.execute("""SELECT digest,state,so_number,shipreq_number,shipreq_numbers
                        FROM orders WHERE digest=? AND state='complete' AND so_number=?
                        AND (business_key=? OR substr(business_key,1,?)=?) LIMIT 1""",
                        (digest, target_so_number, base_key, len(base_key) + 10, base_key + "|resubmit:")).fetchone()
                if not original or original[0] != digest or original[1] != "complete":
                    raise DuplicateOrder(f"PO {base_key} must have a completed matching submission before {resubmit_mode} resubmission")
                previous_so = original[2]
                previous_ships = json.loads(original[4] or "[]") or ([original[3]] if original[3] else [])
                key = f"{base_key}|resubmit:{resubmit_mode}:{resubmission_token}"
                if resubmit_mode == "update":
                    if not previous_so or len(previous_ships) != (len(order.lines) if mapped_cfg.get("shipping_request_mode") == "per_line" else 1):
                        raise ValueError("Saved Radius numbers do not match the order lines; reconcile before updating")
                    header = payload["XLSOrders"]["XLSOrder"][0]
                    header["SOrderNum"] = previous_so
                    for line_payload in header["XLSOrderLine"]:
                        line_payload["SOrderNum"] = previous_so
                    apply_saved_price_groups(conn, base_key, previous_so, payload)
            row = conn.execute("SELECT digest,state,so_number,shipreq_number,shipreq_numbers FROM orders WHERE business_key=?", (key,)).fetchone()
            if row:
                if row[0] != digest:
                    audit.update(file_id, "attention", "Reconciliation", "Changed PO needs manual reconciliation", level="warning")
                    raise DuplicateOrder(f"PO {key} arrived with changed content; manual change handling required")
                if row[1] == "complete":
                    numbers = json.loads(row[4] or "[]") or ([row[3]] if row[3] else [])
                    results.append({"business_key": base_key, "so_number": row[2], "shipreq_number": row[3],
                                    "shipreq_numbers": numbers, "state": "already_complete", "file_id": file_id,
                                    "resubmission_mode": resubmit_mode})
                    continue
                if row[1] != "so_created":
                    audit.update(file_id, "attention", "Reconciliation", "Existing PO is in uncertain state", level="warning")
                    raise DuplicateOrder(f"PO {key} in uncertain state {row[1]}; reconcile in Radius before retry")
                so_number = row[2]
                # The original SO response line numbers are unavailable on restart. Explicitly reconcile.
                audit.update(file_id, "attention", "Reconciliation", "Sales Order exists; shipping request needs reconciliation", level="warning")
                raise DuplicateOrder(f"PO {key} has Sales Order {so_number}; reconcile its lines before shipping request retry")
            if resubmit_mode:
                uncertain = conn.execute("""SELECT business_key,state FROM orders
                    WHERE substr(business_key,1,?)=? AND state<>'complete' LIMIT 1""",
                    (len(base_key) + 10, base_key + "|resubmit:")).fetchone()
                if uncertain:
                    raise DuplicateOrder(f"PO {base_key} has uncertain resubmission {uncertain[0]}; reconcile in Radius before another attempt")
            for change in changes:
                audit.update(file_id, "processing", "Dates", f"PO {order.po_number}: past date changed to today for Radius: {change}", level="warning")
            conn.execute("INSERT INTO orders(business_key,digest,state) VALUES(?,?,?)", (key, digest, "so_pending"))
            conn.commit()
            try:
                audit.update(file_id, "processing", "Sales Order",
                             f"{'Updating existing' if resubmit_mode == 'update' else 'Creating new'} Sales Order for PO {order.po_number} in Radius")
                so = radius.post("XLinkAPISOrder", payload, "XLSOrders", "XLSOrder")
                so_number = int(so.get("SOrderNum") or 0)
                if not so_number:
                    raise RadiusError("Sales Order acknowledgement missing SOrderNum")
                save_radius_record(conn, file_id, order, "XLinkAPISOrder", payload, so,
                                   so_number=so_number)
                conn.execute("UPDATE orders SET state='so_created',so_number=? WHERE business_key=?", (so_number, key))
                conn.commit()
                if resubmit_mode == "update" and so_number != previous_so:
                    raise RadiusError(f"Radius returned Sales Order {so_number} instead of expected {previous_so}; reconcile before retry")
                if resubmit_mode == "new" and so_number == previous_so:
                    raise RadiusError(f"Radius returned existing Sales Order {so_number} for a new-record request; reconcile before retry")
                audit.update(file_id, "processing", "Sales Order", f"Sales Order {so_number} {'updated' if resubmit_mode == 'update' else 'created'}",
                             so_numbers=json.dumps([r["so_number"] for r in results] + [so_number]))
                ship_sales_record = so
                if resubmit_mode == "update" and (len(so.get("XLSOrderLine") or []) != len(order.lines)
                                                  or any(not line.get("ItemCode") for line in so.get("XLSOrderLine") or [])):
                    saved = conn.execute("""SELECT response_json FROM radius_records
                        WHERE business_key=? AND entity='XLinkAPISOrder' AND so_number=?
                        ORDER BY id LIMIT 1""", (base_key, previous_so)).fetchone()
                    if saved:
                        ship_sales_record = json.loads(saved[0])
                ship_payload = build_shipping_request(order, mapped_cfg, so_number, ship_sales_record)
                shipping_payloads = [ship_payload]
                if mapped_cfg.get("shipping_request_mode") == "per_line":
                    header = ship_payload["XLShipReqs"]["XLShipReq"][0]
                    shipping_payloads = []
                    for line in header["XLShipReqLine"]:
                        single = {**header, "XLShipReqLine": [{**line, "ShipReqLineNum": 1}]}
                        shipping_payloads.append({"XLShipReqs": {"XLShipReq": [single]}})
                if resubmit_mode == "update":
                    for request_payload, existing_number in zip(shipping_payloads, previous_ships):
                        ship_header = request_payload["XLShipReqs"]["XLShipReq"][0]
                        ship_header["ShipReqNum"] = existing_number
                        for ship_line in ship_header["XLShipReqLine"]:
                            ship_line["ShipReqNum"] = existing_number
                conn.execute("UPDATE orders SET state='ship_pending' WHERE business_key=?", (key,))
                conn.commit()
                ship_numbers = []
                for index, request_payload in enumerate(shipping_payloads, 1):
                    audit.update(file_id, "processing", "Shipping Request",
                                 f"Submitting Shipping Request {index}/{len(shipping_payloads)} for Sales Order {so_number}")
                    ship = radius.post("XLinkAPIShipReq", request_payload, "XLShipReqs", "XLShipReq")
                    ship_number = int(ship.get("ShipReqNum") or 0)
                    if not ship_number:
                        raise RadiusError("Shipping Request acknowledgement missing ShipReqNum")
                    if ship_number in ship_numbers:
                        raise RadiusError(f"Radius returned duplicate Shipping Request number {ship_number}")
                    expected_lines = len(request_payload["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"])
                    if len(ship.get("XLShipReqLine") or []) != expected_lines:
                        raise RadiusError("Shipping Request acknowledgement missing lines")
                    if any(line.get("ErrorMessage") or line.get("Action", "").lower() == "failed" for line in ship["XLShipReqLine"]):
                        raise RadiusError("Shipping Request acknowledgement contains failed line")
                    ship_numbers.append(ship_number)
                    source_line = order.lines[index - 1].po_line if mapped_cfg.get("shipping_request_mode") == "per_line" else None
                    save_radius_record(conn, file_id, order, "XLinkAPIShipReq", request_payload, ship,
                                       so_number=so_number, shipreq_number=ship_number, po_line=source_line)
                    conn.commit()
                    if resubmit_mode == "update" and ship_number != previous_ships[index - 1]:
                        raise RadiusError(f"Radius returned Shipping Request {ship_number} instead of expected {previous_ships[index - 1]}; reconcile before retry")
                    if resubmit_mode == "new" and ship_number in previous_ships:
                        raise RadiusError(f"Radius returned existing Shipping Request {ship_number} for a new-record request; reconcile before retry")
                    conn.execute("UPDATE orders SET shipreq_number=?,shipreq_numbers=? WHERE business_key=?",
                                 (ship_numbers[0], json.dumps(ship_numbers), key))
                    conn.commit()
                    completed_numbers = [n for result in results for n in result["shipreq_numbers"]]
                    audit.update(file_id, "processing", "Shipping Request", f"Shipping Request {ship_number} {'updated' if resubmit_mode == 'update' else 'created'}",
                                 shipreq_numbers=json.dumps(completed_numbers + ship_numbers))
                conn.execute("UPDATE orders SET state='complete',error=NULL WHERE business_key=?", (key,))
                conn.commit()
                results.append({"business_key": base_key, "so_number": so_number, "shipreq_number": ship_numbers[0],
                                "shipreq_numbers": ship_numbers, "state": "complete", "file_id": file_id,
                                "resubmission_mode": resubmit_mode})
            except Exception as exc:
                if isinstance(exc, RadiusRejected) and conn.execute(
                    "SELECT state FROM orders WHERE business_key=?", (key,)
                ).fetchone()[0] == "so_pending":
                    conn.execute("DELETE FROM orders WHERE business_key=?", (key,))
                else:
                    conn.execute("UPDATE orders SET error=? WHERE business_key=?", (str(exc), key))
                conn.commit()
                raise
    finally:
        conn.close()
    audit.update(file_id, "complete", "Complete", f"{len(results)} purchase order(s) completed",
                 so_numbers=json.dumps([r["so_number"] for r in results]),
                 shipreq_numbers=json.dumps([n for result in results for n in result["shipreq_numbers"]]))
    return results
