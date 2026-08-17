"""Token/environment preflight guard.

Why this module exists
----------------------
GiveCampus documents, in their own support article, that API tokens are scoped to a
single environment:

    "API tokens are environment-specific:
     - Tokens created in production won't work in the sandbox and vice versa.
     - Generate a unique API token in each environment."
    -- https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters

and, under "Common pitfalls":

    "Reusing tokens across environments: Production tokens are not valid in sandbox
     and vice versa."
    -- same URL

and, for copied sample code:

    "When copying example curl commands or sample code from production documentation,
     update the base URL and replace the Authorization token with your sandbox token."
    -- same URL

That last one is the hazard this guard closes. Two independent things have to be
changed together (base URL and token) and nothing checks that you changed both.

IMPORTANT HONESTY NOTE ON TOKEN FORMAT
--------------------------------------
GiveCampus does NOT publish a token format. The only description in the specs is
`Enter the token with the 'Bearer ' prefix, e.g. "Bearer XXXXXXXXXXXXX"`. So there
is no documented way to look at a token string and know which environment minted it,
and this module does not pretend otherwise. Instead it establishes the pairing the
only two ways that are sound offline:

  1. EXPLICIT   -- the operator declares the environment (config / flag / env var).
  2. BOUND      -- a local trust store records SHA-256(token) -> environment the first
                   time the operator vouches for it, and every later run checks the
                   fingerprint against the target base URL. The token itself is never
                   written to disk, only its hash.

A third, strictly best-effort signal (JWT claim inspection) is attempted and reported
but NEVER used on its own to approve a request, because the format is undocumented.

The guard fails closed: if the pairing cannot be established, the verdict is REFUSE.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import enum
import hashlib
import json
import os
import pathlib
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "Environment",
    "Decision",
    "Verdict",
    "EnvironmentMismatch",
    "TokenStore",
    "classify_base_url",
    "fingerprint",
    "inspect_jwt_claims",
    "preflight",
    "PRODUCTION_BASE_URL",
    "SANDBOX_BASE_URL",
]

# Verbatim from the support article's "Sandbox payment processing and deposit
# validation" section:
#   "Sandbox API base URL: https://sandbox.givecampus.com/api/"
#   "Production API base URL: https://www.givecampus.com/api/"
PRODUCTION_BASE_URL = "https://www.givecampus.com/api"
SANDBOX_BASE_URL = "https://sandbox.givecampus.com/api"


class Environment(str, enum.Enum):
    PRODUCTION = "production"
    SANDBOX = "sandbox"
    UNKNOWN = "unknown"


class Decision(str, enum.Enum):
    ALLOW = "allow"
    REFUSE = "refuse"


# Documented host -> environment mapping. Deliberately small: these are the only two
# hosts GiveCampus publishes. Schools may also front the API with a custom domain
# ("Existing custom domain URL, if applicable. If you do not have a custom domain,
# you will use https://www.givecampus.com" --
# https://support.omaticsoftware.com/s/article/How-to-Obtain-API-Credentials-for-GiveCampus),
# so an unknown host is UNKNOWN, not "probably production". Operators register custom
# hosts explicitly via TokenStore.register_host().
DOCUMENTED_HOSTS: dict[str, Environment] = {
    "www.givecampus.com": Environment.PRODUCTION,
    "givecampus.com": Environment.PRODUCTION,
    "sandbox.givecampus.com": Environment.SANDBOX,
}


class EnvironmentMismatch(RuntimeError):
    """Raised instead of sending a request whose token and base URL disagree."""


@dataclasses.dataclass(frozen=True)
class Verdict:
    decision: Decision
    base_url: str
    url_environment: Environment
    token_environment: Environment
    token_fingerprint: str
    reason: str
    signals: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def raise_for_decision(self) -> "Verdict":
        if not self.allowed:
            raise EnvironmentMismatch(self.reason)
        return self


def classify_base_url(base_url: str, extra_hosts: dict[str, Environment] | None = None) -> Environment:
    """Map a base URL to an environment using only documented hosts (plus operator-registered ones)."""
    host = (urlparse(base_url).hostname or "").lower()
    if extra_hosts and host in extra_hosts:
        return extra_hosts[host]
    return DOCUMENTED_HOSTS.get(host, Environment.UNKNOWN)


def fingerprint(token: str) -> str:
    """SHA-256 of the token. This is what gets persisted; the token never is."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def inspect_jwt_claims(token: str) -> dict[str, Any] | None:
    """BEST EFFORT ONLY, and never sufficient on its own.

    GiveCampus does not document the token format. If a token happens to be a JWT we
    decode (never verify) the payload so the operator can see any environment-ish
    claim. Returns None for anything that is not a three-segment JWT, which is the
    expected case for an opaque token.
    """
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _jwt_environment_hint(claims: dict[str, Any]) -> Environment:
    """Read an environment hint out of JWT claims. Undocumented; advisory only."""
    for key in ("env", "environment", "stage", "iss", "aud"):
        value = claims.get(key)
        if not isinstance(value, str):
            continue
        low = value.lower()
        if "sandbox" in low or "staging" in low:
            return Environment.SANDBOX
        if "prod" in low or "www.givecampus.com" in low:
            return Environment.PRODUCTION
    return Environment.UNKNOWN


def _default_store_path() -> pathlib.Path:
    override = os.environ.get("GCAPI_STORE")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / "givecampus" / "token-bindings.json"


