import hashlib
import json
import sqlite3
from pathlib import Path

from .audit import Audit
from .edi import parse_850
from .radius import AddressResolver, RadiusClient, RadiusError, build_sales_order, build_shipping_request


class DuplicateOrder(ValueError):
    pass


def initialize(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        business_key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
        so_number INTEGER, shipreq_number INTEGER, error TEXT
    )""")
    conn.commit()
    return conn


def validate_transport(meta: dict, cfg: dict):
    if meta.get("authentication_status") != "verified" or meta.get("decryption_status") not in ("verified", "not_encrypted"):
        raise ValueError("AS2 signature/decryption verification is required")
    if meta.get("mic_status") != "verified":
        raise ValueError("AS2 MIC verification is required")
    for key, field in (("allowed_as2_sender", "as2_sender"), ("allowed_as2_receiver", "as2_receiver")):
        if not cfg.get(key):
            raise ValueError(f"Configure {key} before ingestion")
        if cfg[key] != meta.get(field):
            raise ValueError(f"AS2 {field} mismatch")


def process_file(edi_path: Path, meta_path: Path, cfg: dict, db_path: Path, *, submit: bool = False, client=None, resolver=None):
    """Process one AS2 gateway handoff. A failed/uncertain POST needs operator reconciliation."""
    audit = Audit(db_path)
    file_id, expected_digest = audit.start(edi_path, cfg["environment"])
    try:
        return _process_file_impl(edi_path, meta_path, cfg, db_path, audit, file_id, expected_digest,
                                  submit=submit, client=client, resolver=resolver)
    except Exception as exc:
        stage = audit.status(file_id)["stage"]
        attention = stage in {"Sales Order", "Shipping Request", "Reconciliation"}
        audit.update(file_id, "attention" if attention else "rejected", stage,
                     str(exc), level="error", error=str(exc)[:2000])
        raise


def _process_file_impl(edi_path, meta_path, cfg, db_path, audit, file_id, expected_digest,
                       *, submit, client, resolver):
    audit.update(file_id, "processing", "AS2", "Checking AS2 gateway receipt")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    validate_transport(meta, cfg)
    data = edi_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if meta.get("sha256", "").lower() != digest or digest != expected_digest:
        raise ValueError("AS2 handoff digest mismatch")
    audit.update(file_id, "processing", "EDI", "AS2 receipt verified; parsing X12 850",
                 as2_sender=meta.get("as2_sender"), as2_receiver=meta.get("as2_receiver"))
    orders = parse_850(data)
    if cfg.get("allowed_isa_sender") and any(o.sender != cfg["allowed_isa_sender"] for o in orders):
        raise ValueError("ISA sender mismatch")
    if cfg.get("allowed_isa_receiver") and any(o.receiver != cfg["allowed_isa_receiver"] for o in orders):
        raise ValueError("ISA receiver mismatch")
    audit.update(file_id, "processing", "Mapping", f"Parsed {len(orders)} purchase order(s)",
                 po_count=len(orders), po_refs=json.dumps([o.po_number for o in orders]))
    address_resolver = resolver or AddressResolver(cfg["environment"])
    if not cfg.get("cust_code"):
        raise ValueError("Missing Radius customer code")
    previews = []
    for order in orders:
        if not order.bill_to_id or not order.ship_to_id:
            raise ValueError(f"PO {order.po_number} lacks Bill To or Ship To N104; configure a partner approved fallback")
        mapped_cfg = dict(cfg)
        mapped_cfg["bill_addr_num"] = address_resolver.resolve(cfg["comp_num"], cfg["cust_code"], order.bill_to_id, "BT")
        mapped_cfg["ship_addr_num"] = address_resolver.resolve(cfg["comp_num"], cfg["cust_code"], order.ship_to_id, "ST")
        if not all(line.promised_ship or line.ship_no_later for line in order.lines):
            raise ValueError(f"PO {order.po_number} has a line without shipping date")
        previews.append((order, mapped_cfg, build_sales_order(order, mapped_cfg)))
    if not submit:
        audit.update(file_id, "previewed", "Preview", "Payload preview generated; no Radius request sent")
        return [{"business_key": o.business_key, "sales_order_payload": p} for o, _, p in previews]
    radius = client or RadiusClient(cfg["environment"])
    conn = initialize(db_path)
    results = []
    try:
        for order, mapped_cfg, payload in previews:
            key = order.business_key
            row = conn.execute("SELECT digest,state,so_number,shipreq_number FROM orders WHERE business_key=?", (key,)).fetchone()
            if row:
                if row[0] != digest:
                    audit.update(file_id, "attention", "Reconciliation", "Changed PO needs manual reconciliation", level="warning")
                    raise DuplicateOrder(f"PO {key} arrived with changed content; manual change handling required")
                if row[1] == "complete":
                    results.append({"business_key": key, "so_number": row[2], "shipreq_number": row[3], "state": "already_complete"})
                    continue
                if row[1] != "so_created":
                    audit.update(file_id, "attention", "Reconciliation", "Existing PO is in uncertain state", level="warning")
                    raise DuplicateOrder(f"PO {key} in uncertain state {row[1]}; reconcile in Radius before retry")
                so_number = row[2]
                # The original SO response line numbers are unavailable on restart. Explicitly reconcile.
                audit.update(file_id, "attention", "Reconciliation", "Sales Order exists; shipping request needs reconciliation", level="warning")
                raise DuplicateOrder(f"PO {key} has Sales Order {so_number}; reconcile its lines before shipping request retry")
            conn.execute("INSERT INTO orders(business_key,digest,state) VALUES(?,?,?)", (key, digest, "so_pending"))
            conn.commit()
            try:
                audit.update(file_id, "processing", "Sales Order", f"Submitting PO {order.po_number} to Radius")
                so = radius.post("XLinkAPISOrder", payload, "XLSOrders", "XLSOrder")
                so_number = int(so.get("SOrderNum") or 0)
                if not so_number:
                    raise RadiusError("Sales Order acknowledgement missing SOrderNum")
                conn.execute("UPDATE orders SET state='so_created',so_number=? WHERE business_key=?", (so_number, key))
                conn.commit()
                audit.update(file_id, "processing", "Sales Order", f"Sales Order {so_number} created",
                             so_numbers=json.dumps([r["so_number"] for r in results] + [so_number]))
                ship_payload = build_shipping_request(order, mapped_cfg, so_number, so)
                conn.execute("UPDATE orders SET state='ship_pending' WHERE business_key=?", (key,))
                conn.commit()
                audit.update(file_id, "processing", "Shipping Request", f"Submitting Shipping Request for Sales Order {so_number}")
                ship = radius.post("XLinkAPIShipReq", ship_payload, "XLShipReqs", "XLShipReq")
                ship_number = int(ship.get("ShipReqNum") or 0)
                if not ship_number:
                    raise RadiusError("Shipping Request acknowledgement missing ShipReqNum")
                if len(ship.get("XLShipReqLine") or []) != len(order.lines):
                    raise RadiusError("Shipping Request acknowledgement missing lines")
                if any(line.get("ErrorMessage") or line.get("Action", "").lower() == "failed" for line in ship["XLShipReqLine"]):
                    raise RadiusError("Shipping Request acknowledgement contains failed line")
                conn.execute("UPDATE orders SET state='complete',shipreq_number=?,error=NULL WHERE business_key=?", (ship_number, key))
                conn.commit()
                results.append({"business_key": key, "so_number": so_number, "shipreq_number": ship_number, "state": "complete"})
                audit.update(file_id, "processing", "Shipping Request", f"Shipping Request {ship_number} created",
                             shipreq_numbers=json.dumps([r["shipreq_number"] for r in results if r.get("shipreq_number")]))
            except Exception as exc:
                conn.execute("UPDATE orders SET error=? WHERE business_key=?", (str(exc), key))
                conn.commit()
                raise
    finally:
        conn.close()
    audit.update(file_id, "complete", "Complete", f"{len(results)} purchase order(s) completed",
                 so_numbers=json.dumps([r["so_number"] for r in results]),
                 shipreq_numbers=json.dumps([r["shipreq_number"] for r in results]))
    return results
