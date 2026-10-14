import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from niagara_edi.edi import EDIError, parse_850
from niagara_edi.service import DuplicateOrder, initialize, process_file


def sample_edi():
    return (Path(__file__).parent / "fixtures" / "synthetic_niagara_850.edi").read_bytes()


class Resolver:
    def resolve(self, comp, cust, ref, kind):
        assert (comp, cust) == (2, "NIA01")
        return {"BILL1": 101, "HOU": 202}[ref]


class Radius:
    def __init__(self):
        self.calls = []

    def post(self, entity, payload, root, child):
        self.calls.append((entity, payload))
        if entity == "XLinkAPISOrder":
            return {"SOrderNum": 9001, "Action": "Add", "XLSOrderLine": [
                {"SOrderLineNum": 1, "ItemCode": "ITEM001", "Action": "Add"}]}
        return {"ShipReqNum": 8001, "Action": "Add", "XLShipReqLine": [{"Action": "Add"}]}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.edi = base / "one.edi"
        data = sample_edi()
        self.edi.write_bytes(data)
        self.meta = base / "one.edi.as2.json"
        self.meta.write_text(json.dumps({"as2_sender": "NIAGARA", "as2_receiver": "OURS",
            "authentication_status": "verified", "decryption_status": "verified",
            "mic_status": "verified", "sha256": hashlib.sha256(data).hexdigest()}))
        self.cfg = {"environment": "TEST", "comp_num": 2, "plant_code": "2", "cust_code": "NIA01",
            "item_identifier": "VN", "item_map": {"VEN001": "ITEM001"},
            "uom_map": {"EA": "EACH"}, "price_unit_map": {"EA": "EACH"},
            "allowed_as2_sender": "NIAGARA", "allowed_as2_receiver": "OURS"}
        self.db = base / "orders.sqlite3"

    def test_parse_and_post_then_replay(self):
        parsed = parse_850(self.edi.read_bytes())
        self.assertEqual(parsed[0].bill_to_id, "BILL1")
        radius = Radius()
        result = process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=radius, resolver=Resolver())
        self.assertEqual(result[0]["state"], "complete")
        self.assertEqual(radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]["BillAddrNum"], 101)
        so = radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]
        self.assertNotIn("SOrderDate", so)
        self.assertEqual(so["SOSourceCode"], "EDI")
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["ShipAddrNum"], 202)
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["ShipDate"], "2026-10-14")
        repeat = process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=radius, resolver=Resolver())
        self.assertEqual(repeat[0]["state"], "already_complete")
        self.assertEqual(len(radius.calls), 2)

    def test_mismatched_se_rejected(self):
        with self.assertRaises(EDIError):
            parse_850(self.edi.read_bytes().replace(b"SE*13*0001", b"SE*12*0001"))

    def test_x12_partner_profile_routes_by_isa_sender(self):
        profile = {key: self.cfg[key] for key in
                   ("comp_num", "plant_code", "cust_code", "item_identifier", "item_map", "uom_map", "price_unit_map")}
        profile.update({"as2_sender": "NIAGARA", "as2_receiver": "OURS", "isa_receiver": "OURID"})
        self.cfg.pop("cust_code")
        self.cfg.pop("allowed_as2_sender")
        self.cfg.pop("allowed_as2_receiver")
        self.cfg["x12_partners"] = {"027948264": profile}
        radius = Radius()
        result = process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=radius, resolver=Resolver())
        self.assertEqual(result[0]["state"], "complete")
        self.assertEqual(radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]["CustCode"], "NIA01")

    def test_controlled_file_drop_profile_needs_no_as2_receipt(self):
        profile = {key: self.cfg[key] for key in
                   ("comp_num", "plant_code", "cust_code", "item_identifier", "item_map", "uom_map", "price_unit_map")}
        profile.update({"transport": "trusted_drop", "isa_receiver": "OURID"})
        self.cfg["x12_partners"] = {"027948264": profile}
        self.meta.unlink()
        result = process_file(self.edi, self.meta, self.cfg, self.db, submit=True,
                              client=Radius(), resolver=Resolver())
        self.assertEqual(result[0]["state"], "complete")

    def test_changed_po_requires_reconciliation(self):
        process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=Radius(), resolver=Resolver())
        data = self.edi.read_bytes().replace(b"2.50", b"3.50")
        self.edi.write_bytes(data)
        meta = json.loads(self.meta.read_text())
        meta["sha256"] = hashlib.sha256(data).hexdigest()
        self.meta.write_text(json.dumps(meta))
        with self.assertRaises(DuplicateOrder):
            process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=Radius(), resolver=Resolver())

    def test_existing_order_database_gains_shipping_request_number_list(self):
        import sqlite3
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("""CREATE TABLE orders (
                business_key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
                so_number INTEGER, shipreq_number INTEGER, error TEXT)""")
            conn.commit()
        conn = initialize(self.db)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
            self.assertIn("shipreq_numbers", columns)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
