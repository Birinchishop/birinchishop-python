"""Exceptions."""

from __future__ import annotations

from typing import Any, List, Optional


class BirinchishopError(Exception):
    """API error (``{"error": {"code", "message", "details"}, "request_id"}``) or a client-side failure.

    Branch on :attr:`code` (stable), never on the message.
    ``status`` is the HTTP status (0 for network errors / timeouts).
    Client-side codes: ``network_error``, ``timeout``, ``missing_api_key``, ``invalid_request``, ``http_<status>``.
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


class SignatureVerificationError(Exception):
    """Webhook signature is missing, invalid or too old."""
