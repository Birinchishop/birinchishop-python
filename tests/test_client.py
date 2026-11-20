"""Unit tests — no network: a fake transport records requests and returns canned responses."""

import hashlib
import hmac
import json
import os
import time
import unittest
import urllib.parse
from pathlib import Path

import birinchishop
from birinchishop import (
    AuthenticationError,
    Birinchishop,
    BirinchishopError,
    ConflictError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    HttpResponse,
    SignatureVerificationError,
    __version__,
    construct_event,
    fill_tracking_url,
    verify_signature,
)

KEY = "bsk_live_test_xxxxxxxx"
SPEC = Path(__file__).parent / "fixtures" / "openapi.json"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected request " + url)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def ok(data, meta=None, status=200, headers=None):
    body = {"data": data, "request_id": "req_1"}
    if meta is not None:
        body["meta"] = meta
    return HttpResponse(status, json.dumps(body).encode(), headers or {"Content-Type": "application/json"})


def err(status, code, message="x", headers=None, details=None):
    e = {"code": code, "message": message}
    if details is not None:
        e["details"] = details
    return HttpResponse(status, json.dumps({"error": e, "request_id": "req_e"}).encode(), headers or {})


def make(responses, **kw):
    t = FakeTransport(responses)
    c = Birinchishop(KEY, transport=t, **kw)
    c._sleep = lambda s: None
    return c, t


def path_of(call):
    return urllib.parse.urlsplit(call["url"]).path


def query_of(call):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(call["url"]).query))


