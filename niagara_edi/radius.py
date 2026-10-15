import base64
import binascii
import json
import logging
import os
from uuid import uuid4

import requests
import pyodbc


ENV_DEFAULTS = {
    "TEST": {"API_URL": "http://P-RAD-FSM-EF.fsm.corp:8081/radadapter/radius/api", "RADIUS_DSN": "Radius_NewLive", "RADIUS_DB_USER": "vision"},
    "LIVE": {"API_URL": "http://fsmradius:8081/radadapter/radius/api", "RADIUS_DSN": "Radius_Live64", "RADIUS_DB_USER": "vision"},
}
logger = logging.getLogger("niagara_edi.radius")


class RadiusError(RuntimeError):
    pass


class RadiusRejected(RadiusError):
    """Radius returned a definitive non-success status for a submitted record."""


def _response_errors(value, path=""):
    errors = []
    if isinstance(value, dict):
        messages = [value.get(key) for key in ("ErrorMessage", "errorMessage") if value.get(key)]
        for message in messages:
            errors.append(f"{path}: {message}" if path else str(message))
        if str(value.get("Action", "")).lower() == "failed" and not messages:
            errors.append(f"{path}: failed" if path else "failed")
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                errors.extend(_response_errors(child, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_response_errors(child, f"{path}[{index}]"))
    return errors


class RadiusClient:
    def __init__(self, environment: str, session=None):
        if environment not in ENV_DEFAULTS:
            raise ValueError("environment must be TEST or LIVE")
        self.url = os.getenv("RADIUS_API_URL", ENV_DEFAULTS[environment]["API_URL"])
        self.session = session or requests.Session()

    def post(self, entity: str, payload: dict, root: str, child: str) -> dict:
        request_id = uuid4().hex[:12]
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s POST %s %s\n%s", request_id, entity, self.url,
                         json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        body = {"efiRadiusRequest": {"entityName": entity, "payload": encoded}}
        try:
            response = self.session.post(self.url, json=body, headers={"Accept": "application/json"}, timeout=60)
        except requests.RequestException as exc:
            logger.debug("%s TRANSPORT ERROR %s", request_id, exc)
            raise RadiusError(f"Radius transport or response error: {exc}") from exc
        try:
            response_data = response.json()
            wrapper = response_data["efiRadiusResponse"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("%s INVALID RESPONSE HTTP %s: %s", request_id,
                         getattr(response, "status_code", "unknown"), getattr(response, "text", ""))
            raise RadiusError(f"Radius transport or response error: {exc}") from exc
        if not isinstance(wrapper, dict):
            raise RadiusError("Invalid Radius response wrapper")
        if wrapper.get("entityName", "").strip() != entity:
            raise RadiusError("Unexpected Radius response entity")
        try:
            status = int(wrapper.get("statusCode", -1))
        except (TypeError, ValueError) as exc:
            raise RadiusError("Invalid Radius response status") from exc
        decoded = None
        if wrapper.get("payload"):
            try:
                decoded = json.loads(base64.b64decode(wrapper["payload"], validate=True))
            except (ValueError, TypeError, binascii.Error) as exc:
                logger.debug("%s INVALID BASE64 RESPONSE PAYLOAD: %s", request_id, exc)
                if status == 1:
                    raise RadiusError("Invalid Radius acknowledgement payload") from exc
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s RESPONSE %s HTTP %s status %s\n%s", request_id, entity,
                         getattr(response, "status_code", "unknown"), status,
                         json.dumps({"entityName": wrapper.get("entityName"),
                                     "statusCode": wrapper.get("statusCode"),
                                     "errorMessage": wrapper.get("errorMessage"),
                                     "payload": decoded}, indent=2, ensure_ascii=False, default=str))
        errors = _response_errors(decoded)
        wrapper_error = wrapper.get("errorMessage")
        if wrapper_error and not any(str(wrapper_error) in error for error in errors):
            errors.insert(0, str(wrapper_error))
        if errors:
            logger.debug("%s ERRORS %s", request_id, json.dumps(list(dict.fromkeys(errors)), ensure_ascii=False))
        if status != 1:
            raise RadiusRejected(f"Radius {entity} status {status}: {'; '.join(dict.fromkeys(errors)) or 'No error detail returned'}")
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RadiusError(f"Radius HTTP error: {exc}") from exc
        try:
            records = decoded[root][child]
            if len(records) != 1:
                raise ValueError("Expected one Radius record")
            record = records[0]
        except (KeyError, TypeError, ValueError) as exc:
            raise RadiusError("Invalid Radius acknowledgement payload") from exc
        if errors:
            raise RadiusError(f"Radius {entity} rejected record: {'; '.join(dict.fromkeys(errors))}")
        return record


class AddressResolver:
    """Resolve customer location codes through PV_CustAddress.AddressRef."""

    def __init__(self, environment: str):
        settings = ENV_DEFAULTS[environment]
        self.dsn = os.getenv("RADIUS_DSN", settings["RADIUS_DSN"])
        self.user = os.getenv("RADIUS_DB_USER", settings["RADIUS_DB_USER"])
        self.password = os.getenv("RADIUS_DB_PASS")
        if not self.password:
            raise ValueError("Set RADIUS_DB_PASS for the Radius address lookup")

    def resolve(self, comp_num: int, cust_code: str, address_ref: str, kind: str) -> int:
        if not address_ref:
            raise ValueError(f"{kind} address reference is missing")
        try:
            conn = pyodbc.connect(f"DSN={self.dsn};UID={self.user};PWD={self.password}", timeout=15)
        except pyodbc.Error as exc:
            raise RadiusError(f"Radius address lookup connection failed for {kind} AddressRef {address_ref!r}: {exc}") from exc
        try:
            try:
                rows = conn.cursor().execute(
                    "SELECT AddressNum FROM PUB.PV_CustAddress WHERE CompNum=? AND CustCode=? AND AddressRef=?",
                    comp_num, cust_code, address_ref,
                ).fetchall()
            except pyodbc.Error as exc:
                raise RadiusError(f"Radius address lookup failed for {kind} AddressRef {address_ref!r}: {exc}") from exc
        finally:
            conn.close()
        if len(rows) != 1:
            raise ValueError(f"Expected one Radius address for {kind} AddressRef {address_ref!r} and customer {cust_code!r} in company {comp_num}, found {len(rows)}")
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
    lines = []
    for index, line in enumerate(order.lines, 1):
        if not line.po_line.isdigit():
            raise ValueError(f"PO line {line.po_line!r} cannot map to numeric Radius CustPOLineNum")
        if order.source_format in {"radius_fixed", "mapped_customer"}:
            customer_item = line.identifiers.get("CI")
            if not customer_item:
                raise ValueError(f"VAN line {line.po_line} is missing CustItemRef")
            item_fields = {"CustItemRef": customer_item}
        else:
            identifier = cfg.get("item_identifier", "VN")
            source_item = line.identifiers.get(identifier)
            item_fields = {"ItemCode": _mapped(cfg.get("item_map", {}), source_item, "item")}
        order_unit = _mapped(cfg.get("uom_map", {}), line.uom, "order UOM")
        price_unit = _mapped(cfg.get("price_unit_map", {}), line.price_uom or line.uom, "price UOM")
        lines.append({"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"], "SOrderNum": 0,
                      "SOrderLineNum": line.radius_line_num or index, "CustPOLineNum": int(line.po_line), **item_fields,
                      "OrderedQty": float(line.quantity), "OrderUnitCode": order_unit,
                      "FCUnitPrice": float(line.price), "PriceUnitCode": price_unit,
                      "ReqDate": line.promised_ship or line.ship_no_later})
    ship_addr = cfg["ship_addr_num"]
    header = {"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"], "SOrderNum": 0,
              "CustCode": cfg["cust_code"], "BillAddrNum": cfg["bill_addr_num"],
              "ShipAddrNum": ship_addr, "AckAddrNum": cfg.get("ack_addr_num", 0),
              "CustRef": order.po_number, "CurrCode": order.currency,
              "CustOrderDate": order.po_date,
              "SOSourceCode": "EDI",
              "XLSOrderLine": lines}
    if order.source_format in {"radius_fixed", "mapped_customer"}:
        if getattr(order, "cust_req_date", ""):
            header["CustReqDate"] = order.cust_req_date
        if getattr(order, "addt_cust_ref", ""):
            header["AddtCustRef"] = order.addt_cust_ref
        if getattr(order, "price_terms", ""):
            header["TermsCode"] = order.price_terms
    else:
        header["AddtCustRef"] = f"{order.po_number}/{order.release}" if order.release else order.po_number
    return {"XLSOrders": {"XLSOrder": [header]}}


