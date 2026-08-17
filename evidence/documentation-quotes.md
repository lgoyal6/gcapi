# Verbatim documentation quotes

All captured 2026-08-14. These are the only inputs used to build this client.
No GiveCampus API endpoint was contacted, in either environment, at any point.

---

## 1. Token / environment incompatibility (the reason the CLI exists)

Source: <https://support.givecampus.com/hc/en-us/articles/29093649557527-GiveCampus-API-A-Deep-Dive-on-Parameters>

Under "Sandbox payment processing and deposit validation":

> **API tokens are environment-specific:**
>
> - Tokens created in production won't work in the sandbox and vice versa.
> - Generate a unique API token in each environment.

> **Where to create API tokens:**
>
> - Sign in to the environment you intend to use and navigate to School Dashboard >
>   Integrations > API Keys to generate the token.

> **Testing tips:**
>
> - In the API Documentation site's testing tool, use the token that corresponds with
>   the base URL (sandbox or production).
> - When copying example curl commands or sample code from production documentation,
>   update the base URL and replace the Authorization token with your sandbox token.
> - Keep sandbox tokens secure, rotate them when needed, and consider creating distinct
>   tokens for different test integrations or team members.

Under "Common pitfalls" (in the deduplication section of the same article):

> - Reusing tokens across environments: Production tokens are not valid in sandbox and
>   vice versa.

Under "Quick checklist" (same article, deduplication section):

> - Remember that API tokens are environment-specific, production tokens won't work in
>   the sandbox and vice versa.

**Transcription note.** In that last line GiveCampus' original uses an em dash between
"environment-specific" and "production"; it is rendered here as a comma to comply with a
house style rule against em dashes. The wording is otherwise unaltered. No other quote in
this file was modified in any way.

**Precision note.** The word "silently" does not appear anywhere in this warning. The
docs assert incompatibility, not that the failure is quiet. See quote 3 for the one
place their documentation does connect a wrong-environment token to a quiet outcome.

---

## 2. Base URLs

Same article:

> - Sandbox API base URL: https://sandbox.givecampus.com/api/
> - Production API base URL: https://www.givecampus.com/api/

Schools may also front the API with their own domain. Source:
<https://support.omaticsoftware.com/s/article/How-to-Obtain-API-Credentials-for-GiveCampus>

> - Existing custom domain URL, if applicable. If you do not have a custom domain, you
>   will use https://www.givecampus.com.

---

## 3. The documented quiet failure: Gifts returning an empty array

Same article, "Why does my Gifts API call return an empty array?":

> If your Gifts API request returns a 200 response with a `status` of `completed` yet
> yields an empty JSON array, the API is functioning as designed. The Gifts endpoint
> requires a state-based filter along with your `start`/`end` parameters.

> - Every Gifts request using a `start`/`end` range must include at least one state
>   parameter. Valid state parameters include: `authorized`, `charged_back`, `disputed`,
>   `failed`, `paid`, `pending`, `pending_authorization`, and `refunded`.

And in the same section's troubleshooting checklist, item 4:

> If event-linked gifts are still missing, double-check your query string for typos and
> confirm that you are using the correct environment token (sandbox vs. production).

This is the one place their own documentation puts a wrong-environment token next to a
symptom that is HTTP 200 with an empty body rather than a loud error.

---

## 4. Auth scheme

Source: <https://www.givecampus.com/documentation/api/v1.0.0/gifts.yaml> (`info.description`)

> Every API request must have a bearer token attached in the Authorization header.
> These tokens are generated within the GiveCampus UI and only school administrators
> with super-user permissions can generate and manage them.
> All API responses will be delivered over a secure HTTPS connection, and a TLS version
> of 1.2 or 1.3 is required.

`securityDefinitions` in every spec:

```yaml
securityDefinitions:
  Bearer:
    type: apiKey
    name: Authorization
    in: header
    description: Enter the token with the `Bearer ` prefix, e.g. "Bearer XXXXXXXXXXXXX".
```

No token format is published. `Bearer XXXXXXXXXXXXX` is the entire specification of the
credential's shape.

---

## 5. Request lifecycle (there is no pagination)