class ClientTests(unittest.TestCase):
    def test_requires_key(self):
        old = os.environ.pop("BSK_KEY", None)
        try:
            with self.assertRaises(BirinchishopError) as cm:
                Birinchishop()
            self.assertEqual(cm.exception.code, "missing_api_key")
        finally:
            if old is not None:
                os.environ["BSK_KEY"] = old

    def test_key_from_env(self):
        os.environ["BSK_KEY"] = KEY
        try:
            self.assertEqual(Birinchishop().api_key, KEY)
        finally:
            del os.environ["BSK_KEY"]

    def test_headers_and_envelope(self):
        c, t = make([ok({"id": 7, "balance": 1000})])
        r = c.get_account()
        self.assertEqual(r["data"]["id"], 7)
        self.assertEqual(r["request_id"], "req_1")
        call = t.calls[0]
        self.assertEqual(call["url"], "https://birinchishop.uz/api/v1/me")
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(call["headers"]["User-Agent"], "birinchishop-python/" + __version__)

    def test_list_offers_query(self):
        c, t = make([ok([], {"page": 1, "total_pages": 0})])
        c.list_offers(search="soat", sort="best_selling", in_stock_only=False, ids=[1, 2], lang="ru")
        q = query_of(t.calls[0])
        self.assertEqual(q, {"search": "soat", "sort": "best_selling", "in_stock_only": "false", "ids": "1,2", "lang": "ru"})

    def test_path_ids_validated(self):
        c, t = make([ok({}), ok({})])
        c.get_offer(5, lang="en")
        c.get_order_sale_chance("9")
        self.assertEqual(t.calls[0]["url"], "https://birinchishop.uz/api/v1/offers/5?lang=en")
        self.assertEqual(path_of(t.calls[1]), "/api/v1/orders/9/sale-chance")
        for bad in ("1/../me", 0, -1, None, True):
            with self.assertRaises(BirinchishopError):
                c.get_order(bad)
        self.assertEqual(len(t.calls), 2)

    def test_create_link_body(self):
        c, t = make([ok({"id": 11}, status=201)])
        c.create_link(432, name="Reels")
        self.assertEqual(t.calls[0]["method"], "POST")
        self.assertEqual(t.calls[0]["body"], {"offer_id": 432, "name": "Reels"})
        self.assertEqual(t.calls[0]["headers"]["Content-Type"], "application/json")

    def test_create_order_idempotency_kept_across_retries(self):
        c, t = make([err(500, "internal_error"), ok({"id": 99, "status": "new"}, status=201)])
        r = c.create_order(1, "+998901234567", name="Ali", sub1="reels")
        self.assertEqual(r["data"]["id"], 99)
        k1 = t.calls[0]["headers"]["Idempotency-Key"]
        self.assertEqual(len(k1), 36)
        self.assertEqual(k1, t.calls[1]["headers"]["Idempotency-Key"])
        self.assertEqual(t.calls[0]["body"], {"link_id": 1, "phone": "+998901234567", "name": "Ali", "sub1": "reels"})

    def test_create_order_replay(self):
        c, t = make([ok({"id": 99}, headers={"idempotent-replayed": "true"})])
        r = c.create_order(1, "+998901234567", external_id="crm-1", idempotency_key="crm-1")
        self.assertEqual(t.calls[0]["headers"]["Idempotency-Key"], "crm-1")
        self.assertTrue(r["replayed"])

    def test_api_error_fields(self):
        c, t = make([err(409, "duplicate_order", "dup", details=[{"field": "phone"}])])
        with self.assertRaises(BirinchishopError) as cm:
            c.create_order(1, "+998901234567")
        e = cm.exception
        self.assertEqual((e.status, e.code, e.request_id, e.details), (409, "duplicate_order", "req_e", [{"field": "phone"}]))
        self.assertEqual(len(t.calls), 1)

    def test_rate_limited_retried(self):
        c, t = make([err(429, "rate_limited", headers={"Retry-After": "0"}), ok([])])
        waits = []
        c._sleep = waits.append
        c.list_orders(status="delivered")
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(waits, [0.0])

    def test_non_transient_not_retried(self):
        for status, code in ((429, "daily_order_limit"), (503, "offer_temporarily_unavailable"), (403, "insufficient_scope")):
            c, t = make([err(status, code)])
            with self.assertRaises(BirinchishopError) as cm:
                c.create_order(1, "+998901234567")
            self.assertEqual(cm.exception.code, code)
            self.assertEqual(len(t.calls), 1)

    def test_other_posts_not_retried(self):
        c, t = make([HttpResponse(502, b"Bad gateway", {})])
        with self.assertRaises(BirinchishopError) as cm:
            c.resume_campaign(4)
        self.assertEqual((cm.exception.status, cm.exception.code), (502, "http_502"))
        self.assertEqual(len(t.calls), 1)

    def test_network_error(self):
        c, t = make([ConnectionResetError("reset"), ConnectionResetError("reset")], max_retries=1)
        with self.assertRaises(BirinchishopError) as cm:
            c.get_insights()
        self.assertEqual((cm.exception.code, cm.exception.status), ("network_error", 0))
        self.assertEqual(len(t.calls), 2)

    def test_timeout(self):
        c, t = make([TimeoutError("timed out")], max_retries=0)
        with self.assertRaises(BirinchishopError) as cm:
            c.get_account()
        self.assertEqual(cm.exception.code, "timeout")

    def test_all_endpoints(self):
        cases = [
            ("get_account", (), {}, "GET", "/me"),
            ("list_offers", (), {}, "GET", "/offers"),
            ("get_offer", (1,), {}, "GET", "/offers/1"),
            ("list_links", (), {"offer_id": 1}, "GET", "/links"),
            ("create_link", (1,), {}, "POST", "/links"),
            ("get_link", (2,), {}, "GET", "/links/2"),
            ("list_orders", (), {}, "GET", "/orders"),
            ("get_order", (3,), {}, "GET", "/orders/3"),
            ("get_stats", (), {"group_by": "offer"}, "GET", "/stats"),
            ("list_events", (), {"after": 5}, "GET", "/events"),
            ("get_order_sale_chance", (3,), {}, "GET", "/orders/3/sale-chance"),
            ("set_test_order_status", (2000000001, "delivered"), {}, "POST", "/sandbox/orders/2000000001/status"),
            ("get_insights", (), {}, "GET", "/insights"),
            ("get_ads_status", (), {}, "GET", "/ads/status"),
            ("list_campaigns", (), {"period": "30d"}, "GET", "/ads/campaigns"),
            ("pause_campaign", (4,), {}, "POST", "/ads/campaigns/4/pause"),
            ("resume_campaign", (4,), {}, "POST", "/ads/campaigns/4/resume"),
            ("change_campaign_budget", (4, 50000), {}, "POST", "/ads/campaigns/4/budget"),
            ("get_action", (6,), {}, "GET", "/actions/6"),
            ("list_webhooks", (), {}, "GET", "/webhooks"),
            ("create_webhook", ("https://example.com/h",), {"kind": "json"}, "POST", "/webhooks"),
            ("update_webhook", (8,), {"is_active": True}, "PATCH", "/webhooks/8"),
            ("delete_webhook", (8,), {}, "DELETE", "/webhooks/8"),
            ("test_webhook", (8,), {}, "POST", "/webhooks/8/test"),
            ("list_webhook_deliveries", (8,), {"limit": 10}, "GET", "/webhooks/8/deliveries"),
            ("get_reference", (), {}, "GET", "/reference"),
        ]
        t = FakeTransport([ok({}) for _ in cases])
        c = Birinchishop(TEST_KEY, transport=t)
        for name, args, kwargs, _, _ in cases:
            getattr(c, name)(*args, **kwargs)
        for i, (name, _, _, method, path) in enumerate(cases):
            self.assertEqual(t.calls[i]["method"], method, name)
            self.assertEqual(path_of(t.calls[i]), "/api/v1" + path, name)
        self.assertEqual(t.calls[11]["body"], {"status": "delivered"})
        self.assertEqual(t.calls[17]["body"], {"daily_budget": 50000})
        self.assertEqual(t.calls[21]["body"], {"is_active": True})

    def test_every_operation_has_a_method(self):
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        c, _ = make([])

        def snake(name):
            out = ""
            for ch in name:
                out += ("_" + ch.lower()) if ch.isupper() else ch
            return out

        for p, item in spec["paths"].items():
            for method, op in item.items():
                op_id = op.get("operationId") if isinstance(op, dict) else None
                if not op_id or op_id == "mcp":
                    continue
                self.assertTrue(callable(getattr(c, snake(op_id), None)), f"{method.upper()} {p} -> {snake(op_id)}")

    def test_iter_orders(self):
        c, t = make([
            ok([{"id": 1}, {"id": 2}], {"page": 1, "total_pages": 2}),
            ok([{"id": 3}], {"page": 2, "total_pages": 2}),
        ])
        ids = [o["id"] for o in c.iter_orders(conversion_status="sale", limit=2)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(query_of(t.calls[1]), {"conversion_status": "sale", "page": "2", "limit": "2"})

    def test_iter_events(self):
        c, t = make([
            ok([{"id": 10}, {"id": 11}], {"next_cursor": 11, "has_more": True}),
            ok([{"id": 12}], {"next_cursor": 12, "has_more": False}),
        ])
        ids = [e["id"] for e in c.iter_events(after=9, include_order=True)]
        self.assertEqual(ids, [10, 11, 12])
        self.assertEqual(query_of(t.calls[1]), {"after": "11", "include_order": "true"})


SECRET = "whsec_test"


def sign(body: bytes, t: int) -> str:
    return f"t={t},v1=" + hmac.new(SECRET.encode(), str(t).encode() + b"." + body, hashlib.sha256).hexdigest()


class WebhookTests(unittest.TestCase):
    def test_valid(self):
        body = json.dumps({"id": "evt_1", "type": "order.created", "data": {"order_id": 1}}).encode()
        t = int(time.time())
        self.assertTrue(verify_signature(body, sign(body, t), SECRET))
        self.assertTrue(verify_signature(body.decode(), sign(body, t), SECRET))

    def test_invalid(self):
        body = b'{"id":"evt_1"}'
        t = int(time.time())
        self.assertFalse(verify_signature(body + b" ", sign(body, t), SECRET))
        self.assertFalse(verify_signature(body, sign(body, t), "other"))
        self.assertFalse(verify_signature(body, sign(body, t - 301), SECRET))
        self.assertTrue(verify_signature(body, sign(body, t - 301), SECRET, tolerance=600))
        self.assertFalse(verify_signature(body, "", SECRET))
        self.assertFalse(verify_signature(body, None, SECRET))
        self.assertFalse(verify_signature(body, f"t={t},v1=zz", SECRET))
        self.assertFalse(verify_signature(body, "v1=" + "a" * 64, SECRET))
        with self.assertRaises(TypeError):
            verify_signature({"id": 1}, sign(body, t), SECRET)

    def test_construct_event(self):
        body = json.dumps({"id": "evt_5", "data": {"conversion_status": "sale"}}).encode()
        t = 1_800_000_000
        self.assertEqual(construct_event(body, sign(body, t), SECRET, now=t + 10)["data"]["conversion_status"], "sale")
        with self.assertRaises(SignatureVerificationError):
            construct_event(body, sign(body, t), SECRET, now=t + 1000)

    def test_node_and_python_signatures_match(self):
        # Same algorithm as the server: hex HMAC-SHA256(secret, "<t>.<raw body>")
        body = '{"id":"evt_9","type":"order.status_changed"}'
        t = 1_800_000_000
        header = sign(body.encode(), t)
        self.assertTrue(verify_signature(body, header, SECRET, now=t))

    def test_fill_tracking_url(self):
        tpl = "https://birinchishop.uz/l/abc123?click_id={click_id}&sub1={sub1}&sub2={sub2}&sub3={sub3}&sub4={sub4}&sub5={sub5}"
        self.assertEqual(fill_tracking_url(tpl, click_id="{clickid}", sub1="camp 1&x"),
                         "https://birinchishop.uz/l/abc123?click_id={clickid}&sub1=camp%201%26x")
        self.assertEqual(fill_tracking_url(tpl), "https://birinchishop.uz/l/abc123")


TEST_KEY = "bsk_test_AAAAAAAAAAAA_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def make_test(responses, **kw):
    t = FakeTransport(responses)
    return Birinchishop(TEST_KEY, transport=t, sleeper=lambda s: None, **kw), t


class TestModeTests(unittest.TestCase):
    """0.2.0: test mode (sandbox), error subclasses, postback macros."""

    def test_is_test_mode_follows_the_key_prefix(self):
        self.assertFalse(make([])[0].is_test_mode())
        self.assertTrue(make_test([])[0].is_test_mode())
        self.assertEqual(birinchishop.TEST_KEY_PREFIX, "bsk_test_")
        self.assertEqual(birinchishop.LIVE_KEY_PREFIX, "bsk_live_")
        self.assertEqual(birinchishop.TEST_ID_BASE, 2000000000)

    def test_set_test_order_status_refuses_live_keys_without_a_request(self):
        c, t = make([])
        with self.assertRaises(PermissionDeniedError) as cm:
            c.set_test_order_status(2000000001, "delivered")
        self.assertEqual(cm.exception.code, "test_mode_only")
        self.assertIsInstance(cm.exception, BirinchishopError)
        self.assertEqual(t.calls, [])

    def test_set_test_order_status_with_a_test_key(self):
        c, t = make_test([ok({"order": {"id": 2000000001, "status": "delivered", "test": True},
                              "previous_status": "new", "webhooks": {"matched": 0, "sent": 0}})])
        r = c.set_test_order_status("2000000001", "delivered")
        self.assertEqual(r["data"]["order"]["status"], "delivered")
        self.assertEqual(t.calls[0]["method"], "POST")
        self.assertEqual(path_of(t.calls[0]), "/api/v1/sandbox/orders/2000000001/status")
        self.assertEqual(t.calls[0]["body"], {"status": "delivered"})
        self.assertEqual(t.calls[0]["headers"]["Authorization"], "Bearer " + TEST_KEY)

    def test_set_test_order_status_is_not_retried(self):
        sleeps = []
        t = FakeTransport([err(500, "internal_error")])
        c = Birinchishop(TEST_KEY, transport=t, sleeper=sleeps.append)
        with self.assertRaises(ServerError):
            c.set_test_order_status(2000000001, "shipping")
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(sleeps, [])

    def test_create_order_with_a_test_key_is_the_same_request(self):
        resp = ok({"id": 2000000123, "status": "new", "test": True, "live_orders_allowed": False}, status=201,
                  headers={"Content-Type": "application/json", "X-Birinchishop-Mode": "test"})
        c, t = make_test([resp])
        r = c.create_order(1, "+998901234567", idempotency_key="lead-1")
        self.assertTrue(r["data"]["test"])
        self.assertFalse(r["data"]["live_orders_allowed"])
        self.assertGreaterEqual(r["data"]["id"], birinchishop.TEST_ID_BASE)
        self.assertEqual(t.calls[0]["headers"]["Idempotency-Key"], "lead-1")
        self.assertEqual(path_of(t.calls[0]), "/api/v1/orders")

    def test_not_available_in_test_mode_is_permission_denied(self):
        c, _ = make_test([err(403, "not_available_in_test_mode")])
        with self.assertRaises(PermissionDeniedError) as cm:
            c.pause_campaign(1)
        self.assertEqual(cm.exception.code, "not_available_in_test_mode")

    def test_statuses_map_to_error_subclasses(self):
        cases = [(400, InvalidRequestError), (413, InvalidRequestError), (415, InvalidRequestError),
                 (422, InvalidRequestError), (401, AuthenticationError), (403, PermissionDeniedError),
                 (404, NotFoundError), (409, ConflictError), (429, RateLimitError), (500, ServerError),
                 (503, ServerError), (418, BirinchishopError)]
        for status, cls in cases:
            c, _ = make([err(status, "code_%d" % status)], max_retries=0)
            with self.assertRaises(cls) as cm:
                c.create_link(1)
            self.assertIs(type(cm.exception), cls, status)
            self.assertEqual(cm.exception.status, status)
            self.assertIsInstance(cm.exception, BirinchishopError)
        c, _ = make([OSError("down")], max_retries=0)
        with self.assertRaises(NetworkError):
            c.get_account()
        old = os.environ.pop("BSK_KEY", None)
        try:
            with self.assertRaises(AuthenticationError):
                Birinchishop("   ")
        finally:
            if old is not None:
                os.environ["BSK_KEY"] = old

    def test_create_order_validates_before_sending(self):
        c, t = make([])
        for kwargs, field_name in (({"link_id": 1, "phone": ""}, "phone"), ({"link_id": None, "phone": "+998901234567"}, "link_id"),
                                   ({"link_id": 1, "phone": "+998901234567", "idempotency_key": "bad key!"}, "Idempotency-Key"),
                                   ({"link_id": 1, "phone": "+998901234567", "idempotency_key": "x" * 129}, "Idempotency-Key")):
            with self.assertRaises(InvalidRequestError) as cm:
                c.create_order(**kwargs)
            self.assertEqual(cm.exception.details[0]["field"], field_name)
        self.assertEqual(t.calls, [])

    def test_path_ids(self):
        c, t = make([ok({}), ok({})])
        c.get_order(2147483647)
        c.get_order(5.0)
        self.assertEqual(path_of(t.calls[1]), "/api/v1/orders/5")
        for bad in (0, -1, 1.5, 2147483648, "1e3", " 3", "", None, True, "\u0663"):
            with self.assertRaises(InvalidRequestError, msg=repr(bad)):
                c.get_order(bad)
        self.assertEqual(len(t.calls), 2)

    def test_datetime_date_filters_are_tashkent_days(self):
        from datetime import datetime, timezone
        c, t = make([ok([], {"total_pages": 0}), ok([])])
        c.list_orders(date_from=datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc), date_to=datetime(2026, 11, 2, 10, 0),
                      updated_since=datetime(2026, 11, 1, tzinfo=timezone.utc))
        q = query_of(t.calls[0])
        self.assertEqual(q["date_from"], "2026-11-02")
        self.assertEqual(q["date_to"], "2026-11-02")
        self.assertEqual(q["updated_since"], "2026-11-01T00:00:00+00:00")
        c.get_stats(date_from=datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(query_of(t.calls[1])["date_from"], "2026-11-04")

    def test_retry_after_http_date(self):
        from email.utils import format_datetime
        from datetime import datetime, timedelta, timezone
        when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
        sleeps = []
        t = FakeTransport([err(429, "rate_limited", headers={"Retry-After": when}), ok([])])
        Birinchishop(KEY, transport=t, sleeper=sleeps.append).list_orders()
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(25 <= sleeps[0] <= 31, sleeps)

    def test_paginate(self):
        c, t = make([ok([{"id": 1}], {"page": 1, "total_pages": 1})])
        self.assertEqual([o["id"] for o in c.paginate("listLinks", offer_id=4)], [1])
        self.assertEqual(query_of(t.calls[0])["offer_id"], "4")
        with self.assertRaises(InvalidRequestError):
            list(c.paginate("list_events"))

    def test_postback_macros(self):
        self.assertIn("payout_earned", birinchishop.POSTBACK_MACROS)
        self.assertIn("test", birinchishop.POSTBACK_MACROS)
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        self.assertEqual(list(birinchishop.POSTBACK_MACROS), spec["x-postback-macros"])
        self.assertNotIn("new", birinchishop.TEST_ORDER_STATUSES)

    def test_test_webhook_events(self):
        t = 1_800_000_000
        body = b'{"id":"evt_test_abc","type":"order.created","test":true,"data":{"order_id":2000000001}}'
        self.assertTrue(construct_event(body, sign(body, t), SECRET, now=t)["test"])
        self.assertEqual(birinchishop.TEST_HEADER, "X-Birinchishop-Test")
        with self.assertRaises(ValueError):
            construct_event(b"42", sign(b"42", t), SECRET, now=t)
        with self.assertRaises(ValueError):
            construct_event(b"not json", sign(b"not json", t), SECRET, now=t)

    def test_version_and_changelog(self):
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(__version__, "0.2.0")
        self.assertIn('version = "%s"' % __version__, (root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("## [%s]" % __version__, (root / "CHANGELOG.md").read_text(encoding="utf-8"))
        for name in birinchishop.__all__:
            self.assertTrue(hasattr(birinchishop, name), name)


if __name__ == "__main__":
    unittest.main()
