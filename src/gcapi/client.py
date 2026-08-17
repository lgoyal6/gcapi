"""Typed synchronous client for the documented GiveCampus API.

Shape of the API (identical for all 55 published operations):

    1. Submit  -> {"request_id": "...", "status": "in_progress"}   (200 or 202)
    2. Poll    -> GET /results/{request_id} every 2-5 seconds       (202 while running)
    3. Download-> {"status": "completed", "download_url": "..."}    (200), then GET that
                  URL for a JSON array of records.

    https://www.givecampus.com/documentation/api/v1.0.0/gifts.yaml (Request Lifecycle)

THERE IS NO PAGINATION. No page, per_page, limit, offset, or cursor parameter appears
in any of the 16 published specs; a completed request yields one whole JSON array at
`download_url`. The documented way to bound a large pull is an incremental,
non-overlapping time window, which is implemented as `iter_gifts_incremental()`:

    "Store the most recent timestamp you successfully processed ... request a new batch
     with start = last_processed_at + 1 and end = now ... This strategy prevents
     overlapping time windows and avoids retrieving duplicate gifts."
    -- https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters

The client cannot be constructed without a passing environment preflight. That is
deliberate: the check happens before an HTTP connection exists, not before each call.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Iterator, Sequence

import httpx

from .env import Environment, TokenStore, Verdict, preflight
from .errors import (
    BulkLimitExceeded,
    EmptyResultGuard,
    PollTimeout,
    PreflightRefused,
    RETRYABLE_STATUS_CODES,
    ResultError,
    error_for_status,
)
from .models import ApiRequest, ApiResults, Gift, GiftState, RequestStatus, TimeField
from .operations import find_operation

__all__ = ["GiveCampusClient", "RetryPolicy", "MAX_BULK_RECORDS"]

# "A bulk write request (POST or PUT) is limited to 5,000 records. If a request exceeds
#  this limit, it will return a 400 Bad Request response."
# https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters
MAX_BULK_RECORDS = 5000

# "Call /results/{request_id} every 2-5 seconds until status changes to completed"
MIN_POLL_SECONDS = 2.0
MAX_POLL_SECONDS = 5.0


class RetryPolicy:
    """Exponential backoff with full jitter, on retryable statuses and transport errors only.

    Retryable statuses come from errors.RETRYABLE_STATUS_CODES: {429, 500, 502, 503, 504}.
    Of those, only 500 appears in a GiveCampus spec; the rest are handled defensively.
    400, 401 and 404 are never retried because a retry cannot change the outcome.
    """

    def __init__(
        self,
        max_attempts: int = 4,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        ceiling = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return random.uniform(0.0, ceiling)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class GiveCampusClient:
    """Construct with a token and a base URL. Refuses to exist on an environment mismatch."""

    def __init__(
        self,
        token: str,
        base_url: str,
        *,
        environment: Environment | None = None,
        store: TokenStore | None = None,
        allow_unverified_environment: bool = False,
        retry: RetryPolicy | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = "gcapi-unofficial/0.1 (+docs-only client)",
    ) -> None:
        if not token or not token.strip():
            raise ValueError("token is required")

        # THE GUARD. Runs before any socket, any httpx.Client, any request.
        self.verdict: Verdict = preflight(
            token,
            base_url,
            declared_environment=environment,
            store=store,
            allow_unverified=allow_unverified_environment,
        )
        if not self.verdict.allowed:
            raise PreflightRefused(self.verdict.reason)

        self._token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.retry = retry or RetryPolicy()
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
            follow_redirects=False,
        )

    # -- lifecycle -----------------------------------------------------------------

    def __enter__(self) -> "GiveCampusClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- transport -----------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == self.retry.max_attempts:
                    raise
                self.retry.sleep(self.retry.delay_for(attempt))
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.retry.max_attempts:
                self.retry.sleep(self.retry.delay_for(attempt, _retry_after_seconds(response)))
                continue
            return response

        raise last_exc if last_exc else RuntimeError("unreachable")

    @staticmethod
    def _json_or_raise(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code >= 400:
            raise error_for_status(response.status_code, body)
        return body

    # -- the three documented lifecycle steps ---------------------------------------

    def submit(self, method: str, path: str, **kwargs: Any) -> ApiRequest:
        """Step 1. Returns {request_id, status} for any documented operation."""
        return ApiRequest.model_validate(self._json_or_raise(self._request(method, path, **kwargs)))

    def poll_once(self, request_id: str, results_path: str = "/results") -> ApiResults:
        """Step 2, single shot. HTTP 202 means still in progress."""
        response = self._request("GET", f"{results_path}/{request_id}")
        body = self._json_or_raise(response)
        results = ApiResults.model_validate(body)
        if response.status_code == 202 and results.status is None:
            results.status = RequestStatus.IN_PROGRESS
        return results

    def wait_for_results(
        self,
        request_id: str,
        *,
        results_path: str = "/results",
        poll_seconds: float = MIN_POLL_SECONDS,
        max_wait_seconds: float = 600.0,
        sleep: Callable[[float], None] | None = None,
    ) -> ApiResults:
        """Step 2, looped, at the documented 2-5 second cadence."""
        if not MIN_POLL_SECONDS <= poll_seconds <= MAX_POLL_SECONDS:
            raise ValueError(
                f"poll_seconds must be between {MIN_POLL_SECONDS} and {MAX_POLL_SECONDS}; "
                "GiveCampus documents polling every 2-5 seconds"
            )
        napper = sleep or self.retry.sleep
        waited = 0.0
        while True:
            results = self.poll_once(request_id, results_path=results_path)
            if results.status is RequestStatus.COMPLETED:
                return results
            if results.status is RequestStatus.ERROR:
                raise ResultError(f"request {request_id} finished with status=error")
            if waited >= max_wait_seconds:
                raise PollTimeout(
                    f"request {request_id} still {results.status} after {waited:.0f}s"
                )
            napper(poll_seconds)
            waited += poll_seconds

    def download(self, download_url: str) -> Any:
        """Step 3. Fetch the JSON array a completed request points at."""
        if not download_url.lower().startswith("https://"):
            raise ValueError(f"refusing to fetch a non-HTTPS download_url: {download_url!r}")
        response = self._http.get(download_url)
        return self._json_or_raise(response)

    def run(self, method: str, path: str, *, results_path: str = "/results", **kwargs: Any) -> Any:
        """All three steps. Works for any of the 55 documented operations."""
        request = self.submit(method, path, **kwargs)
        if not request.request_id:
            raise ResultError(f"{method} {path} returned no request_id: {request!r}")
        results = self.wait_for_results(request.request_id, results_path=results_path)
        if not results.download_url:
            raise ResultError(f"request {request.request_id} completed without a download_url")
        return self.download(results.download_url)

    # -- typed convenience ----------------------------------------------------------

    def gifts(
        self,
        *,
        start: int,
        end: int,
        states: Sequence[GiftState | str] | None = None,
        time_field: TimeField | str | None = None,
        ids: Sequence[int | str] | None = None,
        constituent_ids: Sequence[str] | None = None,
        deposit_ids: Sequence[str] | None = None,
    ) -> list[Gift]:
        """GET /gifts, polled to completion, parsed into typed Gift records.

        Guards the documented silent-empty-array footgun: a start/end query with no
        state flag returns HTTP 200, status completed, and `[]`.

            "Every Gifts request using a start/end range must include at least one
             state parameter."
            -- https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters
        """
        params: dict[str, Any] = {"start": int(start), "end": int(end)}

        if ids:
            # "If passed, all other filters are ignored." (gifts.yaml, ids parameter)
            params["ids"] = ",".join(str(i) for i in ids)
        else:
            if not states:
                raise EmptyResultGuard(
                    "A GET /gifts query with start/end and no state flag returns HTTP 200 with "
                    "status=completed and an empty array. Pass states=[...] (or all eight of "
                    "GiftState to mean 'any state'), or pass ids=[...] to bypass filtering."
                )
            for state in states:
                params[GiftState(state).value if not isinstance(state, GiftState) else state.value] = "true"
            if time_field is not None:
                params["time_field"] = (
                    time_field.value if isinstance(time_field, TimeField) else TimeField(time_field).value
                )
            if constituent_ids:
                params["constituent_ids"] = ",".join(str(i) for i in constituent_ids)
            if deposit_ids:
                params["deposit_ids"] = ",".join(str(i) for i in deposit_ids)

        payload = self.run("GET", "/gifts", params=params)
        if not isinstance(payload, list):
            raise ResultError(f"expected a JSON array of gifts, got {type(payload).__name__}")
        return [Gift.model_validate(item) for item in payload]

    def iter_gifts_incremental(
        self,
        *,
        since: int,
        until: int,
        states: Sequence[GiftState | str],
        time_field: TimeField | str = TimeField.UPDATED_AT,
        window_seconds: int = 86_400,
    ) -> Iterator[list[Gift]]:
        """Walk a long range in contiguous, non-overlapping windows.

        This is the documented substitute for pagination. Windows are half-open by
        construction (`start = previous_end + 1`), which is exactly the pattern their
        deduplication guidance prescribes.
        """
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        cursor = int(since)
        until = int(until)
        while cursor <= until:
            window_end = min(cursor + window_seconds - 1, until)
            yield self.gifts(start=cursor, end=window_end, states=states, time_field=time_field)
            cursor = window_end + 1

    def bulk_write(self, method: str, path: str, records: Sequence[Any], **kwargs: Any) -> Any:
        """POST/PUT a bulk body, refusing over the documented 5,000-record cap."""
        if method.upper() not in ("POST", "PUT", "DELETE"):
            raise ValueError("bulk_write expects POST, PUT or DELETE")
        if len(records) > MAX_BULK_RECORDS:
            raise BulkLimitExceeded(
                f"{len(records)} records exceeds the documented {MAX_BULK_RECORDS}-record cap for a "
                f"single bulk write; split into sequential requests of {MAX_BULK_RECORDS} or fewer."
            )
        return self.run(method.upper(), path, json=list(records), **kwargs)

    # -- introspection --------------------------------------------------------------

    def operation(self, operation_id: str) -> dict[str, Any]:
        """Look up a documented operation by operationId, with its source spec URL."""
        return find_operation(operation_id)
