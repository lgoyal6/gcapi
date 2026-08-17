"""CLI surface, especially the exit code a CI job would gate on."""

from __future__ import annotations

import json

import pytest

from gcapi.cli import EXIT_MISMATCH, EXIT_OK, main
from gcapi.env import Environment, TokenStore

from conftest import PRODUCTION, PROD_TOKEN, SANDBOX, SANDBOX_TOKEN


def test_preflight_exits_3_on_mismatch(tmp_path, capsys):
    store_path = tmp_path / "b.json"
    TokenStore(store_path).bind(SANDBOX_TOKEN, Environment.SANDBOX)
    code = main([
        "--store", str(store_path), "preflight",
        "--token", SANDBOX_TOKEN, "--base-url", PRODUCTION,
    ])
    assert code == EXIT_MISMATCH
    out = capsys.readouterr().out
    assert "[STOP]" in out
    assert "sandbox token to a production base URL" in out


def test_preflight_exits_0_on_match(tmp_path, capsys):
    store_path = tmp_path / "b.json"
    TokenStore(store_path).bind(PROD_TOKEN, Environment.PRODUCTION)
    code = main([
        "--store", str(store_path), "preflight",
        "--token", PROD_TOKEN, "--base-url", PRODUCTION,
    ])
    assert code == EXIT_OK
    assert "[OK" in capsys.readouterr().out


def test_preflight_json_output(tmp_path, capsys):
    store_path = tmp_path / "b.json"
    TokenStore(store_path).bind(PROD_TOKEN, Environment.PRODUCTION)
    main([
        "--store", str(store_path), "preflight",
        "--token", PROD_TOKEN, "--base-url", SANDBOX, "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "refuse"
    assert payload["url_environment"] == "sandbox"
    assert payload["token_environment"] == "production"
    assert len(payload["token_fingerprint"]) == 64


def test_environment_flag_selects_the_documented_base_url(tmp_path, capsys):
    code = main([
        "--store", str(tmp_path / "b.json"), "preflight",
        "--token", SANDBOX_TOKEN, "--environment", "sandbox", "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["base_url"] == "https://sandbox.givecampus.com/api"


def test_token_is_read_from_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIVECAMPUS_TOKEN", PROD_TOKEN)
    store_path = tmp_path / "b.json"
    TokenStore(store_path).bind(PROD_TOKEN, Environment.PRODUCTION)
    assert main(["--store", str(store_path), "preflight", "--base-url", PRODUCTION]) == EXIT_OK


def test_bind_then_preflight(tmp_path, capsys):
    store_path = str(tmp_path / "b.json")
    assert main(["--store", store_path, "bind", "--token", PROD_TOKEN,
                 "--environment", "production", "--label", "CRM export"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--store", store_path, "preflight", "--token", PROD_TOKEN,
                 "--base-url", SANDBOX]) == EXIT_MISMATCH


def test_bindings_listing_never_prints_the_token(tmp_path, capsys):
    store_path = str(tmp_path / "b.json")
    main(["--store", store_path, "bind", "--token", PROD_TOKEN, "--environment", "production"])
    capsys.readouterr()
    main(["--store", store_path, "bindings"])
    out = capsys.readouterr().out
    assert PROD_TOKEN not in out


def test_endpoints_lists_every_documented_operation(capsys):
    assert main(["endpoints"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "55 documented operations across 16 published specs" in out
    assert "GET    /api/gifts" in out
    assert "No page/limit/offset/cursor parameter exists in any spec" in out


def test_endpoints_json_filtered_by_spec(capsys):
    main(["endpoints", "--spec", "v2.0.0", "--json"])
    ops = json.loads(capsys.readouterr().out)
    assert ops and all(op["api_version"] == "v2.0.0" for op in ops)
    assert all(op["base_path"] == "/api/v2" for op in ops)


def test_missing_token_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GIVECAMPUS_TOKEN", raising=False)
    monkeypatch.delenv("GC_API_TOKEN", raising=False)
    assert main(["--store", str(tmp_path / "b.json"), "preflight",
                 "--base-url", PRODUCTION]) == 2
