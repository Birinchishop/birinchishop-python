"""Webhook signature verification and tracking-link helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Any, Dict, Mapping, Optional, Union

from .errors import SignatureVerificationError

SIGNATURE_HEADER = "X-Birinchishop-Signature"
#: Present (value "true") on test events sent for a test key. The event body also has ``"test": true``.
TEST_HEADER = "X-Birinchishop-Test"

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _parse(header: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"t": None, "v1": []}
    if not isinstance(header, str):
        return out
    for part in header.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "t":
            out["t"] = v
        elif k == "v1":
            out["v1"].append(v)
    return out


def verify_signature(
    raw_body: Union[bytes, bytearray, str],
    header: Optional[str],
    secret: str,
    *,
    tolerance: int = 300,
    now: Optional[float] = None,
) -> bool:
    """Verify ``X-Birinchishop-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>``.

    ``raw_body`` must be the exact bytes received (not re-serialized JSON).
    Timestamps further than ``tolerance`` seconds (default 300) from now are rejected.
    """
    if not secret:
        raise ValueError("secret is required")
    if isinstance(raw_body, str):
        body = raw_body.encode("utf-8")
    elif isinstance(raw_body, (bytes, bytearray)):
        body = bytes(raw_body)
    else:
        raise TypeError("raw_body must be the raw request body (bytes or str), not parsed JSON")
    p = _parse(header)
    t = p["t"]
    if not t or not t.isdigit() or not p["v1"]:
        return False
    current = time.time() if now is None else now
    if abs(current - int(t)) > tolerance:
        return False
    expected = hmac.new(secret.encode("utf-8"), t.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return any(_HEX64.match(sig) and hmac.compare_digest(expected, sig.lower()) for sig in p["v1"])


def construct_event(
    raw_body: Union[bytes, bytearray, str],
    header: Optional[str],
    secret: str,
    *,
    tolerance: int = 300,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Verify the signature and return the parsed event ``{id, type, api_version, created_at, data, test?}``.

    Raises :class:`SignatureVerificationError` if the signature is missing, invalid or too old, and ``ValueError``
    if the (correctly signed) body is not a JSON object. Events may arrive twice or out of order: deduplicate by ``id``.
    Test events (sent for a test key) have ``"test": True`` and the header ``X-Birinchishop-Test: true``.
    """
    if not verify_signature(raw_body, header, secret, tolerance=tolerance, now=now):
        raise SignatureVerificationError("Invalid or expired X-Birinchishop-Signature")
    if isinstance(raw_body, (bytes, bytearray)):
        raw_body = bytes(raw_body).decode("utf-8")
    event = json.loads(raw_body)
    if not isinstance(event, dict):
        raise ValueError("Webhook body is not a JSON object")
    return event


def fill_tracking_url(template: str, values: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> str:
    """Fill a link's ``tracking_url_template`` (``click_id``, ``sub1``..``sub5``).

    Values are URL-encoded but ``{`` and ``}`` are kept, so tracker macros such as ``{clickid}`` keep working.
    Placeholders without a value are removed from the URL.
    """
    vals = dict(values or {}, **kwargs)
    base, _, query = template.partition("?")
    parts = []
    for pair in query.split("&"):
        if not pair:
            continue
        m = re.match(r"^([^=]+)=\{([a-z0-9_]+)\}$", pair)
        if not m:
            parts.append(pair)
            continue
        v = vals.get(m.group(2))
        if v is not None and v != "":
            enc = urllib.parse.quote(str(v), safe="{}")
            parts.append(f"{m.group(1)}={enc}")
    return f"{base}?{'&'.join(parts)}" if parts else base
