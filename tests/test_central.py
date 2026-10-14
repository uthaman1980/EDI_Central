import base64
import tempfile
import unittest
from pathlib import Path

from niagara_edi.central import (create_capability, create_partner, migrate,
                                 scan_observer_once, session, set_enabled, set_partner_enabled)
from niagara_edi.dashboard import create_app


class CentralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "state.sqlite3"
        migrate(self.db)

    def partner(self, code="CUST1"):
        with session(self.db) as conn:
            partner = create_partner(conn, partner_code=code, display_name=code,
                                     radius_customer_code=code, company_number=2, plant_code="2")
            set_partner_enabled(conn, partner, True)
            return partner

    def capability(self, partner_id, folder, *, direction="OUTBOUND", route_class="VAN",
                   message_type="856", environment="TEST"):
        with session(self.db) as conn:
            return create_capability(conn, partner_id=partner_id, environment=environment,
                                     direction=direction, message_type=message_type,
                                     route_class=route_class, radius_message_active=True,
                                     radius_contract_verified=True,
                                     radius_outbound_folder=str(folder) if direction == "OUTBOUND" else "",
                                     radius_inbound_folder=str(folder) if direction == "INBOUND" else "",
                                     filename_pattern="*.txt")

    def test_migration_is_idempotent_and_capabilities_are_typed(self):
        migrate(self.db)
        partner = self.partner()
        direct = self.capability(partner, self.base, direction="INBOUND", route_class="DIRECT", message_type="850")
        with session(self.db) as conn:
            row = conn.execute("SELECT strategy,enabled FROM central_capabilities WHERE id=?", (direct,)).fetchone()
            self.assertEqual(tuple(row), ("TRANSLATE_TO_RADIUS", 0))
            with self.assertRaisesRegex(ValueError, "No verified translator"):
                set_enabled(conn, direct, True, allowed_roots=[self.base])

    def test_outbound_van_observer_preserves_file_and_never_claims_pickup(self):
        folder = self.base / "radius_out"
        folder.mkdir()
        original = folder / "shipment.txt"
        original.write_bytes(b"test-radius-outbound-file")
        partner = self.partner()
        capability = self.capability(partner, folder)
        with session(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "outside configured allowed roots"):
                set_enabled(conn, capability, True, allowed_roots=[self.base / "other"])
            set_enabled(conn, capability, True, allowed_roots=[self.base])
        self.assertEqual(scan_observer_once(self.db, capability, stable_seconds=0,
                                            allowed_roots=[self.base]), 0)
        self.assertEqual(scan_observer_once(self.db, capability, stable_seconds=0,
                                            allowed_roots=[self.base]), 1)
        self.assertEqual(scan_observer_once(self.db, capability, stable_seconds=0,
                                            allowed_roots=[self.base]), 0)
        self.assertEqual(original.read_bytes(), b"test-radius-outbound-file")
        with session(self.db) as conn:
            artifacts = conn.execute("SELECT state FROM central_artifacts WHERE capability_id=?", (capability,)).fetchall()
            self.assertEqual([row[0] for row in artifacts], ["PICKUP_UNVERIFIED"])
            set_enabled(conn, capability, False)
        (folder / "second.txt").write_bytes(b"new")
        self.assertEqual(scan_observer_once(self.db, capability, stable_seconds=0,
                                            allowed_roots=[self.base]), 0)

    def test_two_customer_folders_remain_isolated(self):
        a = self.base / "a"
        b = self.base / "b"
        a.mkdir()
        b.mkdir()
        (a / "one.txt").write_bytes(b"one")
        (b / "two.txt").write_bytes(b"two")
        ca = self.capability(self.partner("CUSTA"), a)
        cb = self.capability(self.partner("CUSTB"), b)
        with session(self.db) as conn:
            set_enabled(conn, ca, True, allowed_roots=[self.base])
        for _ in range(2):
            scan_observer_once(self.db, ca, stable_seconds=0, allowed_roots=[self.base])
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_artifacts WHERE capability_id=?", (ca,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_artifacts WHERE capability_id=?", (cb,)).fetchone()[0], 0)

    def test_live_observation_cannot_be_enabled(self):
        folder = self.base / "radius"
        folder.mkdir()
        cap = self.capability(self.partner(), folder, environment="LIVE")
        with session(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "LIVE capability activation"):
                set_enabled(conn, cap, True, allowed_roots=[self.base])

    def test_central_ui_requires_auth_and_csrf(self):
        app = create_app(self.db, username="admin", password="secret",
                         config_path=self.base / "config.json", inbox_path=self.base / "inbox")
        client = app.test_client()
        self.assertEqual(client.get("/central").status_code, 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
        page = client.get("/central", headers=auth)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Radius EDI Central", page.data)
        self.assertEqual(client.get("/central/transactions", headers=auth).status_code, 200)
        self.assertEqual(client.get("/central/exceptions", headers=auth).status_code, 200)
        self.assertEqual(client.get("/central/jobs", headers=auth).status_code, 200)
        self.assertEqual(client.post("/central/partners", data={"partner_code": "A"}, headers=auth).status_code, 403)


if __name__ == "__main__":
    unittest.main()
