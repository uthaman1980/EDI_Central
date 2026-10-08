import base64
import json
import os

import requests
import pyodbc


ENV_DEFAULTS = {
    "TEST": {"API_URL": "http://t-rad-fsm-ef.fsm.corp:8081/radadapter/radius/api", "RADIUS_DSN": "Radius_TestNew", "RADIUS_DB_USER": "vision"},
    "LIVE": {"API_URL": "http://fsmradius:8081/radadapter/radius/api", "RADIUS_DSN": "Radius_Live64", "RADIUS_DB_USER": "vision"},
}


class RadiusError(RuntimeError):
    pass


class RadiusClient:
    def __init__(self, environment: str, session=None):
        if environment not in ENV_DEFAULTS:
            raise ValueError("environment must be TEST or LIVE")
        self.url = os.getenv("RADIUS_API_URL", ENV_DEFAULTS[environment]["API_URL"])
        self.session = session or requests.Session()

    def post(self, entity: str, payload: dict, root: str, child: str) -> dict:
        encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        body = {"efiRadiusRequest": {"entityName": entity, "payload": encoded}}
        try:
            response = self.session.post(self.url, json=body, headers={"Accept": "application/json"}, timeout=60)
            response.raise_for_status()
            wrapper = response.json()["efiRadiusResponse"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise RadiusError(f"Radius transport or response error: {exc}") from exc
        if wrapper.get("entityName", "").strip() != entity:
            raise RadiusError("Unexpected Radius response entity")
        status = int(wrapper.get("statusCode", -1))
        if status != 1:
            raise RadiusError(f"Radius {entity} status {status}: {wrapper.get('errorMessage', '')}")
        try:
            decoded = json.loads(base64.b64decode(wrapper["payload"], validate=True))
            records = decoded[root][child]
            if len(records) != 1:
                raise ValueError("Expected one Radius record")
            record = records[0]
        except (KeyError, TypeError, ValueError) as exc:
            raise RadiusError("Invalid Radius acknowledgement payload") from exc
        if record.get("ErrorMessage") or record.get("Action", "").lower() == "failed":
            raise RadiusError(f"Radius {entity} rejected record: {record.get('ErrorMessage', '')}")
        return record


class AddressResolver:
    """Resolve Niagara N104 values through PV_CustAddress.AddressRef."""

    def __init__(self, environment: str):
        settings = ENV_DEFAULTS[environment]
        self.dsn = os.getenv("RADIUS_DSN", settings["RADIUS_DSN"])
        self.user = os.getenv("RADIUS_DB_USER", settings["RADIUS_DB_USER"])
        self.password = os.getenv("RADIUS_DB_PASS")
        if not self.password:
            raise ValueError("Set RADIUS_DB_PASS for the Radius address lookup")

    def resolve(self, comp_num: int, cust_code: str, address_ref: str, kind: str) -> int:
        if not address_ref:
            raise ValueError(f"Niagara {kind} N1*{kind} location ID is missing")
        conn = pyodbc.connect(f"DSN={self.dsn};UID={self.user};PWD={self.password}", timeout=15)
        try:
            rows = conn.cursor().execute(
                "SELECT AddressNum FROM PUB.PV_CustAddress WHERE CompNum=? AND CustCode=? AND AddressRef=?",
                comp_num, cust_code, address_ref,
            ).fetchall()
        finally:
            conn.close()
        if len(rows) != 1:
            raise ValueError(f"Expected one Radius address for {kind} AddressRef {address_ref!r}, found {len(rows)}")
        return int(rows[0][0])


def _mapped(mapping: dict, key: str, label: str):
    if not key or key not in mapping or not mapping[key]:
        raise ValueError(f"No {label} mapping for {key!r}")
    return mapping[key]


def build_sales_order(order, cfg: dict) -> dict:
    if order.po_type == "BK" and not cfg.get("allow_blanket_orders", False):
        raise ValueError("Blanket POs are disabled until their fulfillment behavior is configured")
    for name in ("comp_num", "plant_code", "cust_code", "bill_addr_num", "ship_addr_num"):
        if cfg.get(name) is None or cfg.get(name) == "":
            raise ValueError(f"Missing Radius setting: {name}")
    identifier = cfg.get("item_identifier", "VN")
    lines = []
    for index, line in enumerate(order.lines, 1):
        if not line.po_line.isdigit():
            raise ValueError(f"PO line {line.po_line!r} cannot map to numeric Radius CustPOLineNum")
        source_item = line.identifiers.get(identifier)
        item_code = _mapped(cfg.get("item_map", {}), source_item, "item")
        order_unit = _mapped(cfg.get("uom_map", {}), line.uom, "order UOM")
        price_unit = _mapped(cfg.get("price_unit_map", {}), line.uom, "price UOM")
        lines.append({"SOrderLineNum": index, "CustPOLineNum": int(line.po_line), "ItemCode": item_code,
                      "OrderedQty": float(line.quantity), "OrderUnitCode": order_unit,
                      "FCUnitPrice": float(line.price), "PriceUnitCode": price_unit,
                      "ReqDate": line.promised_ship or line.ship_no_later})
    ship_addr = cfg["ship_addr_num"]
    ref = f"{order.po_number}/{order.release}" if order.release else order.po_number
    header = {"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"], "SOrderNum": 0,
              "CustCode": cfg["cust_code"], "BillAddrNum": cfg["bill_addr_num"],
              "ShipAddrNum": ship_addr, "AckAddrNum": cfg.get("ack_addr_num", 0),
              "CustRef": order.po_number, "AddtCustRef": ref, "CurrCode": order.currency,
              "CustOrderDate": order.po_date, "SOrderDate": order.po_date,
              "XLSOrderLine": lines}
    return {"XLSOrders": {"XLSOrder": [header]}}


def build_shipping_request(order, cfg: dict, so_number: int, sales_record: dict) -> dict:
    source_lines = sales_record.get("XLSOrderLine") or []
    if len(source_lines) != len(order.lines):
        raise RadiusError("Sales Order acknowledgement did not confirm every line")
    ship_date = min((line.promised_ship or line.ship_no_later for line in order.lines if line.promised_ship or line.ship_no_later), default="")
    if not ship_date:
        raise ValueError("No line shipping date available")
    ship_lines = []
    for index, (edi_line, radius_line) in enumerate(zip(order.lines, source_lines), 1):
        if radius_line.get("ErrorMessage") or radius_line.get("Action", "").lower() == "failed":
            raise RadiusError(f"Sales Order line {index} failed: {radius_line.get('ErrorMessage', '')}")
        number = int(radius_line.get("SOrderLineNum") or 0)
        if not number:
            raise RadiusError("Sales Order response missing line number")
        ship_lines.append({"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"],
                           "ShipReqNum": 0, "ShipReqLineNum": index, "SOPlantCode": cfg["plant_code"],
                           "SOrderNum": so_number, "SOrderLineNum": number,
                           "ItemCode": radius_line["ItemCode"], "ShipQty": float(edi_line.quantity),
                           "CustPOLineNum": int(edi_line.po_line), "CustPOReleaseNum": order.release})
    return {"XLShipReqs": {"XLShipReq": [{"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"],
             "ShipReqNum": 0, "SOrderNum": so_number, "CustCode": cfg["cust_code"],
             "BillAddrNum": cfg["bill_addr_num"],
             "ShipAddrNum": cfg["ship_addr_num"],
             "ShipDate": ship_date, "XLShipReqLine": ship_lines}]}}
