# Changelog

All notable changes to this package are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package uses [Semantic Versioning](https://semver.org/).
While the version is `0.x`, minor releases may contain small breaking changes; they will always be listed here.

## [0.2.0] - 2026-11-20

Feature parity with the official PHP SDK. API version `2026-10-23`. Code written for 0.1.x keeps working.

### Added

- Test mode (sandbox) support: `is_test_mode()` for `bsk_test_` keys and `set_test_order_status(order_id, status)`
  (`POST /sandbox/orders/{order_id}/status`). With a live key it raises `PermissionDeniedError` (`test_mode_only`)
  without sending a request.
- Constants `TEST_KEY_PREFIX`, `LIVE_KEY_PREFIX`, `TEST_ID_BASE`, `TEST_ORDER_STATUSES` and `TEST_HEADER` (`X-Birinchishop-Test`).
- Postback macros `payout_earned` (payout on `sale`, `0` otherwise) and `test` (`1` for test events) in `POSTBACK_MACROS`.
- Error subclasses grouped by HTTP status, all extending `BirinchishopError`: `InvalidRequestError`, `AuthenticationError`,
  `PermissionDeniedError`, `NotFoundError`, `ConflictError`, `RateLimitError`, `ServerError`, `NetworkError`;
  `error_class_for(status)`.
- `paginate(method, **filters)` (`"list_offers"` | `"list_links"` | `"list_orders"`, camelCase names work too).
- `sleeper=` constructor option (wait function between retries, e.g. for tests).
- `Retry-After` given as an HTTP date is honoured (capped at 60 s, as before).
- README: test mode, postbacks, error classes, retries and development sections; this changelog.

### Changed

- `create_order` checks `link_id`, `phone` and the `Idempotency-Key` format (`A-Z a-z 0-9 . _ : -`, 1-128 characters)
  before sending and raises `InvalidRequestError` (`invalid_request`) instead of making a request the API would refuse.
- Path ids accept integers (and integral floats) or ASCII digit strings up to 2147483647; other values raise
  `InvalidRequestError` (`invalid_request`, as before) without a request.
- A `datetime` passed as `date_from` / `date_to` is sent as its Asia/Tashkent date `YYYY-MM-DD`
  (was a full ISO timestamp, which the API rejects). `date` values are unchanged.
- `construct_event` raises `ValueError` when a correctly signed body is valid JSON but not an object.
- The API key is stripped; a blank key is `missing_api_key`.

## [0.1.0] - 2026-11-19

First public (beta) release: every REST endpoint of the Partner API v1, retries with an `Idempotency-Key` on
`create_order`, pagination iterators, `BirinchishopError`, webhook signature verification and `fill_tracking_url`.

[0.2.0]: https://github.com/Birinchishop/birinchishop-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/Birinchishop/birinchishop-python/releases/tag/v0.1.0
