"""Official Python client for the Birinchi Shop Partner API v1.

Docs: https://birinchishop.uz/api/v1/docs
"""

from ._version import __version__
from .client import API_VERSION, DEFAULT_BASE_URL, Birinchishop, HttpResponse, urllib_transport
from .errors import BirinchishopError, SignatureVerificationError
from .webhooks import SIGNATURE_HEADER, construct_event, fill_tracking_url, verify_signature

ORDER_STATUSES = ("new", "processing", "callback", "confirmed", "packed", "shipping", "delivered", "returned", "cancelled")
CONVERSION_STATUSES = ("lead", "approved", "sale", "rejected")
PAYOUT_STATUSES = ("pending", "earned", "under_review", "none")
EVENT_TYPES = ("order.created", "order.status_changed")
OFFER_SORTS = ("recommended", "newest", "best_selling", "highest_payout", "highest_stock", "highest_lead_bonus")
SCOPES = (
    "account:read", "offers:read", "links:read", "links:write", "orders:read", "orders:write",
    "stats:read", "events:read", "webhooks:manage", "insights:read", "ads:read", "ads:write",
)
POSTBACK_MACROS = (
    "click_id", "sub1", "sub2", "sub3", "sub4", "sub5", "order_id", "external_id", "status",
    "conversion_status", "payout", "customer_price", "currency", "offer_id", "link_id", "event", "event_id", "timestamp",
)

__all__ = [
    "__version__", "API_VERSION", "DEFAULT_BASE_URL", "Birinchishop", "HttpResponse", "urllib_transport",
    "BirinchishopError", "SignatureVerificationError",
    "SIGNATURE_HEADER", "verify_signature", "construct_event", "fill_tracking_url",
    "ORDER_STATUSES", "CONVERSION_STATUSES", "PAYOUT_STATUSES", "EVENT_TYPES", "OFFER_SORTS", "SCOPES", "POSTBACK_MACROS",
]
