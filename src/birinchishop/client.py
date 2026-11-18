"""HTTP client for the Birinchi Shop Partner API v1 (standard library only)."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from ._version import __version__
from .errors import BirinchishopError

DEFAULT_BASE_URL = "https://birinchishop.uz/api/v1"
API_VERSION = "2026-10-23"

JSON = Dict[str, Any]


@dataclass
class HttpResponse:
    """What a transport returns. `headers` keys are compared case-insensitively."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> Optional[str]:
        n = name.lower()
        for k, v in self.headers.items():
            if k.lower() == n:
                return v
        return None


# transport(method, url, headers, body, timeout) -> HttpResponse; raise OSError for network errors
Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], HttpResponse]


def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https base URL)
            return HttpResponse(resp.status, resp.read(), dict(resp.headers.items()))
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, e.read() or b"", dict(e.headers.items()) if e.headers else {})


def _q(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def _id(value: Any, name: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    if n < 1 or (isinstance(value, str) and not value.isdigit()) or isinstance(value, bool):
        raise BirinchishopError(f"{name} must be a positive integer", code="invalid_request")
    return n


def _drop_none(d: Mapping[str, Any]) -> JSON:
    return {k: v for k, v in d.items() if v is not None}


def _retry_after(resp: HttpResponse) -> Optional[float]:
    raw = resp.header("Retry-After") or resp.header("RateLimit-Reset")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class Birinchishop:
    """Client for https://birinchishop.uz/api/v1.

    Every method returns the API envelope unchanged: ``{"data": ..., "meta"?: ..., "request_id": ...}``.
    Errors raise :class:`BirinchishopError`; branch on ``e.code``, not on the message.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: Optional[Transport] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BSK_KEY")
        if not self.api_key:
            raise BirinchishopError("API key is required: pass api_key or set BSK_KEY.", code="missing_api_key")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport: Transport = transport or urllib_transport
        self.user_agent = f"birinchishop-python/{__version__}" + (f" {user_agent}" if user_agent else "")
        self._sleep: Callable[[float], None] = time.sleep

    # ── low level ──────────────────────────────────────────────────────
    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        retry: Optional[bool] = None,
    ) -> JSON:
        url = self.base_url + path
        if query:
            pairs = [(k, s) for k, v in query.items() if (s := _q(v)) is not None]
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if headers:
            h.update(headers)
        data: Optional[bytes] = None
        if body is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        can_retry = (method == "GET") if retry is None else retry

        attempt = 0
        while True:
            try:
                resp = self._transport(method, url, h, data, self.timeout)
            except (OSError, TimeoutError) as e:  # URLError, socket.timeout, ConnectionError...
                if can_retry and attempt < self.max_retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e)
                raise BirinchishopError(
                    f"Request timed out after {self.timeout} s" if is_timeout else f"Network error: {e}",
                    code="timeout" if is_timeout else "network_error",
                ) from e

            try:
                payload: Any = json.loads(resp.body.decode("utf-8")) if resp.body else {}
            except (ValueError, UnicodeDecodeError):
                payload = None

            if 200 <= resp.status < 300 and isinstance(payload, dict):
                if (resp.header("Idempotent-Replayed") or "").lower() == "true":
                    payload["replayed"] = True
                return payload

            err = payload.get("error") if isinstance(payload, dict) else None
            err = err if isinstance(err, dict) else {}
            ra = _retry_after(resp)
            exc = BirinchishopError(
                err.get("message") or f"HTTP {resp.status}",
                status=resp.status,
                code=err.get("code") or ("invalid_response" if resp.status < 300 else f"http_{resp.status}"),
                details=err.get("details"),
                request_id=(payload.get("request_id") if isinstance(payload, dict) else None) or resp.header("X-Request-Id"),
                retry_after=ra,
                body=payload if payload is not None else resp.body,
            )
            transient = (resp.status == 429 and exc.code == "rate_limited") or (
                resp.status >= 500 and exc.code != "offer_temporarily_unavailable"
            )
            if can_retry and transient and attempt < self.max_retries:
                self._sleep(min(ra, 60.0) if ra is not None else self._backoff(attempt))
                attempt += 1
                continue
            raise exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(min(8.0, 0.5 * 2**attempt) + random.random() * 0.25)

    # ── account ────────────────────────────────────────────────────────
    def get_account(self) -> JSON:
        """GET /me — scope account:read."""
        return self.request("GET", "/me")

    # ── offers ─────────────────────────────────────────────────────────
    def list_offers(
        self,
        *,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        in_stock_only: Optional[bool] = None,
        min_sale_rate: Optional[int] = None,
        min_payout: Optional[int] = None,
        ids: Optional[Union[str, Sequence[int]]] = None,
        lang: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> JSON:
        """GET /offers — scope offers:read. sort: recommended | newest | best_selling | highest_payout |
        highest_stock | highest_lead_bonus. lang: uz | ru | en. Paginated (limit <= 100)."""
        return self.request("GET", "/offers", query=dict(
            search=search, sort=sort, in_stock_only=in_stock_only, min_sale_rate=min_sale_rate,
            min_payout=min_payout, ids=ids, lang=lang, page=page, limit=limit))

    def get_offer(self, offer_id: int, *, lang: Optional[str] = None) -> JSON:
        """GET /offers/{offer_id} — scope offers:read. Includes variants and ``my_links``."""
        return self.request("GET", f"/offers/{_id(offer_id, 'offer_id')}", query={"lang": lang})

    # ── links ──────────────────────────────────────────────────────────
    def list_links(self, *, offer_id: Optional[int] = None, page: Optional[int] = None, limit: Optional[int] = None) -> JSON:
        """GET /links — scope links:read. Paginated."""
        return self.request("GET", "/links", query=dict(offer_id=offer_id, page=page, limit=limit))

    def get_link(self, link_id: int) -> JSON:
        """GET /links/{link_id} — scope links:read."""
        return self.request("GET", f"/links/{_id(link_id, 'link_id')}")

    def create_link(self, offer_id: int, *, name: Optional[str] = None) -> JSON:
        """POST /links — scope links:write. Check ``get_offer(...)['data']['my_links']`` first to avoid duplicates."""
        return self.request("POST", "/links", body=_drop_none({"offer_id": offer_id, "name": name}))

    # ── orders ─────────────────────────────────────────────────────────
    def list_orders(
        self,
        *,
        status: Optional[str] = None,
        conversion_status: Optional[str] = None,
        date_from: Optional[Union[str, date]] = None,
        date_to: Optional[Union[str, date]] = None,
        updated_since: Optional[Union[str, datetime]] = None,
        link_id: Optional[int] = None,
        offer_id: Optional[int] = None,
        click_id: Optional[str] = None,
        external_id: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> JSON:
        """GET /orders — scope orders:read. Dates are Asia/Tashkent. Paginated."""
        return self.request("GET", "/orders", query=dict(
            status=status, conversion_status=conversion_status, date_from=date_from, date_to=date_to,
            updated_since=updated_since, link_id=link_id, offer_id=offer_id, click_id=click_id,
            external_id=external_id, page=page, limit=limit))

    def get_order(self, order_id: int) -> JSON:
        """GET /orders/{order_id} — scope orders:read."""
        return self.request("GET", f"/orders/{_id(order_id, 'order_id')}")

    def create_order(
        self,
        link_id: int,
        phone: str,
        *,
        name: Optional[str] = None,
        region: Optional[str] = None,
        variant_id: Optional[int] = None,
        variant_type_id: Optional[int] = None,
        external_id: Optional[str] = None,
        click_id: Optional[str] = None,
        sub1: Optional[str] = None,
        sub2: Optional[str] = None,
        sub3: Optional[str] = None,
        sub4: Optional[str] = None,
        sub5: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> JSON:
        """POST /orders — scope orders:write.

        WARNING: this creates a REAL order. A call center phones the customer and a courier delivers.
        Use it only for a real customer who asked to order, with their correct phone number.
        Never send test, sample or fake orders: the account gets blocked.
        Requires at least 3 delivered orders on the account (or admin approval).

        An ``Idempotency-Key`` is always sent (a random UUID if you do not pass one), so retries never
        create duplicates. Pass your own stable key (e.g. your lead id) to stay safe across restarts.
        A replay returns HTTP 200 and the result has ``replayed: True``.
        """
        body = _drop_none({
            "link_id": link_id, "phone": phone, "name": name, "region": region,
            "variant_id": variant_id, "variant_type_id": variant_type_id, "external_id": external_id,
            "click_id": click_id, "sub1": sub1, "sub2": sub2, "sub3": sub3, "sub4": sub4, "sub5": sub5,
        })
        key = idempotency_key or str(uuid.uuid4())
        return self.request("POST", "/orders", body=body, headers={"Idempotency-Key": key}, retry=True)

    def get_order_sale_chance(self, order_id: int) -> JSON:
        """GET /orders/{order_id}/sale-chance — scope insights:read."""
        return self.request("GET", f"/orders/{_id(order_id, 'order_id')}/sale-chance")

    # ── stats & events ─────────────────────────────────────────────────
    def get_stats(
        self,
        *,
        group_by: Optional[str] = None,
        date_from: Optional[Union[str, date]] = None,
        date_to: Optional[Union[str, date]] = None,
        link_id: Optional[int] = None,
        offer_id: Optional[int] = None,
    ) -> JSON:
        """GET /stats — scope stats:read. group_by: day | offer | link. Dates are Asia/Tashkent."""
        return self.request("GET", "/stats", query=dict(
            group_by=group_by, date_from=date_from, date_to=date_to, link_id=link_id, offer_id=offer_id))

    def list_events(
        self,
        *,
        after: Optional[int] = None,
        type: Optional[str] = None,  # noqa: A002 (API field name)
        include_order: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> JSON:
        """GET /events — scope events:read. Cursor-based: pass ``meta['next_cursor']`` as ``after``."""
        return self.request("GET", "/events", query=dict(after=after, type=type, include_order=include_order, limit=limit))

    # ── AI manager ─────────────────────────────────────────────────────
    def get_insights(self) -> JSON:
        """GET /insights — scope insights:read."""
        return self.request("GET", "/insights")

    # ── Meta ads ───────────────────────────────────────────────────────
    def get_ads_status(self) -> JSON:
        """GET /ads/status — scope ads:read."""
        return self.request("GET", "/ads/status")

    def list_campaigns(self, *, period: Optional[str] = None, include_archived: Optional[bool] = None) -> JSON:
        """GET /ads/campaigns — scope ads:read. period: 7d | 30d | 90d."""
        return self.request("GET", "/ads/campaigns", query=dict(period=period, include_archived=include_archived))

    def pause_campaign(self, campaign_id: int) -> JSON:
        """POST /ads/campaigns/{campaign_id}/pause — scope ads:write. Immediate."""
        return self.request("POST", f"/ads/campaigns/{_id(campaign_id, 'campaign_id')}/pause")

    def resume_campaign(self, campaign_id: int) -> JSON:
        """POST /ads/campaigns/{campaign_id}/resume — scope ads:write. Returns an action in status
        ``pending_confirmation``; the partner confirms it in the Telegram bot or panel."""
        return self.request("POST", f"/ads/campaigns/{_id(campaign_id, 'campaign_id')}/resume")

    def change_campaign_budget(self, campaign_id: int, daily_budget: float, *, unit: Optional[str] = None) -> JSON:
        """POST /ads/campaigns/{campaign_id}/budget — scope ads:write. unit: UZS (default) | ACCOUNT.
        Needs the partner's confirmation."""
        return self.request("POST", f"/ads/campaigns/{_id(campaign_id, 'campaign_id')}/budget",
                            body=_drop_none({"daily_budget": daily_budget, "unit": unit}))

    def get_action(self, action_id: int) -> JSON:
        """GET /actions/{action_id} — scope ads:read."""
        return self.request("GET", f"/actions/{_id(action_id, 'action_id')}")

    # ── webhooks / postbacks ───────────────────────────────────────────
    def list_webhooks(self) -> JSON:
        """GET /webhooks — scope webhooks:manage."""
        return self.request("GET", "/webhooks")

    def create_webhook(
        self,
        url: str,
        *,
        kind: Optional[str] = None,
        events: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> JSON:
        """POST /webhooks — scope webhooks:manage. kind: json (signed POST, default) | postback (GET with
        {macros}). The signing ``secret`` is returned once in this response."""
        return self.request("POST", "/webhooks", body=_drop_none(
            {"kind": kind, "url": url, "events": events, "description": description}))

    def update_webhook(
        self,
        webhook_id: int,
        *,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> JSON:
        """PATCH /webhooks/{webhook_id} — scope webhooks:manage."""
        return self.request("PATCH", f"/webhooks/{_id(webhook_id, 'webhook_id')}", body=_drop_none(
            {"url": url, "events": events, "description": description, "is_active": is_active}))

    def delete_webhook(self, webhook_id: int) -> JSON:
        """DELETE /webhooks/{webhook_id} — scope webhooks:manage."""
        return self.request("DELETE", f"/webhooks/{_id(webhook_id, 'webhook_id')}")

    def test_webhook(self, webhook_id: int) -> JSON:
        """POST /webhooks/{webhook_id}/test — scope webhooks:manage. Sends a sample event now."""
        return self.request("POST", f"/webhooks/{_id(webhook_id, 'webhook_id')}/test")

    def list_webhook_deliveries(self, webhook_id: int, *, limit: Optional[int] = None) -> JSON:
        """GET /webhooks/{webhook_id}/deliveries — scope webhooks:manage."""
        return self.request("GET", f"/webhooks/{_id(webhook_id, 'webhook_id')}/deliveries", query={"limit": limit})

    # ── reference ──────────────────────────────────────────────────────
    def get_reference(self) -> JSON:
        """GET /reference — enums, scopes, statuses and business rules."""
        return self.request("GET", "/reference")

    # ── pagination ─────────────────────────────────────────────────────
    def _paginate(self, fn: Callable[..., JSON], kwargs: Dict[str, Any]) -> Iterator[JSON]:
        page = kwargs.pop("page", None) or 1
        kwargs.setdefault("limit", 100)
        if kwargs["limit"] is None:
            kwargs["limit"] = 100
        while True:
            r = fn(page=page, **kwargs)
            items = r.get("data") or []
            yield from items
            total_pages = (r.get("meta") or {}).get("total_pages") or 0
            if not items or page >= total_pages:
                return
            page += 1

    def iter_offers(self, **kwargs: Any) -> Iterator[JSON]:
        """Iterate over all offers matching the filters of :meth:`list_offers`."""
        return self._paginate(self.list_offers, kwargs)

    def iter_links(self, **kwargs: Any) -> Iterator[JSON]:
        """Iterate over all your links."""
        return self._paginate(self.list_links, kwargs)

    def iter_orders(self, **kwargs: Any) -> Iterator[JSON]:
        """Iterate over all orders matching the filters of :meth:`list_orders`."""
        return self._paginate(self.list_orders, kwargs)

    def iter_events(self, *, after: int = 0, **kwargs: Any) -> Iterator[JSON]:
        """Iterate over events after ``after`` until ``has_more`` is false.
        Save the last processed ``event['id']`` and pass it as ``after`` next time."""
        while True:
            r = self.list_events(after=after, **kwargs)
            yield from r.get("data") or []
            meta = r.get("meta") or {}
            nxt = meta.get("next_cursor")
            if not meta.get("has_more") or nxt is None or nxt == after:
                return
            after = nxt