Source: <https://www.givecampus.com/documentation/api/v1.0.0/gifts.yaml>

> The API delivers data asynchronously using a request-and-poll pattern:
>
> 1. **Submit your request** - Call the endpoint with your desired parameters. You'll
>    receive a `request_id` and a `status` of `in_progress`.
> 2. **Poll for results** - Call `/results/{request_id}` every 2-5 seconds until
>    `status` changes to `completed` (or `error`).
> 3. **Download your data** - When complete, the response includes a `download_url`
>    containing a JSON array of objects matching the model defined below.
>
> Most requests complete within a few seconds.

No `page`, `per_page`, `limit`, `offset`, or `cursor` parameter appears in any of the 16
published specs. This was checked mechanically; see
`tests/test_models_and_registry.py::test_no_pagination_parameter_exists_anywhere`.

The documented substitute, from the support article's "Recommended incremental pattern
(safe, idempotent)":

> 1. Store the most recent timestamp you successfully processed (for example, the maximum
>    `confirmed_at` imported) as `last_processed_at` (Unix timestamp).
> 2. When polling the API, request a new batch with `start = last_processed_at + 1` and
>    `end = now`.
> 3. Sort the results by your chosen `time_field` in ascending order and process each
>    gift, recording its unique API `id` to your deduplication index or CRM record.
> 4. Update `last_processed_at` to the largest `time_field` value from the successfully
>    processed gifts.
>
> This strategy prevents overlapping time windows and avoids retrieving duplicate gifts.

---

## 6. Envelope and error schemas

`shared/types/api_request.yaml`:

```yaml
type: object
properties:
  request_id: {type: string}
  status: {type: string, enum: [in_progress, completed, error]}
```

`shared/types/api_results.yaml`:

```yaml
type: object
properties:
  status: {type: string, enum: [completed, error, in_progress]}
  download_url: {type: string}
```

`v2.0.0/types/bad_request_error.yaml`, `unauthorized_error.yaml`, `not_found_error.yaml`
are all `{message: string}`. `internal_server_error.yaml` adds `{status: string}` with
`example: error`.

Status codes across all 55 documented operations: 200, 202, 400, 401, 404, 500.

---

## 7. Rate limits

**Not documented.** No `429`, no `Retry-After`, no request-per-second figure appears
anywhere in the 16 specs. The support article refers the reader onward:

> Note that this limit is separate from GET request rate limits and pagination rules,
> which are documented on the API Documentation site.

but the API Documentation site does not in fact document either. Recorded as a gap, not
used as a claim.

---

## 8. Bulk write cap

Same article, "How many records can I include in a single bulk write request?":

> - A bulk write request (POST or PUT) is limited to 5,000 records. If a request exceeds
>   this limit, it will return a 400 Bad Request response.
> - This cap applies to all bulk write requests in the GiveCampus API, including those
>   for creating or updating constituents, designations, designation groups, imported
>   gifts, and other resources.
> - For the assignments endpoint, the 5,000-record limit counts every constituent
>   reference in the entire request body, not per array item.

---

## 9. Payload examples used as fixtures

Source: <https://support.givecampus.com/hc/en-us/articles/40836868939415-GiveCampus-API-Payload-Examples>

Three gift payloads, copied byte for byte into `tests/fixtures/`:

- "One Time Authorized with Designations" -> `gifts_authorized_with_designations.json`
- "One Time Deposited Gifts" -> `gift_deposited.json`
- "Recurring Subscription Payments" -> `gifts_recurring_installment.json`

The donors in these examples are fictional (Annie Edison, Stephen Colbert, Conan
O'Brien) and the emails use `.test` domains, so they are GiveCampus' own synthetic
records, not real donor data.

---

## 10. Beta status of the Assignments API

Source: <https://www.givecampus.com/documentation/api/v1.0.0/assignments.yaml>

> This API is currently in **Beta**. The interface may change while we confirm successful
> usage with early partners. Please contact your GiveCampus representative before
> integrating.

The Assignments operations are listed in the registry and flagged, but no typed helper
was written for them because the interface is explicitly declared unstable.
