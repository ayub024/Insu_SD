"""Monthly policy generation based on growth targets and assignment rules."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.growth_engine import (
    get_new_broker_target,
    get_new_policy_target,
    get_new_underwriter_target,
)
from core.random_manager import RandomManager
from generators.broker_assignment import assign_broker, choose_channel
from generators.underwriter_assignment import assign_underwriter
import master_data.broker_master as broker_master
from master_data.customer_master import create_customer_for_policy
from master_data.policy_master import create_new_policy
from master_data.underwriter_master import get_active_underwriters, hire_underwriters, init_underwriters

# Product claim-mix percentages from SPEC (used as proxy for policy mix).
_PRODUCT_MIX = [
    ("Building & Contents", "Commercial Property", 15),
    ("Business Interruption", "Commercial Property", 9),
    ("Inland Marine", "Commercial Property", 6),
    ("General Liability", "Commercial Casualty", 12),
    ("Commercial Auto Liability", "Commercial Casualty", 8),
    ("Umbrella / Excess", "Commercial Casualty", 5),
    ("Homeowners", "Personal Property", 18),
    ("Renters", "Personal Property", 4),
    ("Condo", "Personal Property", 3),
    ("Personal Auto", "Personal Casualty", 15),
    ("Personal Umbrella", "Personal Casualty", 3),
    ("Motorcycle / Recreational", "Personal Casualty", 2),
]


def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load generator config") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _parse_month(month_value: str | date) -> date:
    if isinstance(month_value, date):
        return month_value.replace(day=1)
    if len(month_value) == 7:
        return date.fromisoformat(f"{month_value}-01")
    return date.fromisoformat(month_value).replace(day=1)


def _rng_manager(world_state: dict[str, Any]) -> RandomManager:
    manager = world_state.get("rng_manager")
    if manager is not None:
        return manager

    scenario = _load_yaml("config/scenario.yaml").get("scenario", {})
    raw_seed = world_state.get("random_seed")
    if raw_seed is None:
        raw_seed = scenario.get("random_seed", 20260215)
    seed = int(raw_seed) if raw_seed is not None else 20260215
    manager = RandomManager(seed)
    world_state["rng_manager"] = manager
    world_state["random_seed"] = seed
    return manager


def _geo_distribution() -> tuple[list[str], list[float]]:
    geo = _load_yaml("config/geo_distribution.yaml").get("geo_distribution", {})
    regions = ["Midwest", "Southeast", "Northeast", "Southwest", "West"]
    if set(geo.keys()) != set(regions):
        raise ValueError("geo_distribution.yaml must define exactly the 5 required regions")
    return regions, [float(geo[r]) for r in regions]


def _gwp_ranges() -> dict[str, tuple[float, float]]:
    rows = _load_yaml("config/gwp_ranges.yaml").get("gwp_ranges", [])
    mapping: Dict[str, Tuple[float, float]] = {}
    for row in rows:
        mapping[str(row["product_name"])] = (float(row["min_gwp"]), float(row["max_gwp"]))
    return mapping


def _ensure_workforce(month: date, world_state: dict[str, Any]) -> None:
    month_key = month.strftime("%Y-%m")

    if not world_state.get("brokers_initialized", False):
        broker_master.init_brokers()
        world_state["brokers_initialized"] = True

    if not world_state.get("underwriters_initialized", False):
        init_underwriters("2022-01-01")
        world_state["underwriters_initialized"] = True

    broker_applied = world_state.setdefault("broker_growth_applied", set())
    if month_key not in broker_applied:
        broker_master.onboard_brokers(month_key, get_new_broker_target(month_key))
        broker_applied.add(month_key)

    uw_applied = world_state.setdefault("underwriter_growth_applied", set())
    if month_key not in uw_applied:
        hire_underwriters(month_key, get_new_underwriter_target(month_key))
        uw_applied.add(month_key)


def _choose_product_and_lob(rng: RandomManager, entity_seed: str) -> tuple[str, str]:
    labels = [f"{p}|{lob}" for p, lob, _ in _PRODUCT_MIX]
    weights = [w for _, _, w in _PRODUCT_MIX]
    chosen = str(rng.choice_weighted(labels, weights, entity_seed=entity_seed))
    product_name, lob = chosen.split("|", 1)
    return product_name, lob


def _choose_segment(rng: RandomManager, lob: str, entity_seed: str) -> str:
    if lob.startswith("Personal"):
        return "Retail"
    return str(rng.choice_weighted(["SME", "Enterprise"], [70, 30], entity_seed=entity_seed))


def _choose_geography(rng: RandomManager, entity_seed: str) -> str:
    regions, weights = _geo_distribution()
    return str(rng.choice_weighted(regions, weights, entity_seed=entity_seed))


def _month_random_start_date(rng: RandomManager, month: date, entity_seed: str) -> date:
    days_in_month = monthrange(month.year, month.month)[1]
    day = int(rng.randint(1, days_in_month + 1, entity_seed=entity_seed))
    return date(month.year, month.month, day)


def generate_monthly_policies(month: str | date, world_state: Optional[dict[str, Any]] = None) -> list[dict]:
    """Generate new policy objects for a month and update policy master.

    Rules applied:
    - Monthly count from growth engine policy targets.
    - Product sampled by claim-mix proxy from spec.
    - Segment mapping by personal/commercial lines.
    - Geography from config distribution.
    - Channel and broker assignment via broker assignment rules.
    - Underwriter assignment via scoring-based engine.
    - start_date random within month; end_date = start_date + 365 days.
    - new_policy_flag=True, renewal_flag=False.
    """
    if world_state is None:
        world_state = {}

    month_dt = _parse_month(month)
    month_key = month_dt.strftime("%Y-%m")

    rng = _rng_manager(world_state)
    _ensure_workforce(month_dt, world_state)

    target_new = int(get_new_policy_target(month_key))
    gwp_map = _gwp_ranges()

    active_uw = get_active_underwriters(month_key)
    if not active_uw:
        raise ValueError(f"No active underwriters available for {month_key}")

    policies: list[dict] = []
    for i in range(target_new):
        seed_base = f"{month_key}|{i}"

        product_name, lob = _choose_product_and_lob(rng, entity_seed=f"{seed_base}|product")
        segment = _choose_segment(rng, lob, entity_seed=f"{seed_base}|segment")
        geography = _choose_geography(rng, entity_seed=f"{seed_base}|geo")

        # Retry channel draw a few times if no eligible broker exists for the sampled channel/region.
        broker_key = None
        channel_type = "Direct"
        last_error: Optional[Exception] = None
        for _ in range(6):
            try:
                channel_type = choose_channel(month_key, rng)
                broker_key = assign_broker(
                    channel=channel_type,
                    region={"customer_geography": geography, "segment_geography": geography},
                    broker_master=broker_master,
                    month=month_key,
                )
                last_error = None
                break
            except ValueError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error

        min_gwp, max_gwp = gwp_map[product_name]
        gross_written_premium = float(
            rng.uniform(min_gwp, max_gwp, entity_seed=f"{seed_base}|gwp")
        )

        customer_pools = world_state.setdefault("customer_pools", {})
        segment_pool = customer_pools.setdefault((segment, geography), [])

        if segment == "Enterprise":
            reuse_prob = 0.80
        elif segment == "SME":
            reuse_prob = 0.65
        else: # Retail
            reuse_prob = 0.35

        if segment_pool and rng.uniform(0.0, 1.0, entity_seed=f"{seed_base}|reuse") < reuse_prob:
            customer_ref = str(rng._rng(entity_seed=f"{seed_base}|cust_choice").choice(segment_pool))
        else:
            customer_ref = f"{seed_base}|customer"
            segment_pool.append(customer_ref)

        customer = create_customer_for_policy(
            {
                "line_of_business": lob,
                "segment": segment,
                "segment_geography": geography,
                "month": month_key,
                "customer_ref": customer_ref,
            }
        )

        policy_ctx = {
            "month": month_key,
            "line_of_business": lob,
            "product_name": product_name,
            "customer_geography": geography,
            "segment_geography": geography,
            "gross_written_premium": gross_written_premium,
        }
        regional_underwriters = [
            u
            for u in active_uw
            if str(u.get("underwriter_region", u.get("region"))) == geography
        ]
        underwriter_pool = regional_underwriters if regional_underwriters else active_uw
        underwriter = assign_underwriter(policy_ctx, underwriter_pool, world_state)
        if underwriter not in active_uw:
            active_uw.append(underwriter)
        if str(underwriter.get("underwriter_region", underwriter.get("region"))) != geography:
            raise ValueError(
                "Underwriter region must align with customer/segment geography: "
                f"underwriter_region={underwriter.get('underwriter_region', underwriter.get('region'))!r}, "
                f"geography={geography!r}"
            )

        start_dt = _month_random_start_date(rng, month_dt, entity_seed=f"{seed_base}|start")
        end_dt = start_dt + timedelta(days=365)

        policy_row = create_new_policy(
            start_date=start_dt,
            product={
                "product_name": product_name,
                "line_of_business": lob,
                "gross_written_premium": gross_written_premium,
            },
            segment={"segment": segment, "geography": geography},
            customer=customer,
            channel={"channel_type": channel_type},
            broker={"broker_key": broker_key} if broker_key else None,
            underwriter=underwriter,
        )

        # Enforce generator rule for end date.
        policy_row["policy_end_date"] = end_dt.isoformat()

        policy_obj = {
            "policy_key": policy_row["policy_key"],
            "policy_number": policy_row["policy_number"],
            "policy_status": policy_row["policy_status"],
            "policy_start_date": policy_row["policy_start_date"],
            "policy_end_date": policy_row["policy_end_date"],
            "tenure_years": policy_row["tenure_years"],
            "product_name": product_name,
            "line_of_business": lob,
            "segment": segment,
            "customer_key": customer["customer_key"],
            "geography": geography,
            "channel_type": channel_type,
            "broker_key": broker_key,
            "underwriter_key": underwriter["underwriter_key"],
            "gross_written_premium": float(policy_ctx["gross_written_premium"]),
            "new_policy_flag": True,
            "renewal_flag": False,
        }
        policies.append(policy_obj)

    return policies
