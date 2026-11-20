"""Exceptions."""

from __future__ import annotations

from typing import Any, List, Optional, Type


class BirinchishopError(Exception):
    """API error (``{"error": {"code", "message", "details"}, "request_id"}``) or a client-side failure.

    Branch on :attr:`code` (stable), never on the message.
    ``status`` is the HTTP status (0 for network errors / timeouts).
    Client-side codes: ``network_error``, ``timeout``, ``missing_api_key``, ``invalid_request``, ``test_mode_only``,
    ``invalid_response``, ``http_<status>``.

    Subclasses group errors by HTTP status (see :func:`error_class_for`); catching this class catches all of them.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "unknown_error",
        details: Optional[List[Any]] = None,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details
        self.request_id = request_id
        self.retry_after = retry_after
        self.body = body

    def __str__(self) -> str:
        rid = f" (request_id={self.request_id})" if self.request_id else ""
        return f"[{self.status} {self.code}] {self.message}{rid}"


class InvalidRequestError(BirinchishopError):
    """400 / 413 / 415 / 422, or a client-side check (``invalid_request``)."""


class AuthenticationError(BirinchishopError):
    """401 (``unauthorized``, ``key_revoked``, ``key_expired``), or no key (``missing_api_key``)."""


class PermissionDeniedError(BirinchishopError):
    """403 (``insufficient_scope``, ``orders_write_not_allowed``, ``not_available_in_test_mode``, ``test_mode_only``, ...)."""


class NotFoundError(BirinchishopError):
    """404 (``offer_not_found``, ``order_not_found``, ...)."""


class ConflictError(BirinchishopError):
    """409 (``duplicate_order``, ``link_limit_reached``, ...)."""


class RateLimitError(BirinchishopError):
    """429 (``rate_limited`` is retried automatically; ``daily_order_limit`` is not)."""


class ServerError(BirinchishopError):
    """5xx (``internal_error``, ``offer_temporarily_unavailable``, ``http_502``, ...)."""


class NetworkError(BirinchishopError):
    """No HTTP response: ``network_error`` or ``timeout`` (status 0)."""


def error_class_for(status: int) -> Type[BirinchishopError]:
    """The error class for an HTTP status."""
    if status in (400, 413, 415, 422):
        return InvalidRequestError
    if status == 401:
        return AuthenticationError
    if status == 403:
        return PermissionDeniedError
    if status == 404:
        return NotFoundError
    if status == 409:
        return ConflictError
    if status == 429:
        return RateLimitError
    if status >= 500:
        return ServerError
    return BirinchishopError


class SignatureVerificationError(Exception):
    """Webhook signature is missing, invalid or too old."""
