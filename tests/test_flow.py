import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from niagara_edi.edi import EDIError, parse_850
from niagara_edi.service import DuplicateOrder, process_file


def sample_edi():
    isa = "ISA*00*          *00*          *01*027948264      *ZZ*OURID          *261008*1200*U*00401*000000001*0*T*>~"
    assert len(isa) == 106
    segments = [
        "GS*PO*027948264*OURID*20261008*1200*1*X*004010",
        "ST*850*0001", "BEG*00*SA*PO123**20261008", "CUR*SE*USD",
        "REF*8N*NBL", "N9*VR*120201", "N1*BT*Niagara Billing*92*BILL1",
        "N1*ST*Niagara Houston*92*HOU", "PO1*1*25*EA*2.50**BP*BP001*VN*VEN001",
        "REF*SS*1", "DTM*038*20261015", "DTM*079*20261014",
        "CTT*1*25", "SE*13*0001", "GE*1*1", "IEA*1*000000001",
    ]
    return (isa + "~".join(segments) + "~").encode("ascii")


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
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["ShipAddrNum"], 202)
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["ShipDate"], "2026-10-14")
        repeat = process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=radius, resolver=Resolver())
        self.assertEqual(repeat[0]["state"], "already_complete")
        self.assertEqual(len(radius.calls), 2)

    def test_mismatched_se_rejected(self):
        with self.assertRaises(EDIError):
            parse_850(self.edi.read_bytes().replace(b"SE*13*0001", b"SE*12*0001"))

    def test_changed_po_requires_reconciliation(self):
        process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=Radius(), resolver=Resolver())
        data = self.edi.read_bytes().replace(b"2.50", b"3.50")
        self.edi.write_bytes(data)
        meta = json.loads(self.meta.read_text())
        meta["sha256"] = hashlib.sha256(data).hexdigest()
        self.meta.write_text(json.dumps(meta))
        with self.assertRaises(DuplicateOrder):
            process_file(self.edi, self.meta, self.cfg, self.db, submit=True, client=Radius(), resolver=Resolver())


if __name__ == "__main__":
    unittest.main()
