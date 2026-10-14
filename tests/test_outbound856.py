import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niagara_edi.central import (create_capability, create_partner, migrate, scan_observer_once,
                                 session, set_enabled, set_partner_enabled)
from niagara_edi.dashboard import create_app
from niagara_edi.outbound856 import PROFILE, parse_outbound_856


def record(tag, size, fields):
    chars = list(tag.ljust(size))
    for start, width, value in fields:
        chars[start - 1:start - 1 + width] = str(value).rjust(width)
    return "".join(chars)


def sample(customer="CUST1"):
    rows = ["EDI856OutBound", record("SNBOL", 321, [(8, 6, 2), (14, 8, "2"), (22, 8, 9001)])]
    for sr, so in [(7001, 8001), (7002, 8002)]:
        rows.append(record("SNHEAD", 5108, [(8, 6, 2), (14, 8, "2"), (22, 8, sr),
                                             (30, 8, customer), (101, 8, 9001)]))
        rows.append(record("SNHED2", 1494, []))
        rows.append(record("SNLINE", 2675, [(7, 4, 1), (19, 8, so), (27, 4, 1), (113, 30, "PO-TEST")]))
        rows.append(record("SNPICK", 226, []))
        rows.append(record("SNBRAK", 403, []))
    rows.append(record("TOTALS", 24, [(7, 6, 1), (13, 6, 2), (19, 6, len(rows) + 1)]))
    return "\r\n".join(rows).encode("ascii")


class Outbound856Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "state.sqlite3"
        migrate(self.db)

    def setup_capability(self, environment="TEST"):
        folder = self.base / "radius"
        folder.mkdir()
        with session(self.db) as conn:
            partner = create_partner(conn, partner_code="CUST1", display_name="Synthetic customer",
                                     radius_customer_code="CUST1", company_number=2, plant_code="2")
            set_partner_enabled(conn, partner, True)
            cap = create_capability(conn, partner_id=partner, environment=environment,
                                    direction="OUTBOUND", message_type="856", route_class="VAN",
                                    source_format=PROFILE, radius_format="Standard Outbound 856",
                                    format_version="1", radius_message_active=True,
                                    radius_contract_verified=True, radius_outbound_folder=str(folder),
                                    filename_pattern="*.txt")
            set_enabled(conn, cap, True, allowed_roots=[self.base])
        return cap, folder

    def test_parser_preserves_multiple_shipping_notes_and_sales_references(self):
        docs = parse_outbound_856(sample(), customer="CUST1", company=2, plant="2")
        self.assertEqual([x["shipreq"] for x in docs], [7001, 7002])
        self.assertEqual([x["lines"][0]["so"] for x in docs], [8001, 8002])
        self.assertEqual({x["bol"] for x in docs}, {9001})
        self.assertEqual(docs[0]["lines"][0]["po"], "PO-TEST")

    def test_parser_rejects_bad_counts_foreign_customer_and_truncated_reference(self):
        with self.assertRaisesRegex(ValueError, "TOTALS"):
            parse_outbound_856(sample()[:-6] + b"999999")
        with self.assertRaisesRegex(ValueError, "customer/company/plant"):
            parse_outbound_856(sample("OTHER"), customer="CUST1")
        rows = sample().splitlines()
        rows[4] = rows[4][:30]
        with self.assertRaisesRegex(ValueError, "Truncated SNLINE"):
            parse_outbound_856(b"\r\n".join(rows))

    def test_observer_indexes_once_and_preserves_source_and_pickup_state(self):
        cap, folder = self.setup_capability()
        data = sample()
        path = folder / "shipment.txt"
        path.write_bytes(data)
        original = path.stat()
        for _ in range(3):
            scan_observer_once(self.db, cap, stable_seconds=0, allowed_roots=[self.base])
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(path.stat().st_mtime_ns, original.st_mtime_ns)
        with session(self.db) as conn:
            self.assertEqual(tuple(conn.execute("SELECT state,parse_status FROM central_artifacts").fetchone()),
                             ("PICKUP_UNVERIFIED", "PARSED"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_documents").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_document_lines").fetchone()[0], 2)
            artifact = conn.execute("SELECT id FROM central_artifacts").fetchone()[0]
        app = create_app(self.db, username="a", password="b", config_path=self.base / "config.json")
        client = app.test_client()
        auth = {"Authorization": "Basic " + base64.b64encode(b"a:b").decode()}
        page = client.get(f"/central/artifacts/{artifact}", headers=auth)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"8002", page.data)
        self.assertIn(b"shipment.txt", client.get("/central/transactions?q=9001", headers=auth).data)
        self.assertNotIn(b"shipment.txt", client.get("/central/transactions?q=NO-MATCH", headers=auth).data)

    def test_rejected_foreign_file_has_no_references_and_is_visible_as_exception(self):
        cap, folder = self.setup_capability()
        (folder / "wrong.txt").write_bytes(sample("OTHER"))
        for _ in range(2):
            scan_observer_once(self.db, cap, stable_seconds=0, allowed_roots=[self.base])
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT parse_status FROM central_artifacts").fetchone()[0], "REJECTED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_documents").fetchone()[0], 0)

    def test_live_authorization_is_required_for_every_scan(self):
        with patch.dict(os.environ, {"EDI_CENTRAL_LIVE_OBSERVATION": "1"}):
            cap, folder = self.setup_capability("LIVE")
            (folder / "shipment.txt").write_bytes(sample())
            scan_observer_once(self.db, cap, stable_seconds=0, allowed_roots=[self.base])
            self.assertEqual(scan_observer_once(self.db, cap, stable_seconds=0, allowed_roots=[self.base]), 1)
        with patch.dict(os.environ, {"EDI_CENTRAL_LIVE_OBSERVATION": "0"}):
            (folder / "new.txt").write_bytes(sample())
            self.assertEqual(scan_observer_once(self.db, cap, stable_seconds=0, allowed_roots=[self.base]), 0)

    def test_preview_requires_csrf_and_does_not_create_artifacts(self):
        app = create_app(self.db, username="a", password="b", config_path=self.base / "config.json")
        client = app.test_client()
        auth = {"Authorization": "Basic " + base64.b64encode(b"a:b").decode()}
        page = client.get("/central/856-preview", headers=auth)
        import re
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        self.assertEqual(client.post("/central/856-preview", headers=auth).status_code, 403)
        response = client.post("/central/856-preview", headers=auth,
                               data={"csrf_token": token, "file": (io.BytesIO(sample()), "sample.txt")})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"8002", response.data)
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_artifacts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
