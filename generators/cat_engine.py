"""CAT (Catastrophe) event engine.

Responsibilities:
- Load and cache cat_events.yaml.
- Determine which CAT events apply to a given policy
  (region, term overlap, LOB eligibility).
- Emit IBNR transaction rows for all in-scope policies.
- Expose the active CAT event for a claim date so the claim
  generator can tag claims with cat_event_id / cat_reason.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for cat_engine") from exc
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _get_cat_events() -> list[dict]:
    return list(_load_yaml("config/cat_events.yaml").get("cat_events", []))


def _policy_eligible(policy: dict[str, Any], event: dict) -> bool:
    """Return True if this policy qualifies for the given CAT event."""
    # Gate 1 – Region match
    policy_region = str(policy.get("geography", "")).strip()
    event_region = str(event.get("affected_region", "")).strip()
    if policy_region != event_region:
        return False

    # Gate 2 – Coverage period overlap
    event_dt = _to_date(event["event_date"])
    window_end = event_dt + timedelta(days=int(event.get("ibnr_window_days", 90)))
    policy_start = _to_date(policy["policy_start_date"])
    policy_end = _to_date(policy["policy_end_date"])
    # Policy must be active on or before window_end AND after event_date
    if policy_end < event_dt or policy_start > window_end:
        return False

    # Gate 3 – LOB eligibility
    policy_lob = str(policy.get("line_of_business", "")).strip()
    eligible_lobs = [str(lob).strip() for lob in event.get("eligible_lob", [])]
    if eligible_lobs and policy_lob not in eligible_lobs:
        return False

    return True


def get_applicable_cat_events(policy: dict[str, Any]) -> list[dict]:
    """Return all CAT events that apply to a given policy (may be empty)."""
    return [ev for ev in _get_cat_events() if _policy_eligible(policy, ev)]


def get_cat_event_for_claim(
    policy: dict[str, Any],
    claim_date: str | date,
) -> Optional[dict]:
    """Return the CAT event that covers a specific claim date, if any.

    Used by claim_generator to tag claims with cat_event_id / cat_reason.
    Returns the first matching event (events are non-overlapping by design).
    """
    claim_dt = _to_date(claim_date)
    for ev in _get_cat_events():
        if not _policy_eligible(policy, ev):
            continue
        ev_dt = _to_date(ev["event_date"])
        window_end = ev_dt + timedelta(days=int(ev.get("ibnr_window_days", 90)))
        if ev_dt <= claim_dt <= window_end:
            return ev
    return None


def generate_cat_ibnr_rows(
    policy: dict[str, Any],
    world_state: dict[str, Any],
    base_row_fn: Any,          # callable: (policy, ws, domain, type, date) -> dict
    transaction_id_fn: Any,    # callable: (ws) -> str
) -> list[dict]:
    """Emit one 'CAT IBNR Raised' row per applicable CAT event for a policy.

    The IBNR amount is estimated as a percentage of GWP, scaled by the
    event's severity_multiplier.  This represents the insurer's prudent
    reserve for as-yet-unreported losses from the catastrophe.

    We deliberately keep the amount conservative (3–8 % of GWP) because
    IBNR at this stage is an estimate — not an incurred loss.
    """
    events = get_applicable_cat_events(policy)
    if not events:
        return []

    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    rows: list[dict] = []

    rng_manager = world_state.get("rng_manager")
    pk = str(policy["policy_key"])

    for ev in events:
        ev_dt = _to_date(ev["event_date"])
        sev_mult = float(ev.get("severity_multiplier", 1.0))

        # IBNR estimate: 3–8 % of GWP * severity multiplier, capped at 25 % GWP
        if rng_manager is not None:
            base_pct = float(rng_manager.uniform(0.03, 0.08, entity_seed=f"{pk}|cat_ibnr_{ev['id']}"))
        else:
            base_pct = 0.05

        ibnr_amount = round(min(gwp * base_pct * sev_mult, gwp * 0.25), 2)

        row = base_row_fn(policy, world_state, "Claims", "CAT IBNR Raised", ev_dt)
        row["ibnr_amount"] = ibnr_amount
        # Store CAT metadata in the row for downstream dimension enrichment
        row["cat_event_id"] = str(ev["id"])
        row["cat_event_name"] = str(ev["name"])
        row["cat_peril"] = str(ev.get("peril", ""))
        rows.append(row)

    return rows
