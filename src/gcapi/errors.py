"""Typed exceptions for the documented GiveCampus error responses.

Status codes appearing across the 16 published specs: 200, 202, 400, 401, 404, 500.
Documented error bodies (v2.0.0/types/*.yaml):
    bad_request_error       {"message": str}
    unauthorized_error      {"message": str}
    not_found_error         {"message": str}
    internal_server_error   {"message": str, "status": "error"}

429 is NOT documented anywhere in their specs. It is handled defensively as retryable
because ignoring a 429 would be worse than honouring one that never arrives, but it is
labelled undocumented wherever it appears.
"""

from __future__ import annotations

__all__ = [
    "GiveCampusError",
    "PreflightRefused",
    "ApiError",
    "BadRequestError",
    "UnauthorizedError",
    "NotFoundError",
    "ServerError",
    "RateLimitedError",
    "UnexpectedStatusError",
    "ResultError",
    "PollTimeout",
    "BulkLimitExceeded",
    "EmptyResultGuard",
    "error_for_status",
    "RETRYABLE_STATUS_CODES",
    "DOCUMENTED_STATUS_CODES",
]

DOCUMENTED_STATUS_CODES = frozenset({200, 202, 400, 401, 404, 500})

# Only 500 is both documented and retryable. 429/502/503/504 are undocumented but are
# retried defensively; 400/401/404 are never retried because a retry cannot change them.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class GiveCampusError(Exception):
    """Base for everything this client raises."""


class PreflightRefused(GiveCampusError):
    """The token/base-URL preflight refused before any request was constructed."""


class ApiError(GiveCampusError):
    status_code: int = 0

    def __init__(self, message: str, *, status_code: int | None = None, body: object = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.body = body

    def __str__(self) -> str:
        return f"HTTP {self.status_code}: {self.message}"


class BadRequestError(ApiError):
    status_code = 400


class UnauthorizedError(ApiError):
    """HTTP 401. On this API the most common cause is a token from the other environment."""

    status_code = 401


class NotFoundError(ApiError):
    status_code = 404


class ServerError(ApiError):
    status_code = 500


class RateLimitedError(ApiError):
    """HTTP 429. Not documented by GiveCampus; handled defensively."""

    status_code = 429


class UnexpectedStatusError(ApiError):
    """A status code that does not appear in any published spec."""


class ResultError(GiveCampusError):
    """The async job finished with status == "error"."""


class PollTimeout(GiveCampusError):
    """The async job did not reach a terminal status within the allotted budget."""


class BulkLimitExceeded(GiveCampusError):
    """More than 5,000 records in one write request (documented 400 from the server)."""


class EmptyResultGuard(GiveCampusError):
    """A /gifts query that their own docs say returns a silent empty array."""


_BY_STATUS: dict[int, type[ApiError]] = {
    400: BadRequestError,
    401: UnauthorizedError,
    404: NotFoundError,
    429: RateLimitedError,
    500: ServerError,
}


def error_for_status(status_code: int, body: object) -> ApiError:
    """Build the typed exception for a status code, pulling `message` out of the body."""
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")
    if not message:
        message = f"no message in response body ({type(body).__name__})"
    cls = _BY_STATUS.get(status_code)
    if cls is None:
        return UnexpectedStatusError(message, status_code=status_code, body=body)
    return cls(message, status_code=status_code, body=body)
