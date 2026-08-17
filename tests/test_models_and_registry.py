"""Models validate GiveCampus' own published example payloads, and the operation
registry matches their own published specs.

Fixtures are copied verbatim from
https://support.givecampus.com/hc/en-us/articles/40836868939415-GiveCampus-API-Payload-Examples
(their sample donors are fictional: Annie Edison, Stephen Colbert, Conan O'Brien).
"""

from __future__ import annotations

import pytest

from gcapi.models import (
    ApiRequest,
    ApiResults,
    ApiErrorBody,
    DonationType,
    Gift,
    GiftState,
    RequestStatus,
    TimeField,
)
from gcapi.operations import all_operations, find_operation, specs

from conftest import load_fixture


# -- models parse their published examples -------------------------------------------


def test_authorized_gift_example_parses():
    [gift] = [Gift.model_validate(g) for g in load_fixture("gifts_authorized_with_designations.json")]
    assert gift.id == 30059721
    assert gift.donation_type is DonationType.DONATION
    assert gift.state == "authorized"
    assert gift.card_type == "Visa"
    assert gift.address.postal_code == "33333"
    assert gift.project.id == 7605
    assert gift.custom_fields["first_name"] == "Annie"
    assert gift.event_id == 1326


def test_deposited_gift_example_parses():
    gift = Gift.model_validate(load_fixture("gift_deposited.json"))
    assert gift.state == "paid"
    assert gift.stripe_deposit == "pstools_20260520_b81947a61fbb"
    # DRIFT: gifts.yaml types deposited_at as string; the example returns an integer.
    assert gift.timestamps.deposited_at == 1779289975


def test_recurring_installment_example_parses():
    [gift] = [Gift.model_validate(g) for g in load_fixture("gifts_recurring_installment.json")]
    assert gift.donation_type is DonationType.INSTALLMENT
    sub = gift.subscription
    # DRIFT: spec types id/length/installment_number as string; example returns ints.
    assert sub.id == 2295
    assert sub.length == 36
    assert sub.installment_number == 1
    assert sub.normalized_period == "monthly"
    assert sub.normalized_length == 36
    assert sub.period == "limited_months"


def test_undocumented_fields_are_preserved_not_dropped():
    """Their own example ships `constituent_identifer` (missing an i), which is not in
    gifts.yaml. A donor-data client must not silently drop it."""
    gift = Gift.model_validate(load_fixture("gift_deposited.json"))
    extras = gift.undocumented_fields
    assert "constituent_identifer" in extras
    assert extras["constituent_identifer"] == "0000580306"
    assert gift.constituent_identifier == "0000580306"


def test_round_trip_keeps_the_undocumented_key():
    raw = load_fixture("gift_deposited.json")
    dumped = Gift.model_validate(raw).model_dump(mode="json")
    assert dumped["constituent_identifer"] == "0000580306"


def test_bad_donation_type_is_rejected():
    with pytest.raises(Exception):
        Gift.model_validate({"donation_type": "not-a-real-type"})


# -- envelopes -----------------------------------------------------------------------


def test_api_request_envelope():
    r = ApiRequest.model_validate({"request_id": "abc123", "status": "in_progress"})
    assert r.status is RequestStatus.IN_PROGRESS


def test_api_results_envelope():
    r = ApiResults.model_validate({"status": "completed", "download_url": "https://x.test/a.json"})
    assert r.status is RequestStatus.COMPLETED


def test_error_bodies():
    assert ApiErrorBody.model_validate({"message": "Example error message"}).message == "Example error message"
    five = ApiErrorBody.model_validate({"message": "boom", "status": "error"})
    assert five.status == "error"


def test_enum_membership_matches_the_specs():
    assert {s.value for s in GiftState} == {
        "authorized", "charged_back", "disputed", "failed",
        "paid", "pending", "pending_authorization", "refunded",
    }
    assert {t.value for t in TimeField} == {
        "captured_at", "checkout_at", "created_at",
        "deposited_at", "refunded_at", "updated_at",
    }
    assert {d.value for d in DonationType} == {
        "challenge", "donation", "pledge", "general", "installment", "match",
    }


# -- registry ------------------------------------------------------------------------


def test_registry_covers_every_published_spec():
    assert len(specs()) == 16
    assert len(all_operations()) == 55


def test_every_operation_carries_its_source_url():
    for op in all_operations():
        assert op["spec_url"].startswith("https://www.givecampus.com/documentation/api/")
        assert op["method"] in {"GET", "POST", "PUT", "DELETE"}
        assert op["base_path"] in {"/api", "/api/v2"}


def test_v2_specs_use_the_v2_base_path():
    v2 = [op for op in all_operations() if op["api_version"] == "v2.0.0"]
    assert v2 and all(op["base_path"] == "/api/v2" for op in v2)


def test_find_operation_by_id():
    op = find_operation("findGifts")
    assert op["method"] == "GET" and op["path"] == "/gifts"
    assert op["spec"] == "v1.0.0/gifts.yaml"


def test_ambiguous_operation_ids_raise_rather_than_guess():
    """Every spec declares its own /results/{request_id} as `findResults`."""
    with pytest.raises(KeyError, match="not unique"):
        find_operation("findResults")


def test_unknown_operation_id_raises():
    with pytest.raises(KeyError, match="no documented operation"):
        find_operation("deleteAllTheGifts")


def test_no_pagination_parameter_exists_anywhere():
    """The finding that shapes the client: there is nothing to paginate with."""
    paging = {"page", "per_page", "limit", "offset", "cursor", "after", "before", "page_size"}
    found = {
        (op["spec"], p["name"])
        for op in all_operations()
        for p in op["parameters"]
        if p["name"] in paging
    }
    assert found == set()


def test_no_rate_limit_status_is_documented():
    """429 appears in none of the 16 specs; the client retries it defensively anyway."""
    codes = {c for op in all_operations() for c in op["response_codes"]}
    assert codes == {"200", "202", "400", "401", "404", "500"}
    assert "429" not in codes
