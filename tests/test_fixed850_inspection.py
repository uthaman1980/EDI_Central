import base64
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from niagara_edi.dashboard import create_app
from niagara_edi.edi import EDIError
from niagara_edi.fixed850 import inspect_fixed_850, parse_fixed_850
from niagara_edi.service import process_file


def record(tag, size, values):
    line = list(tag.ljust(size))
    for start, width, value in values:
        line[start - 1:start - 1 + width] = str(value).ljust(width)
    return "".join(line)


def incomplete_sample():
    rows = ["EDI850Inbound".ljust(267),
        record("SOHEAD",580,[(7,1,0),(8,6,2),(14,8,2),(30,8,"13102026"),(38,8,"13102026"),
                             (46,8,"SRC850"),(87,10,"SHIPTEST"),(157,30,"TEST-PO-1")]),
        record("SOLINE",881,[(7,1,0),(68,30,"TEST-CUSTOMER-ITEM"),(98,12,"20000.0"),
                             (110,10,"IM"),(120,13,"0.16091"),(862,20,1)]),
        record("SRHEAD",443,[(7,1,0),(8,6,2),(14,8,2),(22,8,"SRC850"),(76,10,"SHIPTEST")]),
        record("SRLINE",567,[(7,1,0),(24,4,1),(28,30,"TEST-PO-1"),(83,30,"TEST-CUSTOMER-ITEM"),
                             (170,12,"20000.0"),(528,20,1)]),
        record("TOTALS",18,[(7,6,2),(13,6,"000006")])]
    return "\r\n".join(rows).encode("ascii")


class Fixed850InspectionTests(unittest.TestCase):
    def test_inspection_reports_missing_values_and_keeps_source_customer(self):
        source=incomplete_sample()
        report=inspect_fixed_850(source)[0]
        self.assertEqual(report["customer_code"],"SRC850")
        self.assertEqual(report["po"],"TEST-PO-1")
        self.assertEqual(report["order_date"],"2026-10-13")
        self.assertEqual(report["currency"],"")
        self.assertEqual(report["bill_reference"],"")
        self.assertEqual(report["missing_fields"],["SOHEAD.BillAddressRef","SOHEAD.CurrCode","SRHEAD.ShipDate","SOLINE[1].ReqDate"])
        self.assertEqual(report["lines"][0]["price_unit"],"")
        self.assertTrue(report["warnings"])
        self.assertEqual(source,incomplete_sample())

    def test_incomplete_source_still_blocks_submission_before_any_api_call(self):
        with self.assertRaisesRegex(EDIError,"CurrCode.*ShipDate"):
            parse_fixed_850(incomplete_sample())
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            path=base/"NIA-example.txt"
            path.write_bytes(incomplete_sample())
            radius=Mock()
            resolver=Mock()
            with self.assertRaisesRegex(EDIError,"missing required source fields"):
                process_file(path,base/"missing.json",{"environment":"TEST","customers":{}},
                             base/"state.sqlite3",submit=True,client=radius,resolver=resolver)
            radius.post.assert_not_called()
            resolver.resolve.assert_not_called()

    def test_inspection_still_rejects_bad_totals_and_line_association(self):
        with self.assertRaisesRegex(EDIError,"record count"):
            inspect_fixed_850(incomplete_sample().replace(b"000006",b"000005"))
        rows=incomplete_sample().splitlines()
        line=list(rows[4].decode())
        line[527:547]=list("9".ljust(20))
        rows[4]="".join(line).encode()
        with self.assertRaisesRegex(EDIError,"Unknown or duplicate"):
            inspect_fixed_850(b"\r\n".join(rows))

    def test_web_inspection_works_without_profile_and_requires_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            app=create_app(base/"state.sqlite3",username="a",password="b",config_path=base/"config.json")
            client=app.test_client()
            auth={"Authorization":"Basic "+base64.b64encode(b"a:b").decode()}
            self.assertEqual(client.get("/central/850-inspect").status_code,401)
            page=client.get("/central/850-inspect",headers=auth)
            token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
            self.assertEqual(client.post("/central/850-inspect",headers=auth).status_code,403)
            response=client.post("/central/850-inspect",headers=auth,
                data={"csrf_token":token,"file":(io.BytesIO(incomplete_sample()),"NIA-example.txt")})
            self.assertEqual(response.status_code,200)
            self.assertIn(b"SRC850",response.data)
            self.assertIn(b"Not configured",response.data)
            self.assertIn(b"SOHEAD.CurrCode",response.data)


if __name__ == "__main__":
    unittest.main()
