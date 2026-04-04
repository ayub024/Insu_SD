"""Policy master data generation with renewal option 2 behavior."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
import random
from typing import Any, Optional

from core.id_factory import IdFactory


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _to_month_start(value: str | date) -> date:
    if isinstance(value, date):
        return value.replace(day=1)
    if len(value) == 7:
        return date.fromisoformat(f"{value}-01")
    return date.fromisoformat(value).replace(day=1)


def _month_end(month_start: date) -> date:
    return date(month_start.year, month_start.month, monthrange(month_start.year, month_start.month)[1])


def _add_months(value: date, months: int) -> date:
    total_month = value.month - 1 + months
    year = value.year + total_month // 12
    month = total_month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


class PolicyMaster:
    """Create and renew Dim_Policy rows while preserving strict schema."""

    def __init__(self) -> None:
        self._ids = IdFactory()
        self._policy_number_counter = 1

        # Store strict Dim_Policy rows keyed by policy_key.
        self._policies: dict[str, dict] = {}

        # Metadata to support idempotent monthly renewals.
        self._last_renewed_month: dict[str, date] = {}

    def _new_policy_number(self, start_date: date) -> str:
        serial = self._policy_number_counter
        self._policy_number_counter += 1
        return f"PN-{start_date.year}-{serial:07d}"

    def _term_end_for_start(self, start_dt: date) -> date:
        # 12-month term ending the day before the same day next year.
        return _add_months(start_dt, 12) - timedelta(days=1)

    def create_new_policy(
        self,
        start_date: str | date,
        product: Any,
        segment: Any,
        customer: Any,
        channel: Any,
        broker: Any,
        underwriter: Any,
    ) -> dict:
        """Create a new policy row (strict Dim_Policy columns only)."""
        del product, segment, customer, channel, broker, underwriter

        start_dt = _to_date(start_date)
        end_dt = self._term_end_for_start(start_dt)

        row = {
            "policy_key": self._ids.next_policy_key(),
            "policy_number": self._new_policy_number(start_dt),
            "policy_status": "Active",
            "policy_start_date": start_dt.isoformat(),
            "policy_end_date": end_dt.isoformat(),
            "tenure_years": 1,
        }
        self._policies[row["policy_key"]] = row
        return row

    def process_renewals(self, month: str | date) -> list[dict]:
        """Renew policies expiring in the target month using option 1.

        Option 1:
        - new policy_key
        - new policy_start_date and policy_end_date
        - increment tenure_years
        - suffix policy_number
        """
        month_start = _to_month_start(month)
        month_last_day = _month_end(month_start)

        renewed_rows: list[dict] = []
        new_policies_to_add: dict[str, dict] = {}
        
        # Iterate over a list of values to avoid mutation error
        for row in list(self._policies.values()):
            policy_key = row["policy_key"]
            if row["policy_status"] != "Active":
                continue

            current_end = _to_date(row["policy_end_date"])
            if not (month_start <= current_end <= month_last_day):
                continue

            # Idempotent renewals per month run.
            if self._last_renewed_month.get(policy_key) == month_start:
                continue

            # NEW: 80% Retention Rate to simulate realistic customer churn
            if random.random() > 0.80:
                row["policy_status"] = "Lapsed"
                continue

            new_start = current_end + timedelta(days=1)
            new_end = _add_months(new_start, 12) - timedelta(days=1)
            tenure = int(row.get("tenure_years", 1))
            new_tenure = tenure + 1

            # Format Policy Key: POL0000001-01
            base_key = row["policy_key"].split("-")[0]
            new_key = f"{base_key}-{new_tenure-1:02d}"

            new_row = dict(row)
            new_row["policy_key"] = new_key
            new_row["policy_start_date"] = new_start.isoformat()
            new_row["policy_end_date"] = new_end.isoformat()
            new_row["tenure_years"] = new_tenure
            new_row["policy_status"] = "Active"

            new_policies_to_add[new_row["policy_key"]] = new_row
            
            # Append prior key purely for main.py enrichment pipeline
            ret_row = dict(new_row)
            ret_row["prior_policy_key"] = policy_key
            renewed_rows.append(ret_row)

            self._last_renewed_month[policy_key] = month_start

        # Safely add the new rows now that iteration is done
        self._policies.update(new_policies_to_add)

        return renewed_rows

    def expire_past_policies(self, month: str | date) -> None:
        """Mark policies as Expired if their policy_end_date has passed."""
        month_start = _to_month_start(month)
        for row in self._policies.values():
            if row["policy_status"] == "Active":
                end_dt = _to_date(row["policy_end_date"])
                if end_dt < month_start:
                    row["policy_status"] = "Expired"


_default_master: Optional[PolicyMaster] = None


def _instance() -> PolicyMaster:
    global _default_master
    if _default_master is None:
        _default_master = PolicyMaster()
    return _default_master


def create_new_policy(
    start_date: str | date,
    product: Any,
    segment: Any,
    customer: Any,
    channel: Any,
    broker: Any,
    underwriter: Any,
) -> dict:
    return _instance().create_new_policy(
        start_date=start_date,
        product=product,
        segment=segment,
        customer=customer,
        channel=channel,
        broker=broker,
        underwriter=underwriter,
    )


def process_renewals(month: str | date) -> list[dict]:
    return _instance().process_renewals(month)

def expire_past_policies(month: str | date) -> None:
    _instance().expire_past_policies(month)
