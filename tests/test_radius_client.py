import base64
import json
import logging
import tempfile
import unittest
from pathlib import Path

from niagara_edi.radius import RadiusClient, RadiusError, RadiusRejected


class Response:
    def __init__(self, wrapper):
        self.wrapper = wrapper

    def raise_for_status(self):
        pass

    def json(self):
        return {"efiRadiusResponse": self.wrapper}


class Session:
    def __init__(self, wrapper):
        self.wrapper = wrapper
        self.request = None

    def post(self, url, *, json, headers, timeout):
        self.request = json
        return Response(self.wrapper)


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


class RadiusClientTests(unittest.TestCase):
    def test_decoded_debug_log_and_all_nested_errors(self):
        reply = {"XLSOrders": {"XLSOrder": [{
            "ErrorMessage": "Invalid billing address (917) Problem with billing and/or shipping address",
            "XLSOrderLine": [{"Action": "failed"} for _ in range(5)]}]}}
        session = Session({"entityName": "XLinkAPISOrder", "statusCode": 9,
                           "errorMessage": "HdrBillAddr -Invalid billing address (917)",
                           "payload": encoded(reply)})
        payload = {"XLSOrders": {"XLSOrder": [{"BillAddrNum": 999, "ShipAddrNum": 888}]}}
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "radius-debug.log"
            handler = logging.FileHandler(log_path, encoding="utf-8")
            radius_logger = logging.getLogger("niagara_edi.radius")
            old_level = radius_logger.level
            radius_logger.setLevel(logging.DEBUG)
            radius_logger.addHandler(handler)
            try:
                with self.assertRaises(RadiusRejected) as error:
                    RadiusClient("TEST", session=session).post("XLinkAPISOrder", payload,
                                                                "XLSOrders", "XLSOrder")
            finally:
                radius_logger.removeHandler(handler)
                radius_logger.setLevel(old_level)
                handler.close()
            logged = log_path.read_text(encoding="utf-8")
        self.assertIn("POST XLinkAPISOrder", logged)
        self.assertIn('"BillAddrNum": 999', logged)
        self.assertIn("RESPONSE XLinkAPISOrder", logged)
        self.assertIn("HdrBillAddr -Invalid billing address (917)", logged)
        self.assertIn("XLSOrders.XLSOrder[0].XLSOrderLine[4]: failed", str(error.exception))
        self.assertEqual(str(error.exception).count(": failed"), 5)

    def test_decodes_request_and_error_response_payload(self):
        response_payload = {"XLSOrders": {"XLSOrder": [{"XLSOrderLine": [
            {"ErrorMessage": "LneCompNum-Company number must be entered (59)"}]}]}}
        session = Session({"entityName": "XLinkAPISOrder", "statusCode": 9,
                           "payload": encoded(response_payload)})
        payload = {"XLSOrders": {"XLSOrder": [{"XLSOrderLine": [{"LneCompNum": 2}]}]}}
        with self.assertRaises(RadiusError) as error:
            RadiusClient("TEST", session=session).post("XLinkAPISOrder", payload,
                                                        "XLSOrders", "XLSOrder")
        sent = json.loads(base64.b64decode(session.request["efiRadiusRequest"]["payload"]))
        self.assertEqual(sent, payload)
        self.assertIn("XLSOrderLine[0]", str(error.exception))
        self.assertIn("LneCompNum-Company number must be entered (59)", str(error.exception))

    def test_success_status_with_line_error_is_rejected(self):
        response_payload = {"XLSOrders": {"XLSOrder": [{"SOrderNum": 0, "XLSOrderLine": [
            {"ErrorMessage": "Invalid customer item"}]}]}}
        session = Session({"entityName": "XLinkAPISOrder", "statusCode": 1,
                           "payload": encoded(response_payload)})
        with self.assertRaisesRegex(RadiusError, "Invalid customer item"):
            RadiusClient("TEST", session=session).post("XLinkAPISOrder", {},
                                                        "XLSOrders", "XLSOrder")


if __name__ == "__main__":
    unittest.main()
