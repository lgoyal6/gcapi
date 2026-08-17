"""gcapi - an unofficial, documentation-only typed client for the GiveCampus API.

Built entirely from GiveCampus' published Swagger 2.0 specs and support articles.
No request was ever made to a GiveCampus API endpoint, production or sandbox, during
development or testing. Tests run against fixtures taken from their own published
example payloads via httpx.MockTransport.
"""

from .env import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    Decision,
    Environment,
    EnvironmentMismatch,
    TokenStore,
    Verdict,
    classify_base_url,
    fingerprint,
    preflight,
)
from .errors import (
    ApiError,
    BadRequestError,
    BulkLimitExceeded,
    EmptyResultGuard,
    GiveCampusError,
    NotFoundError,
    PollTimeout,
    PreflightRefused,
    RateLimitedError,
    ResultError,
    ServerError,
    UnauthorizedError,
)
from .models import (
    ApiErrorBody,
    ApiRequest,
    ApiResults,
    DonationType,
    Gift,
    GiftState,
    RequestStatus,
    TimeField,
)
from .operations import all_operations, find_operation, specs

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # environment guard
    "Environment",
    "Decision",
    "Verdict",
    "TokenStore",
    "preflight",
    "classify_base_url",
    "fingerprint",
    "EnvironmentMismatch",
    "PRODUCTION_BASE_URL",
    "SANDBOX_BASE_URL",
    # errors
    "GiveCampusError",
    "PreflightRefused",
    "ApiError",
    "BadRequestError",
    "UnauthorizedError",
    "NotFoundError",
    "ServerError",
    "RateLimitedError",
    "ResultError",
    "PollTimeout",
    "BulkLimitExceeded",
    "EmptyResultGuard",
    # models
    "Gift",
    "GiftState",
    "TimeField",
    "DonationType",
    "ApiRequest",
    "ApiResults",
    "ApiErrorBody",
    "RequestStatus",
    # registry
    "all_operations",
    "find_operation",
    "specs",
]


def __getattr__(name: str):  # pragma: no cover - lazy so httpx stays optional for CLI info cmds
    if name in ("GiveCampusClient", "RetryPolicy", "MAX_BULK_RECORDS"):
        from . import client as _client

        return getattr(_client, name)
    raise AttributeError(name)
