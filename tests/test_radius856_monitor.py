import base64
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from niagara_edi.central import create_capability, create_partner, migrate, now_utc, session
from niagara_edi.dashboard import create_app
from niagara_edi.radius856_monitor import ShippingNotesReader, connection_settings, sync_source


def record(rec_id=100, status=1, customer="CUST1"):
    return {"TableRecId": rec_id, "Direction": 0, "CreationDate": date(2026,10,14),
            "CreationTime": "10:15", "RecordType": 0, "RecordStatus": status,
            "ErrorMessage": "Invalid reference" if status == 9 else "", "CompNum": 2,
            "PlantCode": "2", "CustCode": customer, "ShipAddrNum": 123, "ShipReqNum": rec_id+7000,
            "ShippingType": 0, "FileName": r"V:\EDI\shipment.txt", "ActualShipDate": date(2026,10,14)}


class Reader:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def read_pages(self, company, recent_cursor, history_cursor, *, page_size):
        self.calls.append((company, recent_cursor, history_cursor))
        return self.rows[:page_size], self.rows[:page_size]


class Radius856MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "state.sqlite3"
        migrate(self.db)
        with session(self.db) as conn:
            self.source = conn.execute("INSERT INTO central_radius_sources(environment,company_number,enabled) VALUES('TEST',2,1)").lastrowid

    def test_persists_all_customers_without_source_files_and_keeps_status_changes(self):
        reader = Reader([record(), record(101,0,"CUST2"), record(102,9,"CUST3")])
        self.assertEqual(sync_source(self.db,self.source,reader=reader),3)
        self.assertEqual(sync_source(self.db,self.source,reader=reader),0)
        reader.rows[0]["RecordStatus"] = 9
        reader.rows[0]["ErrorMessage"] = "Rejected after initial observation"
        self.assertEqual(sync_source(self.db,self.source,reader=reader),1)
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_radius_856_records").fetchone()[0],3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_radius_856_events").fetchone()[0],4)
            self.assertEqual(conn.execute("SELECT record_status FROM central_radius_856_records WHERE table_rec_id=100").fetchone()[0],9)

    def test_scope_mismatch_rolls_back_batch_and_does_not_advance_cursor(self):
        wrong = record(102)
        wrong["CompNum"] = 3
        with self.assertRaisesRegex(ValueError,"scope"):
            sync_source(self.db,self.source,reader=Reader([record(),wrong]))
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_radius_856_records").fetchone()[0],0)
            source=conn.execute("SELECT history_cursor,last_error,lease_owner FROM central_radius_sources").fetchone()
            self.assertIsNone(source[0])
            self.assertIn("scope",source[1])
            self.assertIsNone(source[2])

    def test_paging_disabled_source_and_overlapping_lease(self):
        reader = Reader([record(),record(101)])
        sync_source(self.db,self.source,reader=reader,page_size=2)
        with session(self.db) as conn:
            source=conn.execute("SELECT history_cursor,recent_cursor FROM central_radius_sources").fetchone()
            self.assertEqual(tuple(source),(101,101))
            conn.execute("UPDATE central_radius_sources SET lease_owner='other',lease_until='2999-01-01T00:00:00+00:00'")
        self.assertEqual(sync_source(self.db,self.source,reader=reader),0)
        self.assertEqual(len(reader.calls),1)
        with session(self.db) as conn:
            conn.execute("UPDATE central_radius_sources SET enabled=0,lease_until=NULL")
        self.assertEqual(sync_source(self.db,self.source,reader=reader),0)

    def test_live_does_not_reuse_test_connection_password(self):
        with patch.dict(os.environ,{"RADIUS_DB_PASS":"test-only"},clear=True):
            self.assertEqual(connection_settings("TEST")[2],"test-only")
            with self.assertRaisesRegex(ValueError,"RADIUS_LIVE_MONITOR_PASS"):
                connection_settings("LIVE")
        with session(self.db) as conn:
            conn.execute("UPDATE central_radius_sources SET environment='LIVE'")
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,"not authorized"):
                sync_source(self.db,self.source,reader=Reader([record()]))

    def test_failed_read_does_not_replace_saved_good_records(self):
        sync_source(self.db,self.source,reader=Reader([record()]))
        class FailedReader:
            def read_pages(self,*args,**kwargs):
                raise RuntimeError("Database unavailable")
        with self.assertRaisesRegex(RuntimeError,"unavailable"):
            sync_source(self.db,self.source,reader=FailedReader())
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM central_radius_856_records").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT last_error FROM central_radius_sources").fetchone()[0],"Database unavailable")

    def test_monitor_filters_detail_and_csrf(self):
        sync_source(self.db,self.source,reader=Reader([record(),record(101,9,"CUST2")]))
        app=create_app(self.db,username="a",password="b",config_path=self.base/"config.json")
        client=app.test_client()
        auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
        self.assertEqual(client.get("/central/856-monitor").status_code,401)
        page=client.get("/central/856-monitor?status=9",headers=auth)
        self.assertEqual(page.status_code,200)
        self.assertIn(b"Invalid reference",page.data)
        self.assertIn(b"1 matching indexed records",page.data)
        self.assertEqual(client.get("/central/856-monitor?status=invalid",headers=auth).status_code,400)
        with session(self.db) as conn:
            local_id=conn.execute("SELECT id FROM central_radius_856_records WHERE table_rec_id=101").fetchone()[0]
        detail=client.get(f"/central/856-monitor/records/{local_id}",headers=auth)
        self.assertEqual(detail.status_code,200)
        self.assertIn(b"Rejected",detail.data)
        self.assertEqual(client.post(f"/central/856-monitor/sources/{self.source}/pause",headers=auth).status_code,403)

    def test_odbc_connection_requests_read_only_and_queries_are_scoped_selects(self):
        class Cursor:
            def __init__(self):
                self.calls=[]
            def execute(self,sql,*values):
                self.calls.append((sql,values))
            def fetchmany(self,size):
                return []
            def cancel(self):
                pass
        cursor=Cursor()
        class Connection:
            def cursor(self):
                return cursor
            def close(self):
                pass
        with patch.dict(os.environ,{"RADIUS_TEST_MONITOR_PASS":"test-pass"}), patch("niagara_edi.radius856_monitor.pyodbc.connect",return_value=Connection()) as connect:
            ShippingNotesReader("TEST").read_pages(2,None,100)
        self.assertTrue(connect.call_args.kwargs["readonly"])
        self.assertTrue(all(sql.startswith("SELECT ") for sql,_ in cursor.calls))
        for sql,values in cursor.calls[1:]:
            self.assertIn("Direction=0",sql)
            self.assertIn("ShippingType=0",sql)
            self.assertEqual(values[0],2)

    def test_file_correlation_requires_same_environment_and_filename(self):
        sync_source(self.db,self.source,reader=Reader([record()]))
        with session(self.db) as conn:
            partner=create_partner(conn,partner_code="CUST1",display_name="Synthetic",radius_customer_code="CUST1",company_number=2,plant_code="2")
            local_id=conn.execute("SELECT id FROM central_radius_856_records").fetchone()[0]
            for environment,filename,bol in [("LIVE","shipment.txt",90001),("TEST","other.txt",90002)]:
                cap=create_capability(conn,partner_id=partner,environment=environment,direction="OUTBOUND",message_type="856",route_class="VAN")
                artifact=conn.execute("""INSERT INTO central_artifacts(capability_id,source_path,filename,digest,size_bytes,mtime_ns,observed_at,state)
                    VALUES(?,?,?,?,?,?,?,?)""",(cap,"synthetic/"+filename,filename,"synthetic",1,1,now_utc(),"PICKUP_UNVERIFIED")).lastrowid
                conn.execute("""INSERT INTO central_documents(artifact_id,radius_shipreq_number,status,created_at,radius_bol_number,radius_customer_code,company_number,plant_code)
                    VALUES(?,?,?,?,?,?,?,?)""",(artifact,7100,"PARSED",now_utc(),bol,"CUST1",2,"2"))
        app=create_app(self.db,username="a",password="b",config_path=self.base/"config.json")
        client=app.test_client()
        auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
        detail=client.get(f"/central/856-monitor/records/{local_id}",headers=auth)
        self.assertEqual(detail.status_code,200)
        self.assertIn(b"No matching file was indexed",detail.data)
        self.assertNotIn(b"90001",detail.data)
        self.assertNotIn(b"90002",detail.data)


if __name__ == "__main__":
    unittest.main()
