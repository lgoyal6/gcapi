"""Client behaviour against httpx.MockTransport. No socket is ever opened.

Response shapes come from the published specs:
  submit  -> shared/types/api_request.yaml   {request_id, status}
  poll    -> shared/types/api_results.yaml   {status, download_url}
  errors  -> v2.0.0/types/*.yaml             {message} (+ {status} on 500)
"""

from __future__ import annotations

import httpx
import pytest

from gcapi.client import GiveCampusClient, MAX_BULK_RECORDS, RetryPolicy
from gcapi.env import Environment
from gcapi.errors import (
    BadRequestError,
    BulkLimitExceeded,
    EmptyResultGuard,
    NotFoundError,
    PollTimeout,
    PreflightRefused,
    ResultError,
    ServerError,
    UnauthorizedError,
)
from gcapi.models import GiftState, TimeField

from conftest import PRODUCTION, PROD_TOKEN, SANDBOX, SANDBOX_TOKEN, load_fixture

DOWNLOAD_URL = "https://givecampus-api-results.example/results/req-1.json"


def make_client(handler, store, *, token=PROD_TOKEN, base_url=PRODUCTION, **kw):
    store.bind(token, Environment.PRODUCTION if base_url == PRODUCTION else Environment.SANDBOX)
    return GiveCampusClient(
        token,
        base_url,
        store=store,
        transport=httpx.MockTransport(handler),
        retry=kw.pop("retry", RetryPolicy(max_attempts=3, base_delay=0, sleep=lambda s: None)),
        **kw,
    )


def gifts_handler(payload, *, polls_in_progress=0):
    """Submit -> 200 {request_id}; poll -> N x 202, then 200 with download_url."""
    state = {"polls": 0, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if str(request.url).startswith(DOWNLOAD_URL):
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/gifts"):
            return httpx.Response(200, json={"request_id": "req-1", "status": "in_progress"})
        if "/results/" in request.url.path:
            if state["polls"] < polls_in_progress:
                state["polls"] += 1
                return httpx.Response(202, json={"status": "in_progress"})
            return httpx.Response(200, json={"status": "completed", "download_url": DOWNLOAD_URL})
        return httpx.Response(404, json={"message": "Not Found"})

    handler.state = state  # type: ignore[attr-defined]
    return handler


# -- the guard is structural ---------------------------------------------------------


def test_client_cannot_be_built_with_a_mismatched_token(store):
    store.bind(SANDBOX_TOKEN, Environment.SANDBOX)
    with pytest.raises(PreflightRefused) as exc:
        GiveCampusClient(SANDBOX_TOKEN, PRODUCTION, store=store,
                         transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert "sandbox token to a production base URL" in str(exc.value)


def test_refusal_happens_before_any_request_is_sent(store):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    with pytest.raises(PreflightRefused):
        GiveCampusClient(PROD_TOKEN, SANDBOX, store=store,
                         transport=httpx.MockTransport(handler))
    assert seen == []


def test_client_exposes_its_verdict(store):
    handler = gifts_handler(load_fixture("gifts_authorized_with_designations.json"))
    with make_client(handler, store) as client:
        assert client.verdict.allowed
        assert client.verdict.url_environment is Environment.PRODUCTION


# -- lifecycle -----------------------------------------------------------------------


def test_gifts_round_trip_submit_poll_download(store):
    handler = gifts_handler(load_fixture("gifts_authorized_with_designations.json"))
    with make_client(handler, store) as client:
        gifts = client.gifts(start=1779300000, end=1779400000, states=[GiftState.AUTHORIZED])

    assert len(gifts) == 1
    gift = gifts[0]
    assert gift.id == 30059721
    assert gift.state == "authorized"
    assert gift.value_usd == "10.0"
    assert gift.designations[0].name == "Human Being Fund"
    assert gift.timestamps.datetime_of_pledge == 1779310480

    paths = [r.url.path for r in handler.state["requests"]]
    assert paths[0].endswith("/gifts")
    assert "/results/req-1" in paths[1]


def test_bearer_header_is_sent(store):
    handler = gifts_handler(load_fixture("gifts_authorized_with_designations.json"))
    with make_client(handler, store) as client:
        client.gifts(start=1, end=2, states=[GiftState.PAID])
    assert handler.state["requests"][0].headers["Authorization"] == f"Bearer {PROD_TOKEN}"


def test_state_flags_and_time_field_become_query_params(store):
    handler = gifts_handler([])
    with make_client(handler, store) as client:
        client.gifts(
            start=100, end=200,
            states=[GiftState.PAID, GiftState.REFUNDED],
            time_field=TimeField.UPDATED_AT,
        )
    q = handler.state["requests"][0].url.params
    assert q["start"] == "100" and q["end"] == "200"
    assert q["paid"] == "true" and q["refunded"] == "true"
    assert q["time_field"] == "updated_at"


def test_polling_waits_for_completion(store, instant_sleep):
    handler = gifts_handler(load_fixture("gifts_recurring_installment.json"), polls_in_progress=3)
    with make_client(handler, store) as client:
        request = client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
        results = client.wait_for_results(request.request_id, sleep=instant_sleep)
    assert results.download_url == DOWNLOAD_URL
    assert instant_sleep.calls == [2.0, 2.0, 2.0]


def test_poll_interval_outside_the_documented_2_to_5_seconds_is_rejected(store):
    handler = gifts_handler([])
    with make_client(handler, store) as client:
        with pytest.raises(ValueError, match="2-5 seconds"):
            client.wait_for_results("req-1", poll_seconds=0.1)


def test_poll_timeout(store, instant_sleep):
    handler = gifts_handler([], polls_in_progress=10_000)
    with make_client(handler, store) as client:
        with pytest.raises(PollTimeout):
            client.wait_for_results("req-1", max_wait_seconds=6, sleep=instant_sleep)


def test_status_error_raises(store):
    def handler(request):
        if "/results/" in request.url.path:
            return httpx.Response(200, json={"status": "error"})
        return httpx.Response(200, json={"request_id": "req-1", "status": "in_progress"})

    with make_client(handler, store) as client:
        with pytest.raises(ResultError):
            client.run("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})


def test_non_https_download_url_is_refused(store):
    with make_client(gifts_handler([]), store) as client:
        with pytest.raises(ValueError, match="non-HTTPS"):
            client.download("http://insecure.example/results.json")


# -- the documented silent-empty-array footgun ---------------------------------------


def test_gifts_without_a_state_flag_is_refused_locally(store):
    """Their docs: a start/end query with no state flag returns 200/completed/[]."""
    with make_client(gifts_handler([]), store) as client:
        with pytest.raises(EmptyResultGuard) as exc:
            client.gifts(start=1, end=2)
    assert "empty array" in str(exc.value)


def test_ids_query_bypasses_the_state_requirement(store):
    handler = gifts_handler(load_fixture("gifts_authorized_with_designations.json"))
    with make_client(handler, store) as client:
        client.gifts(start=1, end=2, ids=[30059721])
    q = handler.state["requests"][0].url.params
    assert q["ids"] == "30059721"
    assert "paid" not in q


# -- typed errors --------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,exc",
    [(400, BadRequestError), (401, UnauthorizedError), (404, NotFoundError)],
)
def test_documented_errors_map_to_typed_exceptions(store, status, exc):
    def handler(request):
        return httpx.Response(status, json={"message": "Example error message"})

    with make_client(handler, store) as client:
        with pytest.raises(exc) as caught:
            client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert caught.value.message == "Example error message"
    assert caught.value.status_code == status


def test_500_carries_the_documented_status_field(store):
    def handler(request):
        return httpx.Response(500, json={"message": "boom", "status": "error"})

    with make_client(handler, store) as client:
        with pytest.raises(ServerError) as caught:
            client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert caught.value.body["status"] == "error"


# -- retries -------------------------------------------------------------------------


def test_500_is_retried(store):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500, json={"message": "boom", "status": "error"})
        return httpx.Response(200, json={"request_id": "req-1", "status": "in_progress"})

    with make_client(handler, store) as client:
        request = client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert attempts["n"] == 3
    assert request.request_id == "req-1"


@pytest.mark.parametrize("status", [400, 401, 404])
def test_client_errors_are_never_retried(store, status):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(status, json={"message": "nope"})

    with make_client(handler, store) as client:
        with pytest.raises(Exception):
            client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert attempts["n"] == 1


def test_retry_after_header_is_honoured(store):
    slept: list[float] = []
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"request_id": "req-1", "status": "in_progress"})

    retry = RetryPolicy(max_attempts=3, base_delay=0.5, sleep=slept.append)
    with make_client(handler, store, retry=retry) as client:
        client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert slept == [7.0]


