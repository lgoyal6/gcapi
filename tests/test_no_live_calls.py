"""Proof that this suite cannot reach GiveCampus.

This project was built against published documentation only. No request was made to
www.givecampus.com/api or sandbox.givecampus.com/api at any point, with or without a
credential. These tests make that structural rather than a promise.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import NetworkAccessAttempted


def test_real_transport_is_blocked():
    """The autouse no_network fixture makes any live request raise."""
    with httpx.Client() as client:
        with pytest.raises(NetworkAccessAttempted):
            client.get("https://www.givecampus.com/api/gifts")


def test_sandbox_is_blocked_too():
    with httpx.Client() as client:
        with pytest.raises(NetworkAccessAttempted):
            client.get("https://sandbox.givecampus.com/api/gifts")


# Every non-docstring string literal in the package that mentions a givecampus host.
# These are the two documented base URLs plus the hostnames they are classified by.
# None of them is ever passed to a request without going through the preflight guard.
ALLOWED_HOST_LITERALS = {
    "https://www.givecampus.com/api",
    "https://sandbox.givecampus.com/api",
    "www.givecampus.com",
    "givecampus.com",
    "sandbox.givecampus.com",
}


def _non_docstring_literals(tree):
    """Every string constant in the module except module/class/function docstrings."""
    import ast

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def test_no_undeclared_givecampus_url_literal_in_the_package():
    """The only executable givecampus.com literals are the documented hosts.

    Documentation URLs live in docstrings and comments, which carry no runtime effect.
    A new hardcoded endpoint would fail this test.
    """
    import ast
    import pathlib

    import gcapi

    pkg = pathlib.Path(gcapi.__file__).parent
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for literal in _non_docstring_literals(tree):
            if "givecampus.com" in literal and literal not in ALLOWED_HOST_LITERALS:
                offenders.append(f"{path.name}: {literal!r}")
    assert offenders == [], "undeclared givecampus.com literal: " + "; ".join(offenders)
