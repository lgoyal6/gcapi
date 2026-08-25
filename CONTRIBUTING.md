# Contributing to gcapi

Thanks for looking. gcapi is built entirely from GiveCampus' published documentation,
and that constraint shapes everything about how changes land here.

## The contract you must not break

**No request is ever sent to GiveCampus.** Not from a test, not from a fixture refresh,
not "just once to check". This client handles donor and payment records for real
institutions, and the correct amount of unsolicited traffic against their API is zero.
`tests/conftest.py` installs an autouse `no_network` fixture that replaces
`httpx.HTTPTransport.handle_request` with a raiser, and `tests/test_no_live_calls.py`
asserts that the block actually holds. That same file walks the AST of every module in
`src/gcapi/` and fails if a new `givecampus.com` string literal appears outside
`ALLOWED_HOST_LITERALS`. If your change makes that test fail, the change is wrong.

The second invariant is the reason the CLI half exists:

- The environment check runs inside `Client.__init__`, before any `httpx.Client` is
  constructed. A mismatched token cannot reach the network to be rejected by a 401
  because there is no socket yet. Keep it that way; do not move the check into a
  per-request hook.
- The guard fails closed. An unbound token or an undocumented host is a refusal, not a
  shrug. `--allow-unverified-environment` is the only escape and it is explicit.
- Only `SHA-256(token)` is persisted, never the token. `TokenStore._write` chmods the
  file to `0600`. Anything that writes a raw credential to disk will not land.

## Getting oriented

| Path | What lives there |
|---|---|
| `src/gcapi/env.py` | The preflight guard, the token store, host classification. |
| `src/gcapi/client.py` | submit / poll / download, retries, the typed `gifts()` path. |
| `src/gcapi/models.py` | Pydantic models transcribed from the specs, with six `# DRIFT:` markers. |
| `src/gcapi/operations.json` | Generated. 55 operations from 16 specs. Never hand-edited. |
| `tools/generate_operations.py` | The generator that produces it. |
| `evidence/specs/` | The 16 published YAML specs, verbatim as downloaded. |
| `evidence/documentation-quotes.md` | Every documentation quote this build relies on, with URLs. |
| `tests/fixtures/` | GiveCampus' own published example payloads, verbatim. |

## Building and testing

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'

.venv/bin/python -m pytest      # 81 tests, offline, about a second
.venv/bin/gcapi endpoints       # the 55 operations, parsed from their specs
```

Regenerating the registry after refreshing `evidence/specs/`:

```bash
.venv/bin/python tools/generate_operations.py
```

There is no CI in this repo, so run the suite locally before you open a PR.

## What makes a good PR here

- One concern per PR, with a test that fails before and passes after.
- Every claim needs a citation. If you assert something about the API's behaviour, the
  evidence is a quote in `evidence/documentation-quotes.md` with a URL, or a payload in
  `evidence/specs/`. "The server returns this" is not available to us; we have never
  asked it.
- Do not edit `src/gcapi/operations.py` or `operations.json` by hand. Change the spec
  files or the generator and re-run it. An operation that is not in their YAML does not
  belong in this client.
- New model fields are transcribed from the spec, not guessed. Where their example
  payload disagrees with their spec, widen the type and mark it `# DRIFT:` inline with
  what each side says, the way the existing six are marked.
- Keep `extra="allow"` on models. Silently dropping an unknown key from a donor record
  is the failure mode this setting exists to prevent.

## Good first areas

- Typed response models cover `Gift` only. The other fifteen resources (constituents,
  events, designations, contact reports, opportunities and the rest) go through the
  generic `run()` path and come back as `dict`, even though their specs are already
  sitting in `evidence/specs/`. Constituents is the obvious next one.
- Typed request builders cover `gifts()` only. A second builder in the same shape would
  make the pattern legible.
- `tests/fixtures/` holds three payloads and all three are gifts. Any published example
  payload for another resource, copied verbatim with its source URL, is directly useful.
- Write payloads go through `bulk_write()` as plain lists: the 5,000-record cap is
  enforced, per-field validation is not.

## Conduct

Be decent. Disagree about the code, not about the person.