def test_retries_are_bounded(store):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(500, json={"message": "boom", "status": "error"})

    retry = RetryPolicy(max_attempts=4, base_delay=0, sleep=lambda s: None)
    with make_client(handler, store, retry=retry) as client:
        with pytest.raises(ServerError):
            client.submit("GET", "/gifts", params={"start": 1, "end": 2, "paid": "true"})
    assert attempts["n"] == 4


def test_backoff_is_jittered_and_capped():
    retry = RetryPolicy(max_attempts=8, base_delay=1.0, max_delay=10.0)
    for attempt in range(1, 9):
        delay = retry.delay_for(attempt)
        assert 0.0 <= delay <= 10.0


# -- windowing (the documented substitute for pagination) ----------------------------


def test_incremental_windows_are_contiguous_and_non_overlapping(store):
    handler = gifts_handler([])
    with make_client(handler, store) as client:
        list(
            client.iter_gifts_incremental(
                since=1000, until=1299, states=[GiftState.PAID], window_seconds=100
            )
        )
    windows = [
        (int(r.url.params["start"]), int(r.url.params["end"]))
        for r in handler.state["requests"]
        if r.url.path.endswith("/gifts")
    ]
    assert windows == [(1000, 1099), (1100, 1199), (1200, 1299)]
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start == prev_end + 1


# -- bulk cap ------------------------------------------------------------------------


def test_bulk_write_refuses_over_5000_records(store):
    with make_client(gifts_handler([]), store) as client:
        with pytest.raises(BulkLimitExceeded) as exc:
            client.bulk_write("POST", "/constituents", [{"i": i} for i in range(MAX_BULK_RECORDS + 1)])
    assert "5000" in str(exc.value)


def test_bulk_write_at_the_cap_is_allowed(store):
    def handler(request):
        if str(request.url).startswith(DOWNLOAD_URL):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/constituents"):
            return httpx.Response(200, json={"request_id": "req-1", "status": "in_progress"})
        return httpx.Response(200, json={"status": "completed", "download_url": DOWNLOAD_URL})

    with make_client(handler, store) as client:
        assert client.bulk_write("POST", "/constituents", [{"i": i} for i in range(MAX_BULK_RECORDS)]) == []
