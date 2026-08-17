"""gcapi - CLI for the unofficial GiveCampus client.

The point of this CLI is `gcapi preflight`: it answers "is this token allowed to talk
to this base URL?" without sending anything anywhere. Every other subcommand that would
touch the network runs the same check first and exits non-zero on a mismatch.

Exit codes
    0  ok
    2  usage error
    3  environment mismatch / preflight refused   <- the one worth wiring into CI
    4  API error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Sequence

from .env import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    Environment,
    TokenStore,
    preflight,
)
from .errors import ApiError, GiveCampusError, PreflightRefused
from .models import GiftState, TimeField
from .operations import all_operations, specs

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISMATCH = 3
EXIT_API = 4

TOKEN_ENV_VARS = ("GIVECAMPUS_TOKEN", "GC_API_TOKEN")


def _resolve_token(args: argparse.Namespace) -> str:
    if getattr(args, "token", None):
        return str(args.token)
    for var in TOKEN_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    raise SystemExit(
        f"no token: pass --token or set one of {', '.join(TOKEN_ENV_VARS)}"
    )


def _resolve_base_url(args: argparse.Namespace) -> str:
    if getattr(args, "base_url", None):
        return str(args.base_url)
    env = os.environ.get("GIVECAMPUS_BASE_URL")
    if env:
        return env
    target = getattr(args, "environment", None)
    if target == "sandbox":
        return SANDBOX_BASE_URL
    if target == "production":
        return PRODUCTION_BASE_URL
    raise SystemExit(
        "no base URL: pass --base-url, set GIVECAMPUS_BASE_URL, or pass "
        "--environment production|sandbox"
    )


def _declared(args: argparse.Namespace) -> Environment | None:
    value = getattr(args, "environment", None)
    return Environment(value) if value else None


def _print_verdict(verdict: Any, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "decision": verdict.decision.value,
                    "base_url": verdict.base_url,
                    "url_environment": verdict.url_environment.value,
                    "token_environment": verdict.token_environment.value,
                    "token_fingerprint": verdict.token_fingerprint,
                    "reason": verdict.reason,
                    "signals": verdict.signals,
                },
                indent=2,
            )
        )
        return
    mark = "OK  " if verdict.allowed else "STOP"
    print(f"[{mark}] {verdict.reason}")
    print(f"       base URL         : {verdict.base_url}")
    print(f"       URL environment  : {verdict.url_environment.value}")
    print(f"       token environment: {verdict.token_environment.value} "
          f"(source: {verdict.signals.get('source')})")
    print(f"       token sha256     : {verdict.token_fingerprint[:16]}...")
    if verdict.signals.get("token_is_jwt"):
        print(f"       jwt hint         : {verdict.signals.get('jwt_environment_hint')} "
              f"(undocumented format, advisory only)")


# -- subcommands -------------------------------------------------------------------


def cmd_preflight(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    base_url = _resolve_base_url(args)
    verdict = preflight(
        token,
        base_url,
        declared_environment=_declared(args),
        store=TokenStore(args.store),
        allow_unverified=args.allow_unverified_environment,
    )
    _print_verdict(verdict, args.json)
    return EXIT_OK if verdict.allowed else EXIT_MISMATCH


def cmd_bind(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    store = TokenStore(args.store)
    fp = store.bind(token, Environment(args.environment), label=args.label or "")
    print(f"bound token sha256 {fp[:16]}... -> {args.environment}")
    print(f"store: {store.path} (fingerprints only, never the token)")
    return EXIT_OK


def cmd_unbind(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    store = TokenStore(args.store)
    print("removed" if store.unbind(token) else "no binding for that token")
    return EXIT_OK


def cmd_register_host(args: argparse.Namespace) -> int:
    store = TokenStore(args.store)
    store.register_host(args.host, Environment(args.environment))
    print(f"registered host {args.host} -> {args.environment}")
    return EXIT_OK


def cmd_bindings(args: argparse.Namespace) -> int:
    store = TokenStore(args.store)
    payload = {"store": str(store.path), "bindings": store.bindings(),
               "hosts": {h: e.value for h, e in store.hosts().items()}}
    if args.json:
        print(json.dumps(payload, indent=2))
        return EXIT_OK
    print(f"store: {store.path}")
    if not payload["bindings"]:
        print("  (no token bindings)")
    for fp, entry in payload["bindings"].items():
        print(f"  {fp[:16]}...  {entry['environment']:<10} {entry.get('label','')} "
              f"bound {entry.get('bound_at','')}")
    for host, env in payload["hosts"].items():
        print(f"  host {host} -> {env}")
    return EXIT_OK


def cmd_endpoints(args: argparse.Namespace) -> int:
    ops = all_operations()
    if args.spec:
        ops = [o for o in ops if o["spec"].startswith(args.spec)]
    if args.json:
        print(json.dumps(ops, indent=2))
        return EXIT_OK
    print(f"{len(ops)} documented operations across {len(specs())} published specs\n")
    current = None
    for op in ops:
        if op["spec"] != current:
            current = op["spec"]
            print(f"  {current}  ({op['spec_url']})")
        params = ", ".join(
            f"{p['name']}{'*' if p['required'] else ''}" for p in op["parameters"]
        )
        base = op["base_path"] or ""
        print(f"      {op['method']:<6} {base}{op['path']:<38} {op['operation_id'] or ''}")
        if params:
            print(f"             params: {params}")
    print("\n* = required.  No page/limit/offset/cursor parameter exists in any spec:")
    print("  results arrive as one JSON array behind a completed request's download_url.")
    return EXIT_OK


def cmd_gifts(args: argparse.Namespace) -> int:
    from .client import GiveCampusClient  # imported late so `endpoints`/`bind` need no httpx

    token = _resolve_token(args)
    base_url = _resolve_base_url(args)
    states = [GiftState(s) for s in (args.state or [])]
    if args.all_states:
        states = list(GiftState)

    try:
        client = GiveCampusClient(
            token,
            base_url,
            environment=_declared(args),
            store=TokenStore(args.store),
            allow_unverified_environment=args.allow_unverified_environment,
        )
    except PreflightRefused as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        return EXIT_MISMATCH

    with client:
        print(f"[OK  ] {client.verdict.reason}", file=sys.stderr)
        try:
            gifts = client.gifts(
                start=args.start,
                end=args.end,
                states=states,
                time_field=TimeField(args.time_field) if args.time_field else None,
            )
        except ApiError as exc:
            print(f"[API ] {exc}", file=sys.stderr)
            return EXIT_API
        except GiveCampusError as exc:
            print(f"[ERR ] {exc}", file=sys.stderr)
            return EXIT_API

    print(json.dumps([g.model_dump(mode="json", exclude_none=True) for g in gifts], indent=2))
    return EXIT_OK


# -- parser ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gcapi",
        description="Unofficial typed client for the GiveCampus API, with a "
                    "token/environment preflight guard.",
    )
    parser.add_argument("--store", help="path to the token-binding store "
                                        "(default: ~/.config/givecampus/token-bindings.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_auth_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--token", help=f"API token (or set {' / '.join(TOKEN_ENV_VARS)})")
        p.add_argument("--base-url", help="API base URL (or set GIVECAMPUS_BASE_URL)")
        p.add_argument("--environment", choices=["production", "sandbox"],
                       help="declare which environment the token belongs to")
        p.add_argument("--allow-unverified-environment", action="store_true",
                       help="proceed even when the token/URL pairing cannot be proven")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("preflight", help="check a token against a base URL, sending nothing")
    add_auth_flags(p)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("bind", help="record which environment a token belongs to (stores a hash)")
    p.add_argument("--token")
    p.add_argument("--environment", choices=["production", "sandbox"], required=True)
    p.add_argument("--label", help="human note, e.g. 'nightly CRM sync'")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("unbind", help="forget a token binding")
    p.add_argument("--token")
    p.set_defaults(func=cmd_unbind)

    p = sub.add_parser("register-host", help="map a school custom domain to an environment")
    p.add_argument("host")
    p.add_argument("--environment", choices=["production", "sandbox"], required=True)
    p.set_defaults(func=cmd_register_host)

    p = sub.add_parser("bindings", help="list recorded bindings and hosts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bindings)

    p = sub.add_parser("endpoints", help="print the operations parsed from GiveCampus' own specs")
    p.add_argument("--spec", help="filter, e.g. v1.0.0 or v2.0.0/designations.yaml")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_endpoints)

    p = sub.add_parser("gifts", help="run a Gifts query end to end (preflight, submit, poll, download)")
    add_auth_flags(p)
    p.add_argument("--start", type=int, required=True, help="Unix seconds")
    p.add_argument("--end", type=int, default=None, help="Unix seconds (default: now)")
    p.add_argument("--state", action="append", choices=[s.value for s in GiftState],
                   help="repeatable; at least one is required by the API")
    p.add_argument("--all-states", action="store_true", help="include all eight states")
    p.add_argument("--time-field", choices=[t.value for t in TimeField],
                   help="default is confirmed_at / datetime_of_pledge")
    p.set_defaults(func=cmd_gifts)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "gifts" and args.end is None:
        args.end = int(time.time())
    try:
        return int(args.func(args))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
