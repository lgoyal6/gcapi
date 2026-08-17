"""Shared fixtures.

Every test in this suite runs offline. `no_network` is autouse and monkeypatches the
httpx transport layer so that any attempt to open a real socket fails loudly rather
than reaching GiveCampus. See test_no_live_calls.py for the assertion that proves it.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

SANDBOX = "https://sandbox.givecampus.com/api"
PRODUCTION = "https://www.givecampus.com/api"

PROD_TOKEN = "prod-token-aaaaaaaaaaaaaaaaaaaaaaaa"
SANDBOX_TOKEN = "sbx-token-bbbbbbbbbbbbbbbbbbbbbbbb"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class NetworkAccessAttempted(AssertionError):
    """Raised if anything in the test suite tries to open a real connection."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Hard block on real I/O: httpx's default transport is replaced by a raiser."""

    def _boom(self, request):  # noqa: ANN001
        raise NetworkAccessAttempted(
            f"test suite attempted a real request to {request.url}. "
            "This client is developed against published documentation only."
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _boom, raising=True)
    yield


@pytest.fixture
def store(tmp_path):
    from gcapi.env import TokenStore

    return TokenStore(tmp_path / "bindings.json")


@pytest.fixture
def instant_sleep():
    """A sleep that records durations instead of spending them."""
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    _sleep.calls = calls  # type: ignore[attr-defined]
    return _sleep


def mock_transport(handler):
    return httpx.MockTransport(handler)