class TokenStore:
    """Local, offline record of which environment a token fingerprint belongs to.

    Stores only SHA-256 fingerprints and operator-supplied labels. Never the token.
    """

    def __init__(self, path: pathlib.Path | str | None = None) -> None:
        self.path = pathlib.Path(path) if path else _default_store_path()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"bindings": {}, "hosts": {}}
        data = json.loads(self.path.read_text() or "{}")
        data.setdefault("bindings", {})
        data.setdefault("hosts", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(self.path, 0o600)

    def bind(self, token: str, environment: Environment, label: str = "") -> str:
        fp = fingerprint(token)
        data = self._read()
        data["bindings"][fp] = {
            "environment": environment.value,
            "label": label,
            "bound_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        self._write(data)
        return fp

    def unbind(self, token: str) -> bool:
        fp = fingerprint(token)
        data = self._read()
        removed = data["bindings"].pop(fp, None) is not None
        if removed:
            self._write(data)
        return removed

    def lookup(self, token: str) -> Environment:
        entry = self._read()["bindings"].get(fingerprint(token))
        if not entry:
            return Environment.UNKNOWN
        return Environment(entry["environment"])

    def register_host(self, host: str, environment: Environment) -> None:
        data = self._read()
        data["hosts"][host.lower()] = environment.value
        self._write(data)

    def hosts(self) -> dict[str, Environment]:
        return {h: Environment(v) for h, v in self._read()["hosts"].items()}

    def bindings(self) -> dict[str, dict[str, Any]]:
        return self._read()["bindings"]


def preflight(
    token: str,
    base_url: str,
    *,
    declared_environment: Environment | None = None,
    store: TokenStore | None = None,
    allow_unverified: bool = False,
) -> Verdict:
    """Decide, with zero network traffic, whether this token may be sent to this base URL.

    Resolution order for the token's environment:
      1. declared_environment  (operator said so explicitly, this run)
      2. TokenStore binding    (operator said so previously; matched by SHA-256)
      3. UNKNOWN               (JWT claims are reported but never decide)
    """
    store = store or TokenStore()
    fp = fingerprint(token)
    url_env = classify_base_url(base_url, extra_hosts=store.hosts())

    claims = inspect_jwt_claims(token)
    signals: dict[str, Any] = {
        "url_host": (urlparse(base_url).hostname or "").lower(),
        "token_is_jwt": claims is not None,
        "jwt_environment_hint": (_jwt_environment_hint(claims).value if claims else None),
        "source": None,
    }

    bound_env = store.lookup(token)
    if declared_environment is not None:
        token_env = declared_environment
        signals["source"] = "declared"
    elif bound_env is not Environment.UNKNOWN:
        token_env = bound_env
        signals["source"] = "token-store binding"
    else:
        token_env = Environment.UNKNOWN
        signals["source"] = "none"

    # A declaration that contradicts a stored binding is itself a red flag.
    if (
        declared_environment is not None
        and bound_env is not Environment.UNKNOWN
        and bound_env is not declared_environment
    ):
        return Verdict(
            decision=Decision.REFUSE,
            base_url=base_url,
            url_environment=url_env,
            token_environment=declared_environment,
            token_fingerprint=fp,
            reason=(
                f"Declared environment '{declared_environment.value}' contradicts the stored "
                f"binding '{bound_env.value}' for token {fp[:12]}. Re-bind deliberately with "
                f"'gcapi bind' if the token really did change environments."
            ),
            signals=signals,
        )

    if url_env is Environment.UNKNOWN:
        if allow_unverified:
            return Verdict(
                Decision.ALLOW, base_url, url_env, token_env, fp,
                f"Host '{signals['url_host']}' is not a documented GiveCampus host; allowed by "
                f"--allow-unverified-environment.",
                signals,
            )
        return Verdict(
            Decision.REFUSE, base_url, url_env, token_env, fp,
            (
                f"Host '{signals['url_host']}' is not a documented GiveCampus API host. "
                f"Documented hosts are {PRODUCTION_BASE_URL} (production) and "
                f"{SANDBOX_BASE_URL} (sandbox). If this is your school's custom domain, "
                f"register it once with 'gcapi register-host'."
            ),
            signals,
        )

    if token_env is Environment.UNKNOWN:
        if allow_unverified:
            return Verdict(
                Decision.ALLOW, base_url, url_env, token_env, fp,
                f"Token {fp[:12]} is unbound; allowed by --allow-unverified-environment.",
                signals,
            )
        return Verdict(
            Decision.REFUSE, base_url, url_env, token_env, fp,
            (
                f"Token {fp[:12]} has no recorded environment, and GiveCampus does not publish a "
                f"token format that would let this be inferred. Bind it once with "
                f"'gcapi bind --environment {url_env.value}' or pass --environment explicitly."
            ),
            signals,
        )

    if token_env is not url_env:
        return Verdict(
            Decision.REFUSE, base_url, url_env, token_env, fp,
            (
                f"Refusing to send a {token_env.value} token to a {url_env.value} base URL "
                f"({base_url}). GiveCampus documents that tokens created in production will not "
                f"work in the sandbox and vice versa; generate a token in the environment you "
                f"are targeting."
            ),
            signals,
        )

    return Verdict(
        Decision.ALLOW, base_url, url_env, token_env, fp,
        f"Token {fp[:12]} is recorded as {token_env.value} and {base_url} is a documented "
        f"{url_env.value} base URL.",
        signals,
    )
