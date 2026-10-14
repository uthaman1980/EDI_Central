import json
import tempfile
import unittest
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from niagara_edi.__main__ import scan_inbox
from niagara_edi.edi import EDIError
from niagara_edi.fixed850 import parse_fixed_850
from niagara_edi.radius import AddressResolver, RadiusRejected


SAMPLE = Path(__file__).parent / "fixtures" / "synthetic_fixed_850.txt"
ITEMS = ["TESTREF1", "TESTREF2", "TESTREF3", "TESTREF4", "TESTREF5"]


class Radius:
    def __init__(self, fail_shipping=False, reverse_so_lines=False, missing_item=False, fail_shipping_at=None):
        self.calls = []
        self.fail_shipping = fail_shipping
        self.reverse_so_lines = reverse_so_lines
        self.missing_item = missing_item
        self.fail_shipping_at = fail_shipping_at

    def post(self, entity, payload, root, child):
        self.calls.append((entity, payload))
        if entity == "XLinkAPISOrder":
            lines = payload["XLSOrders"]["XLSOrder"][0]["XLSOrderLine"]
            resolved = [
                {"SOrderLineNum": line["SOrderLineNum"],
                 "ItemCode": line.get("ItemCode") or f"ITEM{ITEMS.index(line['CustItemRef']) + 1}", "Action": "Add"}
                for line in lines]
            if self.missing_item:
                resolved[0].pop("ItemCode")
            return {"SOrderNum": 9001, "Action": "Add",
                    "XLSOrderLine": list(reversed(resolved)) if self.reverse_so_lines else resolved}
        request_number = sum(call[0] == "XLinkAPIShipReq" for call in self.calls)
        if self.fail_shipping or request_number == self.fail_shipping_at:
            raise RuntimeError("Shipping API unavailable")
        lines = payload["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"]
        return {"ShipReqNum": 8000 + request_number, "Action": "Add", "XLShipReqLine": [{"Action": "Add"} for _ in lines]}


class Resolver:
    def __init__(self):
        self.calls = []

    def resolve(self, comp_num, cust_code, address_ref, kind):
        self.calls.append((comp_num, cust_code, address_ref, kind))
        return {"US01": 101, "1K15": 202}[address_ref]


class AddressLookupTests(unittest.TestCase):
    def test_lookup_filters_company_customer_and_address_reference(self):
        class Cursor:
            def execute(self, sql, *params):
                self.query = (sql, params)
                return self

            def fetchall(self):
                return [(101,)]

        class Connection:
            def __init__(self):
                self.cursor_obj = Cursor()
                self.closed = False

            def cursor(self):
                return self.cursor_obj

            def close(self):
                self.closed = True

        connection = Connection()
        with patch.dict("os.environ", {"RADIUS_DB_PASS": "test-secret"}):
            with patch("niagara_edi.radius.pyodbc.connect", return_value=connection) as connect:
                result = AddressResolver("TEST").resolve(2, "PEP01", "US01", "BT")
        self.assertEqual(result, 101)
        self.assertEqual(connection.cursor_obj.query[0],
                         "SELECT AddressNum FROM PUB.PV_CustAddress WHERE CompNum=? AND CustCode=? AND AddressRef=?")
        self.assertEqual(connection.cursor_obj.query[1], (2, "PEP01", "US01"))
        self.assertTrue(connection.closed)
        self.assertEqual(connect.call_args.kwargs["timeout"], 15)


