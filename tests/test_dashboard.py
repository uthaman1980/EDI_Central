import base64
import hashlib
import json
import tempfile
from contextlib import closing
from pathlib import Path
import unittest

from niagara_edi.audit import connect
from niagara_edi.__main__ import scan_inbox
from niagara_edi.dashboard import create_app
from niagara_edi.service import process_file
from test_flow import Radius, Resolver, sample_edi


class DashboardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
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
        self.app = create_app(self.db, username="admin", password="test-secret")
        self.client = self.app.test_client()
        token = base64.b64encode(b"admin:test-secret").decode()
        self.auth = {"Authorization": f"Basic {token}"}

    def test_requires_auth_and_shows_completed_file(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/", headers={"Authorization": "Basic YTpi"}).status_code, 401)
        process_file(self.edi, self.meta, self.cfg, self.db,
                     submit=True, client=Radius(), resolver=Resolver())
        response = self.client.get("/", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PO123", response.data)
        self.assertIn(b"9001", response.data)
        self.assertIn(b"8001", response.data)
        self.assertIn(b"Completed", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        with closing(connect(self.db)) as conn:
            file_id = conn.execute("SELECT id FROM files").fetchone()[0]
        detail = self.client.get(f"/files/{file_id}", headers=self.auth)
        self.assertIn(b"Shipping Request 8001 created", detail.data)
        self.assertIn(b"Confirmed POST records", detail.data)
        self.assertIn(b"Decoded Radius response", detail.data)
        self.assertIn(b"POST payload", detail.data)
        self.assertIn(b"PO PO123", detail.data)
        self.assertIn(b"SO 9001", detail.data)
        self.assertIn(b"8001", self.client.get("/?q=8001", headers=self.auth).data)

    def test_rejected_file_records_error(self):
        meta = json.loads(self.meta.read_text())
        meta["mic_status"] = "failed"
        self.meta.write_text(json.dumps(meta))
        with self.assertRaises(ValueError):
            process_file(self.edi, self.meta, self.cfg, self.db,
                         submit=False, resolver=Resolver())
        response = self.client.get("/?status=rejected", headers=self.auth)
        self.assertIn(b"Rejected", response.data)
        self.assertIn(b"one.edi", response.data)
        with closing(connect(self.db)) as conn:
            row = conn.execute("SELECT status,error FROM files").fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertIn("AS2 MIC", row["error"])

    def test_watcher_tracks_pending_then_archives_success(self):
        inbox = self.edi.parent / "inbox"
        inbox.mkdir()
        edi = inbox / self.edi.name
        meta = inbox / self.meta.name
        edi.write_bytes(self.edi.read_bytes())
        self.assertEqual(scan_inbox(inbox, self.cfg, self.db, submit=True,
                                    client=Radius(), resolver=Resolver()),
                         [(edi.name, "awaiting_receipt")])
        meta.write_text(self.meta.read_text())
        self.assertEqual(scan_inbox(inbox, self.cfg, self.db, submit=True,
                                    client=Radius(), resolver=Resolver()),
                         [(edi.name, "complete")])
        self.assertFalse(edi.exists())
        self.assertEqual(len(list((inbox.parent / "processed").glob("*.edi"))), 1)
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT status FROM files").fetchone()[0], "complete")

    def test_watcher_accepts_controlled_file_drop_without_receipt(self):
        profile = {key: self.cfg[key] for key in
                   ("comp_num", "plant_code", "cust_code", "item_identifier", "item_map", "uom_map", "price_unit_map")}
        profile.update({"transport": "trusted_drop", "isa_receiver": "OURID"})
        self.cfg["x12_partners"] = {"027948264": profile}
        inbox = self.edi.parent / "inbox"
        inbox.mkdir()
        (inbox / self.edi.name).write_bytes(self.edi.read_bytes())
        result = scan_inbox(inbox, self.cfg, self.db, submit=True, client=Radius(), resolver=Resolver())
        self.assertEqual(result, [(self.edi.name, "complete")])
        self.assertEqual(len(list((inbox.parent / "processed").glob("*.edi"))), 1)


if __name__ == "__main__":
    unittest.main()
