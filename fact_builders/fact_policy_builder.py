"""Fact_Policy builder with strict schema and claim-level grain."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from core.id_factory import IdFactory
from generators.financial_generator import generate_financials

_FACT_COLUMNS = [
    "id",
    "policy_key",
    "date_key",
    "product_key",
    "segment_key",
    "underwriter_key",
    "broker_key",
    "customer_key",
    "channel_key",
    "gross_written_premium",
    "ibnr_amount",
    "recoveries_amount",
    "ceded_premium",
    "reinsurance_recovery",
    "net_earned_premium",
    "incurred_claim_amount",
    "paid_claim_amount",
    "outstanding_reserve",
    "operating_expense",
    "acquisition_expense",
    "new_policy_flag",
    "renewal_flag",
]


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_key_from_claim_date(claim_date: str | date) -> int:
    dt = _to_date(claim_date)
    return int(dt.strftime("%Y%m%d"))


def _id_factory(world_state: dict[str, Any]) -> IdFactory:
    factory = world_state.get("fact_id_factory")
    if factory is not None:
        return factory
    factory = IdFactory()
    world_state["fact_id_factory"] = factory
    return factory


def _policy_index(policy_rows: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for row in policy_rows:
        key = str(row.get("policy_key", ""))
        if not key:
            raise ValueError("Each policy row must include policy_key")
        idx[key] = row
    return idx


def _is_zero_claim(policy_obj: dict, claim_obj: dict) -> bool:
    return bool(
        policy_obj.get("zero_claim_policy")
        or claim_obj.get("is_zero_claim")
        or claim_obj.get("zero_claim")
        or claim_obj.get("zero_claim_policy")
    )


def _required_policy_field(policy_obj: dict, field: str) -> Any:
    if field not in policy_obj:
        raise ValueError(f"Policy '{policy_obj.get('policy_key')}' missing required field '{field}'")
    return policy_obj[field]


def build_fact_policy_rows(
    policy_rows: list[dict],
    claim_rows: list[dict],
    world_state: Optional[dict[str, Any]] = None,
) -> list[dict]:
    """Build strict Fact_Policy rows from policies + claims.

    Required policy fields per row:
    - policy_key, product_key, segment_key, underwriter_key, broker_key,
      customer_key, channel_key, policy_start_date, policy_end_date

    Required claim fields per row:
    - policy_key, claim_date, claim_index
    """
    if world_state is None:
        world_state = {}

    pidx = _policy_index(policy_rows)
    ids = _id_factory(world_state)

    rows: list[dict] = []
    for claim in claim_rows:
        policy_key = str(claim.get("policy_key", ""))
        if not policy_key:
            raise ValueError("Each claim row must include policy_key")
        if policy_key not in pidx:
            raise ValueError(f"Claim references unknown policy_key '{policy_key}'")

        policy = pidx[policy_key]

        claim_date = claim.get("claim_date")
        if claim_date is None:
            raise ValueError(f"Claim for policy '{policy_key}' missing claim_date")

        claim_index = claim.get("claim_index")
        if claim_index is None:
            raise ValueError(f"Claim for policy '{policy_key}' missing claim_index")

        zero_claim = _is_zero_claim(policy, claim)
        fin_claim = dict(claim)
        if zero_claim:
            fin_claim["is_zero_claim"] = True

        financials = generate_financials(policy_obj=policy, claim_obj=fin_claim, world_state=world_state)

        # Explicit hard override for zero-claim policies.
        if zero_claim:
            financials["incurred_claim_amount"] = 0.0
            financials["paid_claim_amount"] = 0.0
            financials["outstanding_reserve"] = 0.0
            financials["ibnr_amount"] = 0.0
            financials["recoveries_amount"] = 0.0
            financials["reinsurance_recovery"] = 0.0

        row_id = ids.get_or_create_fact_id(f"{policy_key}|{claim_date}|{claim_index}")

        row = {
            "id": row_id,
            "policy_key": policy_key,
            "date_key": _date_key_from_claim_date(claim_date),
            "product_key": _required_policy_field(policy, "product_key"),
            "segment_key": _required_policy_field(policy, "segment_key"),
            "underwriter_key": _required_policy_field(policy, "underwriter_key"),
            "broker_key": policy.get("broker_key"),
            "customer_key": _required_policy_field(policy, "customer_key"),
            "channel_key": _required_policy_field(policy, "channel_key"),
            "gross_written_premium": financials["gross_written_premium"],
            "ibnr_amount": financials["ibnr_amount"],
            "recoveries_amount": financials["recoveries_amount"],
            "ceded_premium": financials["ceded_premium"],
            "reinsurance_recovery": financials["reinsurance_recovery"],
            "net_earned_premium": financials["net_earned_premium"],
            "incurred_claim_amount": financials["incurred_claim_amount"],
            "paid_claim_amount": financials["paid_claim_amount"],
            "outstanding_reserve": financials["outstanding_reserve"],
            "operating_expense": financials["operating_expense"],
            "acquisition_expense": financials["acquisition_expense"],
            "new_policy_flag": financials["new_policy_flag"],
            "renewal_flag": financials["renewal_flag"],
        }

        # Strict schema guard: no extra/missing columns.
        if list(row.keys()) != _FACT_COLUMNS:
            raise ValueError("Fact_Policy row does not match strict schema columns")

        rows.append(row)

    return rows
