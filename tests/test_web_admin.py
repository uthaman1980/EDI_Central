import io
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from niagara_edi.customer_config import load_config, save_config
from niagara_edi.dashboard import create_app
from niagara_edi.radius import RadiusRejected
from test_fixed850 import ITEMS, Radius, Resolver, SAMPLE
from test_flow import sample_edi


class WebAdminTests(unittest.TestCase):
    def setUp(self):
        date_patch = patch("niagara_edi.service.current_date", return_value=date(2026, 10, 7))
        date_patch.start()
        self.addCleanup(date_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config = self.base / "config.json"
        self.radius = Radius()
        self.resolver = Resolver()
        app = create_app(self.base / "state.sqlite3", username="admin", password="secret",
                         config_path=self.config, inbox_path=self.base / "inbox", radius_client=self.radius,
                         resolver=self.resolver)
        self.client = app.test_client()
        import base64
        self.auth = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
        page = self.client.get("/customers/new", headers=self.auth)
        self.csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()

    def profile_form(self, **changes):
        form = {
            "csrf_token": self.csrf, "kind": "van", "key": "PEP01", "display_name": "Pepsi",
            "cust_code": "PEP01", "comp_num": "2", "plant_code": "2", "item_identifier": "CI",
            "item_map": "",
            "uom_map": "EA=EA", "price_unit_map": "THOUS=THOUS",
            "shipping_request_mode": "per_line",
            "enabled": "on",
        }
        form.update(changes)
        return form

    def test_save_customer_and_submit_van_to_mock_test_radius(self):
        self.assertEqual(self.client.get("/customers").status_code, 401)
        saved = self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        self.assertEqual(saved.status_code, 302)
        self.assertTrue(load_config(self.config)["customers"]["PEP01"]["enabled"])
        self.assertEqual(load_config(self.config)["customers"]["PEP01"]["address_map"], {})
        response = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "van:PEP01", "action": "submit",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Radius records created", response.data)
        self.assertIn(b"Open saved Radius record", response.data)
        self.assertIn(b"Updates are on hold", response.data)
        self.assertIn(b"Create new Radius records", response.data)
        self.assertEqual(len(self.radius.calls), 6)
        self.assertEqual(self.resolver.calls, [(2, "PEP01", "US01", "BT"), (2, "PEP01", "1K15", "ST")])
        self.assertEqual(len(list((self.base / "processed").glob("*.txt"))), 1)

    def test_preview_never_posts_or_enters_watched_inbox(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        response = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "van:PEP01", "action": "preview",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mapping preview ready", response.data)
        self.assertIn(b'"shipping_request_count": 5', response.data)
        self.assertEqual(self.radius.calls, [])
        self.assertFalse((self.base / "inbox").exists())

    def test_submit_saved_preview_then_completed_file_does_not_post_twice(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        preview = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "van:PEP01", "action": "preview",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"Submit this preview to Radius TEST", preview.data)
        with closing(sqlite3.connect(self.base / "state.sqlite3")) as conn:
            file_id = conn.execute("SELECT id FROM files").fetchone()[0]
        saved = self.client.post(f"/files/{file_id}/submit", data={"csrf_token": self.csrf}, headers=self.auth)
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"Radius records created", saved.data)
        self.assertEqual(len(self.radius.calls), 6)
        self.assertEqual(len(list((self.base / "processed").glob("*.txt"))), 1)
        detail = self.client.get(f"/files/{file_id}", headers=self.auth)
        self.assertIn(b"is on hold pending Radius investigation", detail.data)
        self.assertIn(b"Create new Radius records", detail.data)
        repeat = self.client.post(f"/files/{file_id}/submit", data={"csrf_token": self.csrf}, headers=self.auth)
        self.assertEqual(repeat.status_code, 200)
        self.assertIn(b"no new POST sent", repeat.data)
        self.assertEqual(len(self.radius.calls), 6)

    def test_completed_file_holds_updates_and_can_create_new_radius_records(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)

        class UpsertRadius:
            def __init__(self):
                self.calls = []
                self.next_so = 9001
                self.next_ship = 8001

            def post(self, entity, payload, root, child):
                self.calls.append((entity, payload))
                header = payload[root][child][0]
                if entity == "XLinkAPISOrder":
                    number = header["SOrderNum"] or self.next_so
                    if not header["SOrderNum"]:
                        self.next_so += 1
                    return {"SOrderNum": number, "Action": "Update" if header["SOrderNum"] else "Add",
                            "XLSOrderLine": [{"SOrderLineNum": line["SOrderLineNum"],
                                              "ItemCode": f"ITEM{i + 1}", "Action": "Update",
                                              "CustItemRef": line["CustItemRef"],
                                              "PriceGroupCode": ("PEPSI" if i != 2 else "") if number == 9001 else ""}
                                             for i, line in enumerate(header["XLSOrderLine"])]}
                number = header["ShipReqNum"] or self.next_ship
                if not header["ShipReqNum"]:
                    self.next_ship += 1
                return {"ShipReqNum": number, "Action": "Update" if header["ShipReqNum"] else "Add",
                        "XLShipReqLine": [{"Action": "Update"} for _ in header["XLShipReqLine"]]}

        radius = UpsertRadius()
        app = create_app(self.base / "state.sqlite3", username="admin", password="secret",
                         config_path=self.config, inbox_path=self.base / "inbox", radius_client=radius,
                         resolver=self.resolver)
        client = app.test_client()
        token = re.search(rb'name="csrf_token" value="([^"]+)"',
                          client.get("/inbound", headers=self.auth).data).group(1).decode()
        first = client.post("/inbound", data={
            "csrf_token": token, "customer": "van:PEP01", "action": "submit",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(first.status_code, 200)
        preview_again = client.post("/inbound", data={
            "csrf_token": token, "customer": "van:PEP01", "action": "preview",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertIn(b"Updates are on hold", preview_again.data)
        self.assertIn(b"Create new Radius records", preview_again.data)
        with closing(sqlite3.connect(self.base / "state.sqlite3")) as conn:
            file_id = conn.execute("SELECT id FROM files ORDER BY id LIMIT 1").fetchone()[0]
        detail = client.get(f"/files/{file_id}", headers=self.auth)
        nonce = re.search(rb'name="resubmission_token" value="([^"]+)"', detail.data).group(1).decode()
        update = client.post(f"/files/{file_id}/submit", data={
            "csrf_token": token, "resubmission_token": nonce, "resubmit_mode": "update",
            "target_so_number": "9001",
        }, headers=self.auth)
        self.assertEqual(update.status_code, 409)
        self.assertEqual(len(radius.calls), 6)
        repeated_update = client.post(f"/files/{file_id}/submit", data={
            "csrf_token": token, "resubmission_token": nonce, "resubmit_mode": "update",
            "target_so_number": "9001",
        }, headers=self.auth)
        self.assertEqual(repeated_update.status_code, 409)
        self.assertEqual(len(radius.calls), 6)
        new_nonce = re.search(rb'name="resubmission_token" value="([^"]+)"',
                              client.get(f"/files/{file_id}", headers=self.auth).data).group(1).decode()
        create = client.post(f"/files/{file_id}/submit", data={
            "csrf_token": token, "resubmission_token": new_nonce, "resubmit_mode": "new",
        }, headers=self.auth)
        self.assertEqual(create.status_code, 200)
        self.assertIn(b'"so_number": 9002', create.data)
        self.assertEqual(radius.calls[6][1]["XLSOrders"]["XLSOrder"][0]["SOrderNum"], 0)
        self.assertTrue(all("PriceGroupCode" not in line for line in
                            radius.calls[6][1]["XLSOrders"]["XLSOrder"][0]["XLSOrderLine"]))
        self.assertEqual(len(radius.calls), 12)
        repeat = client.post(f"/files/{file_id}/submit", data={
            "csrf_token": token, "resubmission_token": new_nonce, "resubmit_mode": "new",
        }, headers=self.auth)
        self.assertEqual(repeat.status_code, 200)
        self.assertIn(b"no new POST sent", repeat.data)
        self.assertEqual(len(radius.calls), 12)
        with closing(sqlite3.connect(self.base / "state.sqlite3")) as conn:
            new_file_id = conn.execute("SELECT id FROM files WHERE so_numbers='[9002]' ORDER BY id DESC LIMIT 1").fetchone()[0]
        new_detail = client.get(f"/files/{new_file_id}", headers=self.auth)
        self.assertIn(b"is on hold pending Radius investigation", new_detail.data)
        update_new_nonce = re.search(rb'name="resubmission_token" value="([^"]+)"', new_detail.data).group(1).decode()
        missing_target = client.post(f"/files/{new_file_id}/submit", data={
            "csrf_token": token, "resubmission_token": update_new_nonce, "resubmit_mode": "update",
        }, headers=self.auth)
        self.assertEqual(missing_target.status_code, 409)
        self.assertEqual(len(radius.calls), 12)
        update_new = client.post(f"/files/{new_file_id}/submit", data={
            "csrf_token": token, "resubmission_token": update_new_nonce, "resubmit_mode": "update",
            "target_so_number": "9002",
        }, headers=self.auth)
        self.assertEqual(update_new.status_code, 409)
        self.assertEqual(len(radius.calls), 12)

    def test_saved_file_action_blocks_uncertain_state_and_bad_csrf(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "van:PEP01", "action": "preview",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        with closing(sqlite3.connect(self.base / "state.sqlite3")) as conn:
            file_id = conn.execute("SELECT id FROM files").fetchone()[0]
            conn.execute("UPDATE files SET status='attention' WHERE id=?", (file_id,))
            conn.commit()
        self.assertEqual(self.client.post(f"/files/{file_id}/submit", data={"csrf_token": "bad"},
                                          headers=self.auth).status_code, 403)
        self.assertEqual(self.client.post(f"/files/{file_id}/submit", data={"csrf_token": self.csrf},
                                          headers=self.auth).status_code, 409)
        self.assertEqual(self.radius.calls, [])

    def test_rejected_sales_order_can_retry_from_file_history(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)

        class RejectOnce:
            def __init__(self):
                self.real = Radius()
                self.rejected = False

            def post(self, *args):
                if not self.rejected:
                    self.rejected = True
                    raise RadiusRejected("Invalid billing address (917)")
                return self.real.post(*args)

        radius = RejectOnce()
        app = create_app(self.base / "state.sqlite3", username="admin", password="secret",
                         config_path=self.config, inbox_path=self.base / "inbox", radius_client=radius,
                         resolver=self.resolver)
        client = app.test_client()
        token = re.search(rb'name="csrf_token" value="([^"]+)"',
                          client.get("/inbound", headers=self.auth).data).group(1).decode()
        rejected = client.post("/inbound", data={
            "csrf_token": token, "customer": "van:PEP01", "action": "submit",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(rejected.status_code, 400)
        with closing(sqlite3.connect(self.base / "state.sqlite3")) as conn:
            file_id, status = conn.execute("SELECT id,status FROM files ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(status, "rejected")
        retry = client.post(f"/files/{file_id}/submit", data={"csrf_token": token}, headers=self.auth)
        self.assertEqual(retry.status_code, 200)
        self.assertIn(b"Radius records created", retry.data)
        self.assertEqual(len(radius.real.calls), 6)

    def test_direct_customer_profile_and_verified_receipt(self):
        form = self.profile_form(kind="direct", key="027948264", display_name="Niagara",
                                 cust_code="NIA01", item_identifier="VN", item_map="VEN001=ITEM001",
                                 price_unit_map="EA=EACH", address_map="BILL1=101\nHOU=202",
                                 as2_sender="NIAGARA", as2_receiver="OURS", isa_receiver="OURID")
        self.assertEqual(self.client.post("/customers/new", data=form, headers=self.auth).status_code, 302)
        data = sample_edi()
        receipt = json.dumps({"as2_sender": "NIAGARA", "as2_receiver": "OURS",
                              "authentication_status": "verified", "decryption_status": "verified",
                              "mic_status": "verified", "sha256": hashlib.sha256(data).hexdigest()}).encode()
        response = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "direct:027948264", "action": "submit",
            "file": (io.BytesIO(data), "order.edi"),
            "receipt": (io.BytesIO(receipt), "order.edi.as2.json"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.radius.calls), 2)
        self.assertEqual(len(list((self.base / "processed").glob("*.edi"))), 1)

    def test_direct_controlled_file_drop_needs_no_as2_receipt(self):
        form = self.profile_form(kind="direct", key="027948264", cust_code="NIA01",
                                 item_identifier="VN", item_map="VEN001=ITEM001",
                                 price_unit_map="EA=EACH", address_map="BILL1=101\nHOU=202",
                                 transport="trusted_drop", isa_receiver="OURID")
        self.assertEqual(self.client.post("/customers/new", data=form, headers=self.auth).status_code, 302)
        response = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "direct:027948264", "action": "submit",
            "file": (io.BytesIO(sample_edi()), "order.edi"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.radius.calls), 2)

    def test_edit_customer_disable(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        edit = self.client.get("/customers/van/PEP01", headers=self.auth)
        self.assertEqual(edit.status_code, 200)
        self.assertNotIn(b'name="address_map"', edit.data)
        form = self.profile_form()
        form.pop("enabled")
        self.assertEqual(self.client.post("/customers/van/PEP01", data=form, headers=self.auth).status_code, 302)
        self.assertFalse(load_config(self.config)["customers"]["PEP01"]["enabled"])

    def test_new_customer_forms_show_only_relevant_fields(self):
        van = self.client.get("/customers/new?kind=van", headers=self.auth)
        direct = self.client.get("/customers/new?kind=direct", headers=self.auth)
        self.assertNotIn(b'name="address_map"', van.data)
        self.assertNotIn(b'name="item_map"', van.data)
        self.assertIn(b'name="address_map"', direct.data)
        self.assertIn(b'name="item_map"', direct.data)

    def test_customer_save_requires_csrf_and_complete_enabled_mappings(self):
        form = self.profile_form(uom_map="")
        self.assertEqual(self.client.post("/customers/new", data=form, headers=self.auth).status_code, 400)
        self.assertFalse(self.config.exists())
        form = self.profile_form(csrf_token="wrong")
        expired = self.client.post("/customers/new", data=form, headers=self.auth)
        self.assertEqual(expired.status_code, 403)
        self.assertIn(b"Open Process a file again", expired.data)

    def test_web_submission_refuses_live_environment(self):
        self.client.post("/customers/new", data=self.profile_form(), headers=self.auth)
        config = load_config(self.config)
        config["environment"] = "LIVE"
        save_config(self.config, config)
        response = self.client.post("/inbound", data={
            "csrf_token": self.csrf, "customer": "van:PEP01", "action": "submit",
            "file": (io.BytesIO(SAMPLE.read_bytes()), "order.txt"),
        }, headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.radius.calls, [])


if __name__ == "__main__":
    unittest.main()