class Fixed850Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.inbox = self.base / "inbox"
        self.inbox.mkdir()
        self.path = self.inbox / SAMPLE.name
        self.path.write_bytes(SAMPLE.read_bytes())
        self.state = self.base / "state.sqlite3"
        self.resolver = Resolver()
        date_patch = patch("niagara_edi.service.current_date", return_value=date(2026, 10, 7))
        date_patch.start()
        self.addCleanup(date_patch.stop)
        self.cfg = {"environment": "TEST", "customers": {"PEP01": {
            "cust_code": "PEP01", "comp_num": 2, "plant_code": "2", "item_identifier": "CI",
            "item_map": {},
            "uom_map": {"EA": "EA"}, "price_unit_map": {"THOUS": "THOUS"},
            "address_map": {"US01": 999, "1K15": 999},
            "shipping_request_mode": "per_line"}}}

    def test_sample_parses_and_posts_both_entities_then_moves_to_processed(self):
        order = parse_fixed_850(self.path.read_bytes())[0]
        self.assertEqual((order.sender, order.po_number, len(order.lines)), ("PEP01", "TEST-PO-850", 5))
        radius = Radius()
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True, client=radius, resolver=self.resolver),
                         [(SAMPLE.name, "complete")])
        self.assertFalse(self.path.exists())
        self.assertEqual(len(list((self.base / "processed").glob("*.txt"))), 1)
        so = radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]
        ship = radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]
        self.assertEqual(len(radius.calls), 6)
        self.assertEqual((so["CustCode"], so["BillAddrNum"], so["ShipAddrNum"]), ("PEP01", 101, 202))
        self.assertNotIn("SOrderDate", so)
        self.assertEqual((so["SOSourceCode"], so["CustOrderDate"], so["CustReqDate"]),
                         ("EDI", "2026-10-07", "2026-10-07"))
        self.assertNotIn("AddtCustRef", so)
        self.assertEqual(so["TermsCode"], "Net 60")
        self.assertEqual(self.resolver.calls, [(2, "PEP01", "US01", "BT"), (2, "PEP01", "1K15", "ST")])
        self.assertEqual((so["XLSOrderLine"][0]["SOrderLineNum"], so["XLSOrderLine"][0]["PriceUnitCode"]), (1, "THOUS"))
        self.assertEqual([line["SOrderLineNum"] for line in so["XLSOrderLine"]], [1, 2, 3, 4, 5])
        self.assertEqual([line["CompNum"] for line in so["XLSOrderLine"]], [2] * 5)
        self.assertEqual([line["PlantCode"] for line in so["XLSOrderLine"]], ["2"] * 5)
        self.assertEqual([line["CustPOLineNum"] for line in so["XLSOrderLine"]], [10, 20, 30, 40, 50])
        self.assertEqual(so["XLSOrderLine"][0]["OrderUnitCode"], "EA")
        self.assertEqual((so["XLSOrderLine"][0]["FCUnitPrice"], so["XLSOrderLine"][0]["ReqDate"]),
                         (10.0, "2026-10-07"))
        self.assertEqual(so["XLSOrderLine"][0]["CustItemRef"], "TESTREF1")
        self.assertNotIn("ItemCode", so["XLSOrderLine"][0])
        self.assertEqual((ship["ShipDate"], ship["XLShipReqLine"][0]["ShipQty"]), ("2026-10-07", 1000.0))
        self.assertEqual(ship["XLShipReqLine"][0]["ItemCode"], "ITEM1")
        self.assertEqual(ship["XLShipReqLine"][0]["CustRef"], "TEST-PO-850")
        self.assertEqual([call[1]["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"][0]["CustPOLineNum"]
                          for call in radius.calls[1:]], [10, 20, 30, 40, 50])
        self.assertTrue(all(len(call[1]["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"]) == 1
                            for call in radius.calls[1:]))
        with closing(sqlite3.connect(self.state)) as conn:
            numbers = conn.execute("SELECT shipreq_numbers FROM orders").fetchone()[0]
        self.assertEqual(json.loads(numbers), [8001, 8002, 8003, 8004, 8005])
        with closing(sqlite3.connect(self.state)) as conn:
            records = conn.execute("""SELECT entity,po_line,so_number,shipreq_number,request_json,response_json
                FROM radius_records ORDER BY id""").fetchall()
        self.assertEqual(len(records), 6)
        self.assertEqual((records[0][0], records[0][2]), ("XLinkAPISOrder", 9001))
        self.assertEqual([(row[1], row[3]) for row in records[1:]],
                         [("10", 8001), ("20", 8002), ("30", 8003), ("40", 8004), ("50", 8005)])
        self.assertEqual(json.loads(records[0][4])["XLSOrders"]["XLSOrder"][0]["CustRef"], "TEST-PO-850")
        self.assertEqual(json.loads(records[0][5])["SOrderNum"], 9001)

    def test_preview_keeps_file_in_inbox(self):
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=False, resolver=self.resolver),
                         [(SAMPLE.name, "previewed")])
        self.assertTrue(self.path.exists())

    def test_past_dates_use_today_and_record_each_change(self):
        radius = Radius()
        original = self.path.read_bytes()
        with patch("niagara_edi.service.current_date", return_value=date(2026, 10, 14)):
            self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True,
                                        client=radius, resolver=self.resolver), [(SAMPLE.name, "complete")])
        so = radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]
        self.assertEqual(so["CustReqDate"], "2026-10-14")
        self.assertEqual([line["ReqDate"] for line in so["XLSOrderLine"]], ["2026-10-14"] * 5)
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["ShipDate"], "2026-10-14")
        processed = next((self.base / "processed").glob("*.txt"))
        self.assertEqual(processed.read_bytes(), original)
        with closing(sqlite3.connect(self.state)) as conn:
            events = [row[0] for row in conn.execute(
                "SELECT message FROM file_events WHERE stage='Dates' ORDER BY id")]
        self.assertEqual(len(events), 7)
        self.assertTrue(all("2026-10-07 -> 2026-10-14" in event for event in events))
        self.assertTrue(any("line 50 required date" in event for event in events))

    def test_definitive_sales_order_rejection_clears_pending_attempt(self):
        class RejectingRadius:
            def post(self, *args):
                raise RadiusRejected("Radius XLinkAPISOrder status 9: Invalid billing address (917)")

        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True,
                                    client=RejectingRadius(), resolver=self.resolver), [(SAMPLE.name, "failed")])
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT status FROM files").fetchone()[0], "rejected")

    def test_shipping_failure_never_moves_to_processed(self):
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True, client=Radius(True), resolver=self.resolver),
                         [(SAMPLE.name, "failed")])
        self.assertEqual(list((self.base / "processed").iterdir()), [])
        self.assertEqual(len(list((self.base / "failed").glob("*.txt"))), 1)

    def test_reordered_radius_lines_keep_customer_items_aligned(self):
        radius = Radius(reverse_so_lines=True)
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True, client=radius, resolver=self.resolver),
                         [(SAMPLE.name, "complete")])
        ship_lines = [call[1]["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"][0]
                      for call in radius.calls[1:]]
        self.assertEqual([line["ItemCode"] for line in ship_lines],
                         ["ITEM1", "ITEM2", "ITEM3", "ITEM4", "ITEM5"])

    def test_partial_shipping_failure_records_created_requests_and_stops_retry(self):
        radius = Radius(fail_shipping_at=3)
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True,
                                    client=radius, resolver=self.resolver), [(SAMPLE.name, "failed")])
        with closing(sqlite3.connect(self.state)) as conn:
            state, numbers = conn.execute("SELECT state,shipreq_numbers FROM orders").fetchone()
        self.assertEqual((state, json.loads(numbers)), ("ship_pending", [8001, 8002]))
        self.path.write_bytes(SAMPLE.read_bytes())
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True,
                                    client=radius, resolver=self.resolver), [(SAMPLE.name, "failed")])
        self.assertEqual(len(radius.calls), 4)

    def test_unresolved_item_stops_before_shipping_post(self):
        radius = Radius(missing_item=True)
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True, client=radius, resolver=self.resolver),
                         [(SAMPLE.name, "failed")])
        self.assertEqual(len(radius.calls), 1)
        self.assertEqual(list((self.base / "processed").iterdir()), [])

    def test_control_total_rejects_truncated_file(self):
        with self.assertRaises(EDIError):
            parse_fixed_850(self.path.read_bytes().replace(b"000014", b"000013"))

    def test_unknown_customer_does_not_post(self):
        self.cfg["customers"].clear()
        radius = Radius()
        self.assertEqual(scan_inbox(self.inbox, self.cfg, self.state, submit=True, client=radius),
                         [(SAMPLE.name, "failed")])
        self.assertEqual(radius.calls, [])


if __name__ == "__main__":
    unittest.main()
