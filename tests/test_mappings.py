import base64
import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from niagara_edi.central import create_partner, migrate, session, set_partner_enabled
from niagara_edi.customer_config import save_config
from niagara_edi.dashboard import create_app
from niagara_edi.fixed850 import parse_fixed_850
from niagara_edi.mapping_engine import map_rows, preview, read_source, template_rules
from niagara_edi.mappings import (clone_revision, create_profile, get_revision, partner_for,
                                  publish_revision, run_preview, save_draft)
from niagara_edi.service import DuplicateOrder, process_file
from test_fixed850_inspection import incomplete_sample
from test_outbound856 import sample as outbound_sample


def source_row(po="MAP-PO-1",line="10"):
    return {"CompNum":"2","PlantCode":"2","CustCode":"CUST1","CustRef":po,
            "CustOrderDate":"2026-10-14","CurrCode":"USD","BillAddressRef":"BILL1",
            "ShipAddressRef":"SHIP1","CustReqDate":"2026-10-14","TermsCode":"NET30",
            "ShipDate":"2026-10-20","CustPOLineRef":line,"CustItemRef":"TEST-ITEM",
            "OrderedQty":"100","OrderUnitCode":"EA","FCUnitPrice":"2.50",
            "PriceUnitCode":"EA","ReqDate":"2026-10-20","ShipQty":"100"}


class Resolver:
    def __init__(self):
        self.calls=[]
    def resolve(self,company,customer,reference,kind):
        self.calls.append((company,customer,reference,kind))
        return {"BILL1":101,"SHIP1":202}[reference]


