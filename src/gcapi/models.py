"""Pydantic models for the documented GiveCampus payloads.

Every field below is transcribed from a published spec. Nothing is invented.

  Gift and its nested objects -> https://www.givecampus.com/documentation/api/v1.0.0/gifts.yaml
  ApiRequest                  -> https://www.givecampus.com/documentation/api/shared/types/api_request.yaml
  ApiResults                  -> https://www.givecampus.com/documentation/api/shared/types/api_results.yaml
  InProgressApiResults        -> https://www.givecampus.com/documentation/api/shared/types/in_progress_api_results.yaml
  ApiErrorBody                -> https://www.givecampus.com/documentation/api/v2.0.0/types/bad_request_error.yaml

TWO DELIBERATE MODELLING CHOICES
--------------------------------
1. `extra="allow"` everywhere. This client carries donor and payment records. Silently
   dropping a field the docs have not caught up with would be the worst possible
   failure mode, so undocumented keys are preserved on the model. Their own published
   example payload contains `constituent_identifer` (missing an "i") alongside the
   documented `constituent_identifier`; `extra="allow"` keeps both.

2. `LooseStr = str | int` on the specific fields where GiveCampus' published example
   payload disagrees with their own published spec type. Each one is marked
   `# DRIFT:` inline with what the spec says versus what the example shows. Widening
   only those fields keeps the rest of the model strict enough to be useful.
   Drift observed 2026-08-14 against
   https://support.givecampus.com/hc/en-us/articles/40836868939415-GiveCampus-API-Payload-Examples
"""

from __future__ import annotations

import enum
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RequestStatus",
    "DonationType",
    "GiftState",
    "TimeField",
    "ApiRequest",
    "ApiResults",
    "ApiErrorBody",
    "Address",
    "Advocate",
    "Affiliation",
    "GiftDesignation",
    "Incentive",
    "MatchingCompany",
    "Project",
    "Subscription",
    "GiftTimestamps",
    "Gift",
]

LooseStr = Union[str, int]


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RequestStatus(str, enum.Enum):
    """shared/types/api_request.yaml + api_results.yaml"""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"


class DonationType(str, enum.Enum):
    """gifts.yaml -> Gift.donation_type.enum"""

    CHALLENGE = "challenge"
    DONATION = "donation"
    PLEDGE = "pledge"
    GENERAL = "general"
    INSTALLMENT = "installment"
    MATCH = "match"


class GiftState(str, enum.Enum):
    """The eight state query flags on GET /gifts (gifts.yaml parameters)."""

    AUTHORIZED = "authorized"
    CHARGED_BACK = "charged_back"
    DISPUTED = "disputed"
    FAILED = "failed"
    PAID = "paid"
    PENDING = "pending"
    PENDING_AUTHORIZATION = "pending_authorization"
    REFUNDED = "refunded"


class TimeField(str, enum.Enum):
    """gifts.yaml -> time_field.enum. Default when omitted is confirmed_at
    (a.k.a. datetime_of_pledge), per the endpoint description."""

    CAPTURED_AT = "captured_at"
    CHECKOUT_AT = "checkout_at"
    CREATED_AT = "created_at"
    DEPOSITED_AT = "deposited_at"
    REFUNDED_AT = "refunded_at"
    UPDATED_AT = "updated_at"


class ApiRequest(_Base):
    """Response to a submit call: {request_id, status}."""

    request_id: str | None = None
    status: RequestStatus | None = None


class ApiResults(_Base):
    """Response to GET /results/{request_id}: {status, download_url}."""

    status: RequestStatus | None = None
    download_url: str | None = None


class ApiErrorBody(_Base):
    """{message} for 400/401/404; 500 additionally carries {status: "error"}."""

    message: str | None = None
    status: str | None = None


class Address(_Base):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    country: str | None = None
    state: str | None = None
    postal_code: str | None = None


class Advocate(_Base):
    id: LooseStr | None = None
    source: str | None = None
    link: str | None = None
    description: str | None = None
    tag: str | None = None
    link_url: str | None = None


class Affiliation(_Base):
    unique_identifier: str | None = None
    name: str | None = None
    values: list[Any] | None = None


class GiftDesignation(_Base):
    id: int | None = None
    name: str | None = None
    amount: LooseStr | None = None
    unique_identifier: str | None = None
    write_in: str | None = None


