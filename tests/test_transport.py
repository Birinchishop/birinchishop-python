"""The real urllib transport against a loopback HTTP server (127.0.0.1, no internet)."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from birinchishop import Birinchishop, BirinchishopError


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer bsk_live_loop_x":
            return self._send(401, {"error": {"code": "unauthorized", "message": "Invalid API key."}, "request_id": "req_a"})
        if self.path.startswith("/api/v1/offers"):
            return self._send(200, {"data": [{"id": 1}], "meta": {"page": 1, "total_pages": 1}, "request_id": "req_b", "echo": self.path})
        return self._send(404, {"error": {"code": "not_found", "message": "No route"}, "request_id": "req_c"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        self._send(201, {"data": {"id": 5, "echo": body, "key": self.headers.get("Idempotency-Key")}, "request_id": "req_d"},
                   {"Idempotent-Replayed": "false"})


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}/api/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_get_ok(self):
        r = Birinchishop("bsk_live_loop_x", base_url=self.base).list_offers(search="soat", in_stock_only=True)
        self.assertEqual(r["data"][0]["id"], 1)
        self.assertEqual(r["echo"], "/api/v1/offers?search=soat&in_stock_only=true")

    def test_http_error(self):
        with self.assertRaises(BirinchishopError) as cm:
            Birinchishop("bsk_live_wrong", base_url=self.base).get_account()
        self.assertEqual((cm.exception.status, cm.exception.code, cm.exception.request_id), (401, "unauthorized", "req_a"))

    def test_post_json(self):
        r = Birinchishop("bsk_live_loop_x", base_url=self.base).create_link(7, name="Ўзбек")
        self.assertEqual(r["data"]["echo"], {"offer_id": 7, "name": "Ўзбек"})
        self.assertNotIn("replayed", r)

    def test_connection_refused(self):
        c = Birinchishop("bsk_live_loop_x", base_url="http://127.0.0.1:9/api/v1", max_retries=0, timeout=2)
        with self.assertRaises(BirinchishopError) as cm:
            c.get_account()
        self.assertEqual(cm.exception.code, "network_error")


if __name__ == "__main__":
    unittest.main()
