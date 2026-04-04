"""Runtime world-state container for month-by-month synthetic generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


def _parse_month(value: str | date) -> str:
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    text = str(value)
    if len(text) >= 7:
        return text[:7]
    raise ValueError("Month must be a date or YYYY-MM/ YYYY-MM-DD string")


def _next_month(month_key: str) -> str:
    year = int(month_key[0:4])
    month = int(month_key[5:7])
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


@dataclass
class WorldState:
    """Mutable state shared across generators/validators during a run."""

    current_month: str = "2022-01"
    active_policies: set[str] = field(default_factory=set)
    policy_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    broker_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    underwriter_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    underwriter_workloads_per_month: dict[str, dict[str, int]] = field(default_factory=dict)
    claim_registry: list[dict[str, Any]] = field(default_factory=list)

    # Dict-style compatibility for existing modules using world_state like a mapping.
    extra: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            self.extra[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra.get(key, default)

    def setdefault(self, key: str, default: Any) -> Any:
        if hasattr(self, key):
            current = getattr(self, key)
            if current is None:
                setattr(self, key, default)
                return default
            return current
        return self.extra.setdefault(key, default)

    def advance_month(self) -> str:
        """Advance current month by one and return the new YYYY-MM key."""
        self.current_month = _next_month(_parse_month(self.current_month))
        return self.current_month

    def register_policy(self, policy_row: dict[str, Any]) -> None:
        """Add/update policy in registry and active policy set."""
        policy_key = str(policy_row.get("policy_key", ""))
        if not policy_key:
            raise ValueError("policy_row must include policy_key")

        self.policy_registry[policy_key] = policy_row

        status = str(policy_row.get("policy_status", "Active"))
        if status == "Active":
            self.active_policies.add(policy_key)
        else:
            self.active_policies.discard(policy_key)

    def register_claim_row(self, claim_row: dict[str, Any]) -> None:
        """Append a claim-level fact candidate row to claim registry."""
        policy_key = str(claim_row.get("policy_key", ""))
        if not policy_key:
            raise ValueError("claim_row must include policy_key")
        self.claim_registry.append(claim_row)

    def update_workload(
        self,
        underwriter_key: str,
        month: str | date | None = None,
        increment: int = 1,
    ) -> int:
        """Increment and return monthly workload for an underwriter."""
        if increment < 0:
            raise ValueError("increment must be >= 0")

        month_key = _parse_month(month if month is not None else self.current_month)
        uw_key = str(underwriter_key)
        if not uw_key:
            raise ValueError("underwriter_key must be non-empty")

        monthly = self.underwriter_workloads_per_month.setdefault(month_key, {})
        monthly[uw_key] = int(monthly.get(uw_key, 0)) + int(increment)
        return monthly[uw_key]
