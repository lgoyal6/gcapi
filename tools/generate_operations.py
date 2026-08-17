"""Generate src/gcapi/operations.json from GiveCampus' own published Swagger 2.0 specs.

The specs in evidence/specs/ were downloaded verbatim from
https://www.givecampus.com/documentation/api/<version>/<name>.yaml on 2026-08-14.

Nothing here invents an endpoint. If an operation is not in their YAML, it is not
in the generated registry. Re-run after refreshing evidence/specs/:

    python tools/generate_operations.py
"""

from __future__ import annotations

import glob
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("pip install pyyaml to regenerate the registry")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_GLOB = os.path.join(ROOT, "evidence", "specs", "v*", "*.yaml")
OUT = os.path.join(ROOT, "src", "gcapi", "operations.json")

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

DOC_URL = "https://www.givecampus.com/documentation/api/{version}/{name}.yaml"


def main() -> int:
    operations = []
    for path in sorted(glob.glob(SPEC_GLOB)):
        parts = path.split(os.sep)
        version, filename = parts[-2], parts[-1]
        name = filename[: -len(".yaml")]
        spec = yaml.safe_load(open(path))
        base_path = spec.get("basePath")
        title = spec.get("info", {}).get("title")
        for route, ops in (spec.get("paths") or {}).items():
            for method, op in ops.items():
                if method not in HTTP_METHODS:
                    continue
                params = []
                for p in op.get("parameters") or []:
                    params.append(
                        {
                            "name": p.get("name"),
                            "in": p.get("in"),
                            "required": bool(p.get("required")),
                            "type": p.get("type"),
                            "enum": p.get("enum"),
                        }
                    )
                operations.append(
                    {
                        "spec": f"{version}/{filename}",
                        "spec_url": DOC_URL.format(version=version, name=name),
                        "api_version": version,
                        "title": title,
                        "base_path": base_path,
                        "method": method.upper(),
                        "path": route,
                        "operation_id": op.get("operationId"),
                        "summary": op.get("summary"),
                        "parameters": params,
                        "response_codes": sorted((op.get("responses") or {}).keys()),
                    }
                )

    payload = {
        "source": "GiveCampus published Swagger 2.0 specs",
        "captured": "2026-08-14",
        "docs_index": "https://www.givecampus.com/documentation/api/index.html",
        "operations": operations,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"wrote {OUT}: {len(operations)} operations from {len(set(o['spec'] for o in operations))} specs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