class Radius:
    def __init__(self):
        self.calls=[]
        self.next_so=9000
    def post(self,entity,payload,root,child):
        self.calls.append((entity,payload))
        if entity=="XLinkAPISOrder":
            lines=payload["XLSOrders"]["XLSOrder"][0]["XLSOrderLine"]
            self.next_so+=1
            return {"SOrderNum":self.next_so,"XLSOrderLine":[{"SOrderLineNum":line["SOrderLineNum"],"ItemCode":"RADIUS-ITEM"} for line in lines]}
        return {"ShipReqNum":8000+len(self.calls),"XLShipReqLine":[{"Action":"Add"}]}


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.db=self.base/"state.sqlite3"
        migrate(self.db)
        with session(self.db) as conn:
            self.partner_id=create_partner(conn,partner_code="CUST1",display_name="Synthetic customer",
                radius_customer_code="CUST1",company_number=2,plant_code="2")
            set_partner_enabled(conn,self.partner_id,True)
            self.profile_id=create_profile(conn,partner_id=self.partner_id,name="Direct orders",environment="TEST",
                direction="INBOUND",message_type="850",source_format="JSON",target_format="RADIUS_API",actor="test")
        self.data=json.dumps([source_row()]).encode()

    def revision(self):
        with session(self.db) as conn:
            profile=get_revision(conn,self.profile_id,1)
            return profile,partner_for(conn,profile),json.loads(profile["rules_json"])

    def publish(self):
        with session(self.db) as conn:
            result=run_preview(conn,self.profile_id,1,self.data,"test")
            self.assertEqual(result["errors"],[])
            publish_revision(conn,self.profile_id,1,"test")
            return get_revision(conn,self.profile_id,1)["revision_id"]

    def test_scalar_transforms_and_lookup_do_not_guess_unknown_codes(self):
        rules=[{"target":"unit","source":"uom","operation":"lookup","lookup":{"EACH":"EA"},"required":True},
               {"target":"date","source":"header.date","operation":"date","input_format":"%m/%d/%Y","output_format":"%Y-%m-%d"},
               {"target":"price","source":"price","operation":"number"},
               {"target":"customer","operation":"constant","value":"CUST1"}]
        rows,trace,errors=map_rows([{"uom":"EACH","header":{"date":"10/20/2026"},"price":"2.500"}],rules)
        self.assertEqual(errors,[])
        self.assertEqual(rows,[{"unit":"EA","date":"2026-10-20","price":"2.500","customer":"CUST1"}])
        self.assertEqual(len(trace),4)
        self.assertTrue(map_rows([{"uom":"UNKNOWN","price":"NaN"}],rules)[2])
        scaled,_,issues=map_rows([{"price":"0.125"}],[{"target":"per_thousand","source":"price","operation":"multiply","value":"1000"}])
        self.assertEqual(issues,[])
        self.assertEqual(scaled[0]["per_thousand"],"125.000")
        self.assertTrue(map_rows([{"price":"1e99999999"}],[{"target":"price","source":"price","operation":"number"}])[2])

    def test_radius_file_preview_round_trips_but_direct_api_needs_no_flat_file(self):
        profile,partner,rules=self.revision()
        result=preview(self.data,profile,partner,rules)
        self.assertEqual(result["errors"],[])
        self.assertTrue(result["content"].startswith("["))
        profile["target_format"]="RADIUS850"
        text=preview(self.data,profile,partner,rules)["content"]
        orders=parse_fixed_850(text.encode())
        self.assertEqual((orders[0].sender,orders[0].po_number),("CUST1","MAP-PO-1"))

    def test_csv_input_and_multiple_pos_are_preserved(self):
        profile,partner,rules=self.revision()
        profile["source_format"]="CSV"
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=list(source_row()))
        writer.writeheader()
        writer.writerows([source_row(),source_row("MAP-PO-2")])
        result=preview(output.getvalue().encode(),profile,partner,rules)
        self.assertEqual(result["errors"],[])
        self.assertEqual([row["CustRef"] for row in result["rows"]],["MAP-PO-1","MAP-PO-2"])
        with self.assertRaisesRegex(ValueError,"1000"):
            read_source(json.dumps([{}]*1001).encode(),"JSON",partner,"INBOUND")

    def test_publishing_requires_current_successful_proof_and_prevents_edits(self):
        with session(self.db) as conn:
            with self.assertRaisesRegex(ValueError,"successful sample"):
                publish_revision(conn,self.profile_id,1,"test")
            run_preview(conn,self.profile_id,1,self.data,"test")
            profile=get_revision(conn,self.profile_id,1)
            rules=json.loads(profile["rules_json"])
            rules[0]["value"]="3"
            save_draft(conn,self.profile_id,1,rules,profile["edit_version"],"test")
            with self.assertRaisesRegex(ValueError,"successful sample"):
                publish_revision(conn,self.profile_id,1,"test")
            rules[0]["value"]="2"
            save_draft(conn,self.profile_id,1,rules,2,"test")
            run_preview(conn,self.profile_id,1,self.data,"test")
            publish_revision(conn,self.profile_id,1,"test")
            with self.assertRaisesRegex(ValueError,"immutable"):
                save_draft(conn,self.profile_id,1,rules,3,"test")
            self.assertEqual(clone_revision(conn,self.profile_id,1,"test"),2)
            with self.assertRaisesRegex(ValueError,"successful sample"):
                publish_revision(conn,self.profile_id,2,"test")

    def test_validation_rejects_missing_fields_customer_scope_and_bad_quantities(self):
        profile,partner,rules=self.revision()
        for name,value in [("BillAddressRef",""),("OrderedQty","1e999"),("ShipQty","101"),("CustRef","bad|key")]:
            row=source_row()
            row[name]=value
            self.assertTrue(preview(json.dumps([row]).encode(),profile,partner,rules)["errors"],name)
        rules[2]["value"]="FOREIGN"
        self.assertTrue(preview(self.data,profile,partner,rules)["errors"])
        with session(self.db) as conn:
            p=get_revision(conn,self.profile_id,1)
            with self.assertRaisesRegex(ValueError,"Another editor"):
                save_draft(conn,self.profile_id,1,json.loads(p["rules_json"]),99,"test")

    def test_incomplete_fixed_input_is_reported_without_filling_unapproved_values(self):
        profile,partner,rules=self.revision()
        profile["source_format"]="RADIUS850"
        result=preview(incomplete_sample(),profile,partner,rules)
        self.assertTrue(result["errors"])
        self.assertTrue(any(issue["target"]=="CurrCode" for issue in result["errors"]))

    def test_outbound_scope_and_customer_csv_serialization(self):
        profile,partner,_=self.revision()
        profile.update(direction="OUTBOUND",message_type="856",source_format="RADIUS856",target_format="CSV")
        rules=[{"target":"Customer PO","source":"CustRef","required":True},
               {"target":"Shipment","source":"BOLNum","required":True}]
        result=preview(outbound_sample(),profile,partner,rules)
        self.assertEqual(result["errors"],[])
        csv_rows=list(csv.DictReader(io.StringIO(result["content"])))
        self.assertEqual(len(csv_rows),2)
        self.assertEqual(csv_rows[0]["Shipment"],"9001")
        with self.assertRaisesRegex(ValueError,"does not match"):
            preview(outbound_sample("OTHER"),profile,partner,rules)

    def test_direct_mapping_reuses_existing_posting_and_audit_then_blocks_duplicate(self):
        revision_id=self.publish()
        path=self.base/"customer.json"
        path.write_bytes(self.data)
        radius=Radius()
        resolver=Resolver()
        cfg={"environment":"TEST","customers":{},"x12_partners":{}}
        first=process_file(path,self.base/"unused.json",cfg,self.db,submit=True,client=radius,resolver=resolver,mapping_revision_id=revision_id)
        self.assertEqual(len(radius.calls),2)
        so=radius.calls[0][1]["XLSOrders"]["XLSOrder"][0]
        self.assertEqual((so["CustCode"],so["BillAddrNum"],so["ShipAddrNum"]),("CUST1",101,202))
        self.assertNotIn("SOrderDate",so)
        self.assertEqual(so["SOSourceCode"],"EDI")
        self.assertEqual(so["XLSOrderLine"][0]["CustItemRef"],"TEST-ITEM")
        self.assertEqual(radius.calls[1][1]["XLShipReqs"]["XLShipReq"][0]["XLShipReqLine"][0]["ItemCode"],"RADIUS-ITEM")
        process_file(path,self.base/"unused.json",cfg,self.db,submit=True,client=radius,resolver=resolver,mapping_revision_id=revision_id)
        self.assertEqual(len(radius.calls),2)
        with session(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM radius_records").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT revision_id FROM central_mapping_file_links WHERE file_id=?",(first[0]["file_id"],)).fetchone()[0],revision_id)

    def test_draft_live_and_changed_scope_cannot_post(self):
        profile,_,_=self.revision()
        path=self.base/"customer.json"
        path.write_bytes(self.data)
        radius=Radius()
        cfg={"environment":"TEST"}
        with self.assertRaisesRegex(ValueError,"published TEST"):
            process_file(path,self.base/"unused",cfg,self.db,submit=True,client=radius,mapping_revision_id=profile["revision_id"])
        revision_id=self.publish()
        with self.assertRaisesRegex(ValueError,"only in TEST"):
            process_file(path,self.base/"unused",{"environment":"LIVE"},self.db,submit=True,client=radius,mapping_revision_id=revision_id)
        with session(self.db) as conn:
            conn.execute("UPDATE central_partners SET radius_customer_code='OTHER' WHERE id=?",(self.partner_id,))
        with self.assertRaisesRegex(ValueError,"scope changed"):
            process_file(path,self.base/"unused",cfg,self.db,submit=True,client=radius,mapping_revision_id=revision_id)
        self.assertEqual(radius.calls,[])

    def test_known_sales_order_from_legacy_route_blocks_cross_route_duplicate(self):
        revision_id=self.publish()
        path=self.base/"customer.json"
        path.write_bytes(self.data)
        radius=Radius()
        cfg={"environment":"TEST"}
        process_file(path,self.base/"unused",cfg,self.db,submit=True,client=radius,resolver=Resolver(),mapping_revision_id=revision_id)
        with session(self.db) as conn:
            conn.execute("UPDATE orders SET business_key='LEGACY|MAP-PO-1|'")
            conn.execute("UPDATE radius_records SET business_key='LEGACY|MAP-PO-1|'")
        with self.assertRaisesRegex(DuplicateOrder,"from another route"):
            process_file(path,self.base/"unused",cfg,self.db,submit=True,client=radius,resolver=Resolver(),mapping_revision_id=revision_id)
        self.assertEqual(len(radius.calls),2)

    def test_web_editor_preview_and_saved_submission_pin_published_mapping(self):
        revision_id=self.publish()
        config=self.base/"config.json"
        save_config(config,{"environment":"TEST","customers":{},"x12_partners":{}})
        radius=Radius()
        app=create_app(self.db,username="a",password="b",config_path=config,inbox_path=self.base/"inbox",radius_client=radius,resolver=Resolver())
        client=app.test_client()
        auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
        base=f"/central/mappings/{self.profile_id}/versions/1"
        self.assertEqual(client.get("/central/mappings").status_code,401)
        self.assertEqual(client.get("/central/mappings",headers=auth).status_code,200)
        page=client.get(base,headers=auth)
        self.assertEqual(page.status_code,200)
        token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
        self.assertEqual(client.post(base+"/publish",headers=auth).status_code,403)
        result=client.post(base+"/api-preview",headers=auth,data={"csrf_token":token,"file":(io.BytesIO(self.data),"customer.json")})
        self.assertEqual(result.status_code,200)
        self.assertIn(b"Submit reviewed order",result.data)
        self.assertEqual(radius.calls,[])
        with session(self.db) as conn:
            file_id=conn.execute("SELECT file_id FROM central_mapping_file_links WHERE revision_id=?",(revision_id,)).fetchone()[0]
        result=client.post(f"/files/{file_id}/submit",headers=auth,data={"csrf_token":token})
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(radius.calls),2)
        self.assertEqual(client.get(f"/files/{file_id}",headers=auth).status_code,200)
        repeat=client.post(f"/files/{file_id}/submit",headers=auth,data={"csrf_token":token,
            "resubmit_mode":"new","resubmission_token":"a"*32})
        error=re.search(rb'role="alert">(.*?)</div>',repeat.data,re.S)
        self.assertEqual(repeat.status_code,200,error.group(1).decode() if error else "Request failed")
        self.assertEqual(len(radius.calls),4)

    def test_failed_sample_is_audited_and_draft_form_keeps_invalid_lookup_for_correction(self):
        with session(self.db) as conn:
            result=run_preview(conn,self.profile_id,1,b'not json',"test")
            self.assertEqual(result["errors"][0]["target"],"Source validation")
            self.assertEqual(conn.execute("SELECT error_count FROM central_mapping_runs").fetchone()[0],1)
        app=create_app(self.db,username="a",password="b",config_path=self.base/"config.json")
        client=app.test_client()
        auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
        base=f"/central/mappings/{self.profile_id}/versions/1"
        page=client.get(base,headers=auth)
        token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
        response=client.post(base+"/save",headers=auth,data={"csrf_token":token,"edit_version":"1",
            "target":"CustRef","source":"my_customer_po","operation":"lookup","value":"","default":"",
            "required":"yes","input_format":"","output_format":"","lookup":"INVALID-JSON"})
        self.assertEqual(response.status_code,400)
        self.assertIn(b"my_customer_po",response.data)
        self.assertIn(b"INVALID-JSON",response.data)

    def test_web_publish_and_clone_require_the_saved_edit_version_and_fresh_proof(self):
        app=create_app(self.db,username="a",password="b",config_path=self.base/"config.json")
        client=app.test_client()
        auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
        base=f"/central/mappings/{self.profile_id}/versions/1"
        page=client.get(base,headers=auth)
        token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
        response=client.post(base+"/preview",headers=auth,data={"csrf_token":token,"edit_version":"1",
            "file":(io.BytesIO(self.data),"sample.json")})
        self.assertEqual(response.status_code,200)
        self.assertEqual(client.post(base+"/publish",headers=auth,data={"csrf_token":token,"edit_version":"99"}).status_code,409)
        self.assertEqual(client.post(base+"/publish",headers=auth,data={"csrf_token":token,"edit_version":"1"}).status_code,302)
        copied=client.post(base+"/clone",headers=auth,data={"csrf_token":token,"edit_version":"1"})
        self.assertEqual(copied.status_code,302)
        version2=base[:-1]+"2"
        self.assertEqual(client.post(version2+"/publish",headers=auth,data={"csrf_token":token,"edit_version":"1"}).status_code,409)

    def test_per_line_shipping_mode_is_preserved_by_direct_mapping(self):
        with session(self.db) as conn:
            conn.execute("UPDATE central_mapping_profiles SET shipping_request_mode='per_line' WHERE id=?",(self.profile_id,))
        self.data=json.dumps([source_row(),source_row(line="20")]).encode()
        revision_id=self.publish()
        path=self.base/"customer.json"
        path.write_bytes(self.data)
        radius=Radius()
        result=process_file(path,self.base/"unused",{"environment":"TEST"},self.db,submit=True,
            client=radius,resolver=Resolver(),mapping_revision_id=revision_id)
        self.assertEqual(len(radius.calls),3)
        self.assertEqual(result[0]["shipreq_numbers"],[8002,8003])


if __name__ == "__main__":
    unittest.main()
