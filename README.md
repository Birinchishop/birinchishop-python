# birinchishop — Python client for the Birinchi Shop Partner API

Official Python (3.8+) client for the **[Birinchi Shop](https://birinchishop.uz) Partner API v1** —
a COD (cash on delivery) CPA affiliate network in Uzbekistan. Partners promote ready products; the network runs the
call center, warehouse and nationwide delivery and pays a fixed amount for every **delivered** order.

- Standard library only (`urllib`), no dependencies; type hints (`py.typed`)
- Every REST endpoint of `/api/v1` (offers, links, orders, stats, events, AI insights, Meta ads, webhooks)
- Automatic retries for `429 rate_limited`, `5xx` and network errors (GET requests and `create_order` with an `Idempotency-Key`)
- Pagination iterators, webhook signature verification, tracking-link builder

API docs: https://birinchishop.uz/api/v1/docs · OpenAPI: https://birinchishop.uz/api/v1/openapi.json ·
Guide for AI assistants: https://birinchishop.uz/api/v1/guide.md · More examples: https://github.com/Birinchishop/birinchishop-api-examples

## Install

```bash
pip install birinchishop
```

## Quick start

Create a key in the partner panel (**Dashboard → API**) with only the scopes you need. The key is a **server-side secret**:
keep it in an environment variable, never in a repository or client-side code.

```python
from birinchishop import Birinchishop

bs = Birinchishop()  # reads BSK_KEY from the environment, or Birinchishop("bsk_live_...")

me = bs.get_account()["data"]
print(me["name"], me["balance"], me["currency"])

# Best-selling offers in stock with a good sale rate
for o in bs.list_offers(sort="best_selling", min_sale_rate=25, limit=10, lang="en")["data"]:
    print(o["id"], o["name"], o["payout"], o["sale_rate_percent"])

# A tracking link (check my_links first — do not create duplicates)
offer = bs.get_offer(432)["data"]
link = offer["my_links"][0] if offer["my_links"] else bs.create_link(432, name="Instagram reels")["data"]
print(link["url"], link["tracking_url_template"])
```

Every method returns the API envelope unchanged: `{"data": ..., "meta"?: ..., "request_id": ...}`.
Method names are the OpenAPI `operationId`s in snake_case; parameters keep the API's field names.

## Orders

```python
# All delivered orders (payout earned), every page
for order in bs.iter_orders(conversion_status="sale", date_from="2026-11-01"):
    print(order["id"], order["payout"], (order["tracking"] or {}).get("click_id"))
```

### Submitting an order — read this first

> **`create_order` creates a REAL order.** A call center phones the customer and a courier delivers the product.
> Use it only for a real customer who asked to order, with their correct phone number.
> **Never send test, sample or fake orders — the account gets blocked.**
> It needs the `orders:write` scope and an account with at least 3 delivered orders (or admin approval).

```python
res = bs.create_order(
    link_id=2001, phone=customer_phone, name=customer_name, region=customer_region,
    external_id=lead_id, sub1="reels",
    idempotency_key=f"lead-{lead_id}",  # same key on retry => same order, never a duplicate
)
print(res["data"]["id"], res["data"]["status"], "(already existed)" if res.get("replayed") else "")
```

Without `idempotency_key` the SDK generates one per call, so its own retries are safe.

## Errors

```python
from birinchishop import BirinchishopError

try:
    bs.create_order(link_id=2001, phone=customer_phone)
except BirinchishopError as e:
    if e.code == "duplicate_order":            # same phone + offer within 24 h — do not retry
        ...
    elif e.code == "orders_write_not_allowed": # needs 3 delivered orders or admin approval
        ...
    else:
        print(e.status, e.code, e.message, e.request_id)  # send request_id to support
```

Attributes: `status` (HTTP, `0` for network errors), `code`, `message`, `details`, `request_id`, `retry_after`.
Client-side codes: `network_error`, `timeout`, `missing_api_key`, `invalid_request`, `http_<status>`.

## Events and webhooks

Poll the event feed with a cursor:

```python
cursor = load_cursor()  # 0 the first time
for ev in bs.iter_events(after=cursor, include_order=True):
    print(ev["type"], ev["order_id"], ev["previous_status"], "->", ev["status"], ev["conversion_status"])
    cursor = ev["id"]
save_cursor(cursor)
```

Or receive signed webhooks (`kind="json"`). Verify `X-Birinchishop-Signature` on the **raw** body (Flask example):

```python
import os
from flask import Flask, request, abort
from birinchishop import construct_event, SignatureVerificationError

app = Flask(__name__)

@app.post("/hooks/birinchishop")
def hook():
    try:
        event = construct_event(request.get_data(), request.headers.get("X-Birinchishop-Signature"),
                                os.environ["BSK_WEBHOOK_SECRET"])
    except SignatureVerificationError:
        abort(401)
    # Events may arrive twice or out of order: deduplicate by event["id"]. Answer within 10 s.
    print(event["type"], event["data"]["order_id"], event["data"]["conversion_status"])
    return "ok"
```

`verify_signature(raw_body, header, secret, tolerance=300)` returns a bool if you prefer.
Register endpoints with `create_webhook(url, kind="json" | "postback", events=[...])` (scope `webhooks:manage`);
the signing `secret` is returned **once**. For S2S postbacks to Keitaro/Binom see
[integrations](https://github.com/Birinchishop/birinchishop-api-examples/tree/main/integrations).

```python
from birinchishop import fill_tracking_url
fill_tracking_url(link["tracking_url_template"], click_id="{clickid}", sub1="{campaign_id}")
# https://birinchishop.uz/l/<link>?click_id={clickid}&sub1={campaign_id}   (unused sub2..sub5 removed)
```

## All methods

| Method | Endpoint | Scope |
|---|---|---|
| `get_account()` | `GET /me` | `account:read` |
| `list_offers(...)` / `iter_offers(...)` | `GET /offers` | `offers:read` |
| `get_offer(offer_id, lang=)` | `GET /offers/{offer_id}` | `offers:read` |
| `list_links(...)` / `iter_links(...)` | `GET /links` | `links:read` |
| `get_link(link_id)` | `GET /links/{link_id}` | `links:read` |
| `create_link(offer_id, name=)` | `POST /links` | `links:write` |
| `list_orders(...)` / `iter_orders(...)` | `GET /orders` | `orders:read` |
| `get_order(order_id)` | `GET /orders/{order_id}` | `orders:read` |
| `create_order(link_id, phone, ...)` | `POST /orders` — **real order** | `orders:write` |
| `get_order_sale_chance(order_id)` | `GET /orders/{order_id}/sale-chance` | `insights:read` |
| `get_stats(group_by=, date_from=, date_to=, link_id=, offer_id=)` | `GET /stats` | `stats:read` |
| `list_events(...)` / `iter_events(...)` | `GET /events` | `events:read` |
| `get_insights()` | `GET /insights` | `insights:read` |
| `get_ads_status()` | `GET /ads/status` | `ads:read` |
| `list_campaigns(period=, include_archived=)` | `GET /ads/campaigns` | `ads:read` |
| `pause_campaign(id)` | `POST /ads/campaigns/{id}/pause` (immediate) | `ads:write` |
| `resume_campaign(id)` | `POST /ads/campaigns/{id}/resume` (partner confirms) | `ads:write` |
| `change_campaign_budget(id, daily_budget, unit=)` | `POST /ads/campaigns/{id}/budget` (partner confirms) | `ads:write` |
| `get_action(action_id)` | `GET /actions/{action_id}` | `ads:read` |
| `list_webhooks()` | `GET /webhooks` | `webhooks:manage` |
| `create_webhook(url, kind=, events=, description=)` | `POST /webhooks` | `webhooks:manage` |
| `update_webhook(id, url=, events=, description=, is_active=)` | `PATCH /webhooks/{id}` | `webhooks:manage` |
| `delete_webhook(id)` | `DELETE /webhooks/{id}` | `webhooks:manage` |
| `test_webhook(id)` | `POST /webhooks/{id}/test` | `webhooks:manage` |
| `list_webhook_deliveries(id, limit=)` | `GET /webhooks/{id}/deliveries` | `webhooks:manage` |
| `get_reference()` | `GET /reference` | — |

Options: `Birinchishop(api_key=None, base_url="https://birinchishop.uz/api/v1", timeout=30.0, max_retries=2, transport=None)`.
`transport` lets you plug in your own HTTP stack (e.g. for proxies or tests).

## Rules that matter

- Money is integer UZS. Date filters use Asia/Tashkent (UTC+5); timestamps are ISO 8601 UTC.
- Payout is earned only when an order is **delivered** (`conversion_status == "sale"`).
- One phone number cannot order the same offer twice within 24 hours, and at most 3 different offers per 24 hours.
- Customer phone numbers are always masked.
- Rate limits: 120 requests/minute per key, 30 orders/minute per account.

---

**O'zbekcha.** Birinchi Shop hamkorlik API'si uchun rasmiy Python kutubxonasi (faqat standart kutubxona, Python 3.8+).
O'rnatish: `pip install birinchishop`. Kalitni panelda yarating (Dashboard → API) va faqat serverdagi `BSK_KEY` muhit o'zgaruvchisida saqlang.
`create_order` — **haqiqiy zakaz**: call-center mijozga qo'ng'iroq qiladi. Sinov yoki soxta zakaz yubormang — akkaunt bloklanadi.

**Русский.** Официальный клиент Python для партнёрского API Birinchi Shop (только стандартная библиотека, Python 3.8+).
Установка: `pip install birinchishop`. Ключ создаётся в панели (Dashboard → API) и хранится только на сервере (`BSK_KEY`).
`create_order` создаёт **реальный заказ**: колл-центр звонит клиенту. Не отправляйте тестовые или фейковые заказы — аккаунт блокируется.

Registration is free: https://birinchishop.uz/register?utm_source=pypi&utm_campaign=sdk-python ·
Support: https://t.me/birinchishop_support

## License

MIT. Using the API itself is subject to the partner agreement on birinchishop.uz.