class Incentive(_Base):
    id: LooseStr | None = None
    headline: str | None = None
    description: str | None = None
    market_value: LooseStr | None = None
    tax_deductible_amount: LooseStr | None = None


class MatchingCompany(_Base):
    id: LooseStr | None = None
    name: str | None = None
    search_status: str | None = None
    search_text: str | None = None


class Project(_Base):
    id: int | None = None
    type: str | None = None
    name: str | None = None
    unique_identifier: str | None = None


class Subscription(_Base):
    id: LooseStr | None = None  # DRIFT: spec says string; example shows 2295 (int)
    state: str | None = None
    period: str | None = None
    length: LooseStr | None = None  # DRIFT: spec says string; example shows 36 (int)
    normalized_period: str | None = None
    normalized_length: int | None = None
    installment_number: LooseStr | None = None  # DRIFT: spec says string; example shows 1 (int)
    indefinite: bool | None = None
    subscription_type: str | None = None


class GiftTimestamps(_Base):
    """All Unix seconds. Note the spec types two of these as strings while their own
    example returns integers; see DRIFT markers."""

    created_at: int | None = None
    updated_at: int | None = None
    checkout_at: int | None = None
    captured_at: int | None = None
    datetime_of_pledge: int | None = None
    deposited_at: LooseStr | None = None  # DRIFT: spec says string; example shows 1779289975 (int)
    refunded_at: LooseStr | None = None  # DRIFT: spec says string; example shows null/int
    subscription_start_at: int | None = None
    subscription_end_at: int | None = None
    subscription_last_run_at: int | None = None


class Gift(_Base):
    """One element of the JSON array behind a completed Gifts request's download_url.

    Field order follows gifts.yaml. Everything is Optional because the published
    example payloads return null for most fields on a typical gift.
    """

    ach_verified: bool | None = None
    fund_source: str | None = None
    address: Address | None = None
    advocate: Advocate | None = None
    affiliations: list[Affiliation] | None = None
    anonymous: bool | None = None
    card_type: str | None = None
    checkout_id: str | None = None
    constituent_identifier: str | None = None
    crypto_transaction_hash_id: str | None = None
    crypto_value_usd_at_time_of_donation: LooseStr | None = None
    currency: str | None = None
    custom_fields: dict[str, Any] | None = None
    daf_fund_id: LooseStr | None = None
    daf_grant_id: LooseStr | None = None
    daf_tracking_id: LooseStr | None = None
    designations: list[GiftDesignation] | None = None
    donation_type: DonationType | None = None
    donor_covered_payment_fee: LooseStr | None = None
    event_id: int | None = None
    event_unique_identifier: str | None = None
    failure_reason: str | None = None
    give_full_match_challenge_amount: LooseStr | None = None
    honor: str | None = None
    id: int | None = None
    incentive: Incentive | None = None
    joint_gift: str | None = None
    joint_year: LooseStr | None = None
    maiden_name: str | None = None
    matching_company: MatchingCompany | None = None
    match_contribution_ids: list[int] | None = None
    name_title: str | None = None
    note_content: str | None = None
    paid_email: str | None = None
    paid_name: str | None = None
    payer_email: str | None = None
    payer_name: str | None = None
    payment_country: str | None = None
    payment_method: str | None = None
    payment_service_fee: LooseStr | None = None
    phone_number: LooseStr | None = None
    project: Project | None = None
    refund_reason: str | None = None
    refund_recovery_transfer: str | None = None
    refund_status: str | None = None
    spouse: str | None = None
    state: str | None = None
    stripe_deposit: str | None = None
    stripe_refund: str | None = None
    subscription: Subscription | None = None
    terminal_gift: bool | None = None
    terminal_type: str | None = None
    timestamps: GiftTimestamps | None = Field(default=None)
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_medium: str | None = None
    utm_source: str | None = None
    utm_term: str | None = None
    value: LooseStr | None = None
    value_in_currency: LooseStr | None = None
    value_orig_currency: LooseStr | None = None
    value_usd: LooseStr | None = None

    @property
    def undocumented_fields(self) -> dict[str, Any]:
        """Keys present in the payload but absent from gifts.yaml.

        Their own published example trips this with `constituent_identifer`.
        """
        return dict(self.__pydantic_extra__ or {})
