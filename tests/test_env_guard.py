"""The environment guard: the reason this project exists.

Behaviour under test traces to:
  "API tokens are environment-specific: Tokens created in production won't work in the
   sandbox and vice versa. Generate a unique API token in each environment."
  https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters
"""

from __future__ import annotations

import pytest

from gcapi.env import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    Decision,
    Environment,
    EnvironmentMismatch,
    classify_base_url,
    fingerprint,
    inspect_jwt_claims,
    preflight,
)

from conftest import PRODUCTION, PROD_TOKEN, SANDBOX, SANDBOX_TOKEN


# -- base URL classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (PRODUCTION_BASE_URL, Environment.PRODUCTION),
        (SANDBOX_BASE_URL, Environment.SANDBOX),
        ("https://www.givecampus.com/api/", Environment.PRODUCTION),
        ("https://givecampus.com/api", Environment.PRODUCTION),
        ("https://sandbox.givecampus.com/api/v2", Environment.SANDBOX),
        ("https://giving.someschool.edu/api", Environment.UNKNOWN),
    ],
)
def test_classify_documented_hosts(url, expected):
    assert classify_base_url(url) is expected


def test_documented_base_urls_are_the_published_ones():
    # Verbatim from the support article's sandbox section.
    assert PRODUCTION_BASE_URL == "https://www.givecampus.com/api"
    assert SANDBOX_BASE_URL == "https://sandbox.givecampus.com/api"


# -- the headline refusals -----------------------------------------------------------


def test_sandbox_token_pointed_at_production_is_refused(store):
    store.bind(SANDBOX_TOKEN, Environment.SANDBOX, label="nightly test sync")
    verdict = preflight(SANDBOX_TOKEN, PRODUCTION, store=store)
    assert verdict.decision is Decision.REFUSE
    assert verdict.token_environment is Environment.SANDBOX
    assert verdict.url_environment is Environment.PRODUCTION
    assert "sandbox token to a production base URL" in verdict.reason


def test_production_token_pointed_at_sandbox_is_refused(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION, label="CRM export")
    verdict = preflight(PROD_TOKEN, SANDBOX, store=store)
    assert verdict.decision is Decision.REFUSE
    assert "production token to a sandbox base URL" in verdict.reason


def test_matching_pair_is_allowed(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    verdict = preflight(PROD_TOKEN, PRODUCTION, store=store)
    assert verdict.allowed
    assert verdict.signals["source"] == "token-store binding"


def test_declared_environment_works_without_a_binding(store):
    verdict = preflight(
        SANDBOX_TOKEN, SANDBOX, declared_environment=Environment.SANDBOX, store=store
    )
    assert verdict.allowed
    assert verdict.signals["source"] == "declared"


def test_declaration_contradicting_a_binding_is_refused(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    verdict = preflight(
        PROD_TOKEN, SANDBOX, declared_environment=Environment.SANDBOX, store=store
    )
    assert verdict.decision is Decision.REFUSE
    assert "contradicts the stored binding" in verdict.reason


# -- fail-closed behaviour -----------------------------------------------------------


def test_unbound_token_is_refused_by_default(store):
    verdict = preflight(PROD_TOKEN, PRODUCTION, store=store)
    assert verdict.decision is Decision.REFUSE
    assert "has no recorded environment" in verdict.reason


def test_unknown_host_is_refused_not_assumed_production(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    verdict = preflight(PROD_TOKEN, "https://giving.someschool.edu/api", store=store)
    assert verdict.decision is Decision.REFUSE
    assert verdict.url_environment is Environment.UNKNOWN


def test_registered_custom_host_is_honoured(store):
    store.register_host("giving.someschool.edu", Environment.PRODUCTION)
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    verdict = preflight(PROD_TOKEN, "https://giving.someschool.edu/api", store=store)
    assert verdict.allowed


def test_escape_hatch_allows_but_says_so(store):
    verdict = preflight(PROD_TOKEN, PRODUCTION, store=store, allow_unverified=True)
    assert verdict.allowed
    assert "allow-unverified-environment" in verdict.reason


def test_raise_for_decision(store):
    store.bind(SANDBOX_TOKEN, Environment.SANDBOX)
    with pytest.raises(EnvironmentMismatch):
        preflight(SANDBOX_TOKEN, PRODUCTION, store=store).raise_for_decision()


# -- the store never holds the secret ------------------------------------------------


def test_store_persists_only_the_hash(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION, label="CRM export")
    raw = store.path.read_text()
    assert PROD_TOKEN not in raw
    assert fingerprint(PROD_TOKEN) in raw
    assert "CRM export" in raw


def test_store_file_is_owner_only(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    assert (store.path.stat().st_mode & 0o777) == 0o600


def test_unbind_removes_the_binding(store):
    store.bind(PROD_TOKEN, Environment.PRODUCTION)
    assert store.lookup(PROD_TOKEN) is Environment.PRODUCTION
    assert store.unbind(PROD_TOKEN) is True
    assert store.lookup(PROD_TOKEN) is Environment.UNKNOWN


def test_fingerprint_is_sha256_and_whitespace_insensitive():
    assert fingerprint(PROD_TOKEN) == fingerprint(f"  {PROD_TOKEN}\n")
    assert len(fingerprint(PROD_TOKEN)) == 64


# -- JWT inspection is advisory only -------------------------------------------------


def test_opaque_token_is_not_a_jwt():
    assert inspect_jwt_claims(PROD_TOKEN) is None


def test_jwt_claims_are_reported_but_never_decide(store):
    # eyJhbGciOiJub25lIn0 . {"env":"sandbox"} . sig
    jwt = "eyJhbGciOiJub25lIn0.eyJlbnYiOiJzYW5kYm94In0.sig"
    assert inspect_jwt_claims(jwt) == {"env": "sandbox"}
    verdict = preflight(jwt, PRODUCTION, store=store)
    # The claim says sandbox and the URL says production, but with no binding and no
    # declaration the verdict is REFUSE for the unbound reason, not the mismatch reason.
    assert verdict.decision is Decision.REFUSE
    assert verdict.signals["token_is_jwt"] is True
    assert verdict.signals["jwt_environment_hint"] == "sandbox"
    assert "has no recorded environment" in verdict.reason
