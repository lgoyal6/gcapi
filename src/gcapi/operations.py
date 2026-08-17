"""Access to the operation registry generated from GiveCampus' own Swagger specs.

operations.json is produced by tools/generate_operations.py from the YAML files in
evidence/specs/, which were downloaded verbatim from
https://www.givecampus.com/documentation/api/ on 2026-08-14. No endpoint in this
registry was written by hand.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

__all__ = ["registry", "all_operations", "find_operation", "operations_for_spec", "specs"]


@functools.lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    raw = resources.files(__package__).joinpath("operations.json").read_text()
    return json.loads(raw)


def all_operations() -> list[dict[str, Any]]:
    return list(registry()["operations"])


def specs() -> list[str]:
    return sorted({op["spec"] for op in all_operations()})


def operations_for_spec(spec: str) -> list[dict[str, Any]]:
    return [op for op in all_operations() if op["spec"] == spec]


def find_operation(operation_id: str) -> dict[str, Any]:
    """Look up by operationId.

    GiveCampus reuses some operationIds across specs (every spec declares its own
    `/results/{request_id}`, and form_fields.yaml labels its GET `findDesignations`).
    Ambiguous lookups raise rather than silently picking one.
    """
    matches = [op for op in all_operations() if op["operation_id"] == operation_id]
    if not matches:
        raise KeyError(f"no documented operation with operationId {operation_id!r}")
    if len(matches) > 1:
        where = ", ".join(f"{m['spec']} ({m['method']} {m['path']})" for m in matches)
        raise KeyError(
            f"operationId {operation_id!r} is not unique across GiveCampus' specs: {where}"
        )
    return matches[0]