def build_shipping_request(order, cfg: dict, so_number: int, sales_record: dict) -> dict:
    source_lines = sales_record.get("XLSOrderLine") or []
    if len(source_lines) != len(order.lines):
        raise RadiusError("Sales Order acknowledgement did not confirm every line")
    by_number = {}
    for radius_line in source_lines:
        try:
            number = int(radius_line.get("SOrderLineNum") or 0)
        except (TypeError, ValueError) as exc:
            raise RadiusError("Sales Order response contains an invalid line number") from exc
        if not number or number in by_number:
            raise RadiusError("Sales Order response has missing or duplicate line numbers")
        by_number[number] = radius_line
    ship_date = getattr(order, "shipping_date", "") or min((line.promised_ship or line.ship_no_later for line in order.lines if line.promised_ship or line.ship_no_later), default="")
    if not ship_date:
        raise ValueError("No line shipping date available")
    ship_lines = []
    for index, edi_line in enumerate(order.lines, 1):
        expected_number = edi_line.radius_line_num or index
        radius_line = by_number.get(expected_number)
        if radius_line is None:
            raise RadiusError(f"Sales Order response missing line {expected_number}")
        if radius_line.get("ErrorMessage") or radius_line.get("Action", "").lower() == "failed":
            raise RadiusError(f"Sales Order line {index} failed: {radius_line.get('ErrorMessage', '')}")
        number = expected_number
        item_code = radius_line.get("ItemCode")
        if not item_code:
            raise RadiusError(f"Sales Order response did not resolve ItemCode for line {edi_line.po_line}")
        ship_line = {"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"],
                           "ShipReqNum": 0, "ShipReqLineNum": index, "SOPlantCode": cfg["plant_code"],
                           "SOrderNum": so_number, "SOrderLineNum": number,
                           "ItemCode": item_code, "ShipQty": float(edi_line.ship_quantity if edi_line.ship_quantity is not None else edi_line.quantity),
                           "CustPOLineNum": int(edi_line.po_line), "CustPOReleaseNum": order.release}
        if order.source_format in {"radius_fixed", "mapped_customer"} and edi_line.shipment_number:
            ship_line["CustRef"] = edi_line.shipment_number
        ship_lines.append(ship_line)
    return {"XLShipReqs": {"XLShipReq": [{"CompNum": cfg["comp_num"], "PlantCode": cfg["plant_code"],
             "ShipReqNum": 0, "SOrderNum": so_number, "CustCode": cfg["cust_code"],
             "BillAddrNum": cfg["bill_addr_num"],
             "ShipAddrNum": cfg["ship_addr_num"],
             "ShipDate": ship_date, "XLShipReqLine": ship_lines}]}}
