"""Build event-grain policy transaction fact rows.

The new grain is one row per Policy x Transaction Event x Event Date.  A policy
term starts with underwriting events, then emits monthly billing collections.
Claim rows are added in parallel when a real claim occurs.
"""

from __future__ import annotations
from functools import lru_cache

from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from generators.cat_engine import generate_cat_ibnr_rows
from generators.financial_generator import generate_financials

_FACT_COLUMNS = [
    "transaction_id",
    "transaction_domain",
    "transaction_type",
    "policy_key",
    "date_key",
    "policy_issue_date",
    "product_key",
    "segment_key",
    "underwriter_key",
    "broker_key",
    "customer_key",
    "channel_key",
    "claim_key",
    "pricing_decision_type",
    "quoted_price",
    "bind_price",
    "indicated_premium",
    "expected_loss",
    "underwriting_expense",
    "other_expense",
    "acquisition_expense",
    "gross_written_premium",
    "premium_collected_amount",
    "ibnr_amount",
    "recoveries_amount",
    "ceded_premium",
    "reinsurance_recovery",
    "incurred_claim_amount",
    "paid_claim_amount",
    "renewal_flag",
]


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load event builder config") from exc

    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_key(value: str | date) -> int:
    return int(_to_date(value).strftime("%Y%m%d"))


def _add_months(value: date, months: int) -> date:
    total_month = value.month - 1 + months
    year = value.year + total_month // 12
    month = total_month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _counter(world_state: dict[str, Any], key: str, start: int = 1) -> int:
    value = int(world_state.get(key, start))
    world_state[key] = value + 1
    return value


def _transaction_id(world_state: dict[str, Any]) -> str:
    return f"TXN{_counter(world_state, 'transaction_event_counter'):010d}"


def _claim_key(world_state: dict[str, Any], policy_key: str, claim_index: Any, claim_date: Any) -> int:
    stable_key = f"{policy_key}|{claim_index}|{claim_date}"
    mapping = world_state.setdefault("claim_key_by_stable_ref", {})
    if stable_key not in mapping:
        mapping[stable_key] = _counter(world_state, "claim_key_counter", start=1)
    return int(mapping[stable_key])


def _event_offset_days(event_type: str, claim_key: int, world_state: dict[str, Any]) -> int:
    cfg = _load_yaml("config/claim_event_workflow.yaml").get("claim_event_workflow", {})
    offsets = cfg.get("timing_offsets_days", {})
    rule = offsets.get(event_type, {"min": 0, "max": 0})
    min_days = int(rule.get("min", 0))
    max_days = int(rule.get("max", min_days))
    if max_days <= min_days:
        return min_days

    rng_manager = world_state.get("rng_manager")
    if rng_manager is not None:
        return int(rng_manager.randint(min_days, max_days + 1, entity_seed=f"{claim_key}|{event_type}|offset"))

    import random

    return random.randint(min_days, max_days)


def _event_occurs(probability: float, stable_ref: str, world_state: dict[str, Any]) -> bool:
    probability = max(0.0, min(float(probability), 1.0))
    rng_manager = world_state.get("rng_manager")
    if rng_manager is not None:
        draw = float(rng_manager.uniform(0.0, 1.0, entity_seed=stable_ref))
    else:
        import random

        draw = random.random()
    return draw < probability


def _uniform_float(min_value: float, max_value: float, stable_ref: str, world_state: dict[str, Any]) -> float:
    if max_value <= min_value:
        return float(min_value)
    rng_manager = world_state.get("rng_manager")
    if rng_manager is not None:
        return float(rng_manager.uniform(min_value, max_value, entity_seed=stable_ref))

    import random

    return float(random.uniform(min_value, max_value))


def _pricing_decision_type(policy: dict[str, Any], world_state: dict[str, Any]) -> str:
    existing = policy.get("pricing_decision_type")
    if existing:
        return str(existing)

    cfg = _load_yaml("config/underwriting_workflow.yaml").get("underwriting_workflow", {})
    weights = cfg.get("pricing_decision_type_distribution", {"Automated": 0.65, "Manual": 0.35})
    automated_weight = float(weights.get("Automated", 0.65))
    manual_weight = float(weights.get("Manual", 0.35))
    threshold = automated_weight / max(automated_weight + manual_weight, 1e-9)

    rng_manager = world_state.get("rng_manager")
    if rng_manager is not None:
        draw = float(rng_manager.uniform(0.0, 1.0, entity_seed=f"{policy['policy_key']}|pricing"))
    else:
        import random

        draw = random.random()
    return "Automated" if draw < threshold else "Manual"


def _expense_components(policy: dict[str, Any], pricing_decision_type: str) -> tuple[float, float, float]:
    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    is_renewal = bool(policy.get("renewal_flag", False))
    cfg = _load_yaml("config/expense_rules.yaml").get("expense_rules", {})
    base = cfg.get("new_business", {})
    renewal = cfg.get("renewal", {})
    pricing_adj = cfg.get("pricing_decision_type_adjustment", {}).get(pricing_decision_type, {})

    uw_range = base.get("underwriting_expense_pct_of_gwp", {"min": 0.01, "max": 0.04})
    acq_range = base.get("acquisition_expense_pct_of_gwp", {"min": 0.05, "max": 0.18})
    other_range = base.get("other_expense_pct_of_gwp", {"min": 0.005, "max": 0.03})

    uw_pct = (float(uw_range["min"]) + float(uw_range["max"])) / 2.0
    acq_pct = (float(acq_range["min"]) + float(acq_range["max"])) / 2.0
    other_pct = (float(other_range["min"]) + float(other_range["max"])) / 2.0

    uw = gwp * uw_pct * float(pricing_adj.get("underwriting_expense_multiplier", 1.0))
    acq = gwp * acq_pct
    other = gwp * other_pct

    if is_renewal:
        uw *= float(renewal.get("underwriting_expense_multiplier", 0.60))
        acq *= float(renewal.get("acquisition_expense_multiplier", 0.40))
        other *= float(renewal.get("other_expense_multiplier", 0.60))

    return round(uw, 2), round(other, 2), round(acq, 2)


def _base_row(
    policy: dict[str, Any],
    world_state: dict[str, Any],
    transaction_domain: str,
    transaction_type: str,
    event_date: date,
    claim_key: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "transaction_id": _transaction_id(world_state),
        "transaction_domain": transaction_domain,
        "transaction_type": transaction_type,
        "policy_key": policy["policy_key"],
        "date_key": _date_key(event_date),
        "policy_issue_date": (_to_date(policy.get("policy_start_date", event_date)) - timedelta(days=5)).isoformat(),
        "product_key": policy.get("product_key", ""),
        "segment_key": policy.get("segment_key", ""),
        "underwriter_key": policy.get("underwriter_key", ""),
        "broker_key": policy.get("broker_key"),
        "customer_key": policy.get("customer_key", ""),
        "channel_key": policy.get("channel_key", ""),
        "claim_key": claim_key,
        "pricing_decision_type": _pricing_decision_type(policy, world_state),
        "quoted_price": 0.0,
        "bind_price": 0.0,
        "indicated_premium": 0.0,
        "expected_loss": 0.0,
        "underwriting_expense": 0.0,
        "other_expense": 0.0,
        "acquisition_expense": 0.0,
        "gross_written_premium": 0.0,
        "premium_collected_amount": 0.0,
        "ibnr_amount": 0.0,
        "recoveries_amount": 0.0,
        "ceded_premium": 0.0,
        "reinsurance_recovery": 0.0,
        "incurred_claim_amount": 0.0,
        "paid_claim_amount": 0.0,
        "renewal_flag": bool(policy.get("renewal_flag", False)),
    }


def _append_underwriting_events(policy: dict[str, Any], world_state: dict[str, Any], rows: list[dict]) -> None:
    start_dt = _to_date(policy["policy_start_date"])
    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    pricing_type = _pricing_decision_type(policy, world_state)
    underwriting_expense, other_expense, acquisition_expense = _expense_components(policy, pricing_type)
    indicated = round(gwp * 1.04, 2)
    quoted = round(gwp * 1.02, 2)
    expected_loss = round(gwp * 0.62, 2)
    is_renewal = bool(policy.get("renewal_flag", False))

    quote_type = "Renewal Quote Created" if is_renewal else "Quote Created"
    approved_type = "Renewal Quote Approved" if is_renewal else "Quote Approved"
    bound_type = "Renewal Policy Bound" if is_renewal else "Policy Bound"
    issued_type = "Renewal Policy Issued" if is_renewal else "Policy Issued"

    quote = _base_row(policy, world_state, "Underwriting", quote_type, start_dt - timedelta(days=15))
    quote.update(
        {
            "quoted_price": quoted,
            "indicated_premium": indicated,
            "expected_loss": expected_loss,
            "underwriting_expense": round(underwriting_expense * 0.30, 2),
        }
    )
    rows.append(quote)

    approved = _base_row(policy, world_state, "Underwriting", approved_type, start_dt - timedelta(days=10))
    approved.update(
        {
            "quoted_price": quoted,
            "indicated_premium": indicated,
            "expected_loss": expected_loss,
            "underwriting_expense": round(underwriting_expense * 0.20, 2),
        }
    )
    rows.append(approved)

    bound = _base_row(policy, world_state, "Underwriting", bound_type, start_dt - timedelta(days=5))
    bound.update(
        {
            "quoted_price": quoted,
            "bind_price": gwp,
            "indicated_premium": indicated,
            "expected_loss": expected_loss,
            "underwriting_expense": round(underwriting_expense * 0.50, 2),
            "other_expense": other_expense,
            "acquisition_expense": acquisition_expense,
            "gross_written_premium": gwp,
        }
    )
    rows.append(bound)




def _append_billing_events(policy: dict[str, Any], world_state: dict[str, Any], rows: list[dict]) -> None:
    cfg = _load_yaml("config/billing_schedule.yaml").get("billing_schedule", {})
    # Trait override: scenario_engine may have set billing_installments on the policy.
    installments = int(policy.get("billing_installments") or cfg.get("default_installments", 12))
    decimals = int(cfg.get("rounding", {}).get("decimals", 2))
    start_dt = _to_date(policy["policy_start_date"])
    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    installment_amount = round(gwp / installments, decimals)
    running_total = 0.0

    # Lapse trait: how many payments are collected before the policy lapses?
    lapse_type = str(policy.get("lapse_type") or "")
    lapse_after_n = int(policy.get("lapse_after_n") or 0)
    # early lapse = 0 payments; mid lapse = lapse_after_n payments
    max_collections = 0 if lapse_type == "early" else (lapse_after_n if lapse_type == "mid" else installments)



    month_step = 12 // installments if installments in (1, 2, 3, 4, 6, 12) else 1
    collections_made = 0
    for month_idx in range(installments):
        event_date = _add_months(start_dt, month_idx * month_step)

        # Lapse cutoff: stop collecting if we have reached the lapse limit.
        if lapse_type in ("early", "mid") and collections_made >= max_collections:
            break

        # Legacy lifecycle plan cutoff (cancellation/reinstatement path).
        lifecycle_plan = _policy_lifecycle_plan(policy, world_state)
        termination_date = lifecycle_plan.get("termination_date")
        reinstatement_date = lifecycle_plan.get("reinstatement_date")
        if termination_date and event_date >= termination_date and not reinstatement_date:
            break

        is_final = (month_idx == installments - 1) or (lapse_type == "mid" and collections_made == max_collections - 1)
        amount = round(gwp - running_total, decimals) if is_final else installment_amount
        event_type = "Premium Collected"
        running_total = round(running_total + amount, decimals)
        collections_made += 1

        collected = _base_row(policy, world_state, "Billing", event_type, event_date)
        collected["premium_collected_amount"] = amount
        rows.append(collected)


def _reinsurance_applies(policy: dict[str, Any], rules: dict[str, Any]) -> bool:
    lob = str(policy.get("line_of_business", ""))
    product_name = str(policy.get("product_name", ""))
    applicable = {str(v) for v in rules.get("applicable_lob", [])}
    if not applicable:
        return False
    return lob in applicable or any(token in lob or token in product_name for token in applicable)


def _append_reinsurance_ceded_event(policy: dict[str, Any], world_state: dict[str, Any], rows: list[dict]) -> None:
    cfg = _load_yaml("config/reinsurance_rules.yaml").get("reinsurance_rules", {})
    if not bool(cfg.get("enabled", False)):
        return

    ceded_rules = cfg.get("ceded_premium", {})
    if not _reinsurance_applies(policy, ceded_rules):
        return

    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    min_pct = float(ceded_rules.get("min_pct_of_gwp", 0.05))
    max_pct = float(ceded_rules.get("max_pct_of_gwp", 0.25))
    ceded_pct = _uniform_float(min_pct, max_pct, f"{policy['policy_key']}|ceded_pct", world_state)
    event_offset = int(ceded_rules.get("event_offset_days", 4))
    event_type = str(ceded_rules.get("event_type", "Ceded Premium Booked"))
    event_date = _to_date(policy["policy_start_date"]) + timedelta(days=event_offset)

    row = _base_row(policy, world_state, "Reinsurance", event_type, event_date)
    row["ceded_premium"] = round(gwp * ceded_pct, 2)
    rows.append(row)


def _append_reinsurance_claim_events(
    policy: dict[str, Any],
    world_state: dict[str, Any],
    rows: list[dict],
    claim_key: int,
    claim_dt: date,
    incurred: float,
) -> None:
    cfg = _load_yaml("config/reinsurance_rules.yaml").get("reinsurance_rules", {})
    if not bool(cfg.get("enabled", False)):
        return

    recovery_rules = cfg.get("reinsurance_recovery", {})
    threshold = float(recovery_rules.get("trigger_on_claim_threshold", 50000.0))
    if incurred < threshold:
        return

    min_pct = float(recovery_rules.get("min_pct_of_incurred", 0.10))
    max_pct = float(recovery_rules.get("max_pct_of_incurred", 0.40))
    recovery_pct = _uniform_float(min_pct, max_pct, f"{claim_key}|reinsurance_recovery_pct", world_state)
    recovery_amount = round(incurred * recovery_pct, 2)

    estimated_date = claim_dt + timedelta(days=int(recovery_rules.get("estimated_offset_days", 45)))
    received_date = claim_dt + timedelta(days=int(recovery_rules.get("received_offset_days", 95)))

    estimated = _base_row(
        policy,
        world_state,
        "Reinsurance",
        str(recovery_rules.get("estimated_event_type", "Reinsurance Recovery Estimated")),
        estimated_date,
        claim_key=claim_key,
    )
    rows.append(estimated)

    received = _base_row(
        policy,
        world_state,
        "Reinsurance",
        str(recovery_rules.get("received_event_type", "Reinsurance Recovery Received")),
        received_date,
        claim_key=claim_key,
    )
    received["reinsurance_recovery"] = recovery_amount
    rows.append(received)


def _policy_lifecycle_plan(policy: dict[str, Any], world_state: dict[str, Any]) -> dict[str, Any]:
    plan_by_policy = world_state.setdefault("policy_lifecycle_plan_by_policy_key", {})
    policy_key = str(policy["policy_key"])
    if policy_key in plan_by_policy:
        return plan_by_policy[policy_key]

    cfg = _load_yaml("config/policy_lifecycle_rules.yaml").get("policy_lifecycle_rules", {})
    start_dt = _to_date(policy["policy_start_date"])
    cancellation_probability = float(cfg.get("cancellation_probability", 0.0))
    lapse_probability = float(cfg.get("lapse_probability", 0.0))
    reinstatement_probability = float(cfg.get("reinstatement_probability", 0.0))

    plan: dict[str, Any] = {
        "termination_type": None,
        "termination_date": None,
        "reinstatement_date": None,
    }

    cancel_occurs = _event_occurs(cancellation_probability, f"{policy_key}|cancel", world_state)
    lapse_occurs = False if cancel_occurs else _event_occurs(lapse_probability, f"{policy_key}|lapse", world_state)

    if cancel_occurs or lapse_occurs:
        termination_name = "cancellation" if cancel_occurs else "lapse"
        termination_cfg = cfg.get(termination_name, {})
        min_month = int(termination_cfg.get("month_offset_min", 3))
        max_month = int(termination_cfg.get("month_offset_max", 10))
        month_offset = int(_uniform_float(min_month, max_month + 1, f"{policy_key}|{termination_name}_month", world_state))
        termination_date = _add_months(start_dt, month_offset)

        plan["termination_type"] = str(termination_cfg.get("transaction_type", "Policy Cancelled" if cancel_occurs else "Policy Lapsed"))
        plan["termination_date"] = termination_date

        if _event_occurs(reinstatement_probability, f"{policy_key}|reinstatement", world_state):
            reinstatement_cfg = cfg.get("reinstatement", {})
            delay_min = int(reinstatement_cfg.get("delay_days_min", 15))
            delay_max = int(reinstatement_cfg.get("delay_days_max", 45))
            delay_days = int(_uniform_float(delay_min, delay_max + 1, f"{policy_key}|reinstatement_delay", world_state))
            plan["reinstatement_date"] = termination_date + timedelta(days=delay_days)

    plan_by_policy[policy_key] = plan
    return plan


def _append_endorsement(
    policy: dict[str, Any],
    world_state: dict[str, Any],
    rows: list[dict],
    month_offset: int,
    direction: str,
    idx: int,
) -> None:
    """Emit the full endorsement sequence."""
    cfg = _load_yaml("config/policy_lifecycle_rules.yaml").get("policy_lifecycle_rules", {})
    endorsement_cfg = cfg.get("endorsement", {})
    start_dt = _to_date(policy["policy_start_date"])
    pk = str(policy["policy_key"])

    event_dt = _add_months(start_dt, month_offset)

    gwp = float(policy.get("gross_written_premium", 0.0) or 0.0)
    if direction == "increase":
        adj_pct = _uniform_float(0.01, float(endorsement_cfg.get("premium_adjustment_max_pct", 0.20)),
                                 f"{pk}|end_adj_{idx}", world_state)
    else:
        adj_pct = _uniform_float(float(endorsement_cfg.get("premium_adjustment_min_pct", -0.10)), -0.01,
                                 f"{pk}|end_adj_{idx}", world_state)
    adjustment_amount = round(gwp * adj_pct, 2)

    transaction_types = endorsement_cfg.get("transaction_types", {})

    endorsement = _base_row(policy, world_state, "Policy Servicing",
                            str(transaction_types.get("single", "Endorsement")), event_dt)
    
    endorsement["underwriting_expense"] = 125.0
    endorsement["other_expense"] = 125.0
    endorsement["quoted_price"] = adjustment_amount
    endorsement["bind_price"] = adjustment_amount
    endorsement["gross_written_premium"] = adjustment_amount
    endorsement["premium_collected_amount"] = adjustment_amount
    
    rows.append(endorsement)


def _append_policy_servicing_events(policy: dict[str, Any], world_state: dict[str, Any], rows: list[dict]) -> None:
    cfg = _load_yaml("config/policy_lifecycle_rules.yaml").get("policy_lifecycle_rules", {})
    if not bool(cfg.get("servicing_enabled", True)):
        return

    start_dt = _to_date(policy["policy_start_date"])
    end_dt = _to_date(policy["policy_end_date"])
    lifecycle_plan = _policy_lifecycle_plan(policy, world_state)

    # ------------------------------------------------------------------
    # Endorsements — driven by scenario_engine trait list.
    # Falls back to legacy probability-based single endorsement if the
    # trait key is absent (backward compat with policies generated without traits).
    # ------------------------------------------------------------------
    trait_endorsements: list[dict] = list(policy.get("endorsements") or [])

    if trait_endorsements:
        # Trait-driven multi-endorsement loop.
        for idx, end_spec in enumerate(trait_endorsements):
            _append_endorsement(
                policy, world_state, rows,
                month_offset=int(end_spec["month_offset"]),
                direction=str(end_spec.get("direction", "increase")),
                idx=idx,
            )
    else:
        # Legacy probability fallback (endorsement_probability from YAML).
        endorsement_probability = float(cfg.get("endorsement_probability", 0.0))
        if _event_occurs(endorsement_probability, f"{policy['policy_key']}|endorsement", world_state):
            endorsement_cfg = cfg.get("endorsement", {})
            min_month = int(endorsement_cfg.get("request_month_offset_min", 2))
            max_month = int(endorsement_cfg.get("request_month_offset_max", 8))
            mo = int(_uniform_float(min_month, max_month + 1, f"{policy['policy_key']}|endorsement_month", world_state))
            _append_endorsement(policy, world_state, rows, month_offset=mo, direction="increase", idx=0)

    # ------------------------------------------------------------------
    # Lapse event — trait-driven date, or legacy lifecycle plan.
    # ------------------------------------------------------------------
    lapse_type = str(policy.get("lapse_type") or "")
    if lapse_type in ("early", "mid"):
        lapse_after_n = int(policy.get("lapse_after_n") or 0)
        # Compute the lapse date from start + lapse_after_n months
        total_month = start_dt.month - 1 + lapse_after_n
        lapse_year = start_dt.year + total_month // 12
        lapse_month = total_month % 12 + 1
        lapse_day = min(start_dt.day, monthrange(lapse_year, lapse_month)[1])
        trait_lapse_dt = date(lapse_year, lapse_month, lapse_day)
        lapse_row = _base_row(policy, world_state, "Policy Servicing", "Policy Lapsed", trait_lapse_dt)
        lapse_row["other_expense"] = 50.0
        rows.append(lapse_row)
    else:
        # Legacy lifecycle plan (cancellation / reinstatement).
        termination_date = lifecycle_plan.get("termination_date")
        termination_type = lifecycle_plan.get("termination_type")
        reinstatement_date = lifecycle_plan.get("reinstatement_date")
        if termination_date and termination_type:
            termination = _base_row(policy, world_state, "Policy Servicing", str(termination_type), termination_date)
            termination["other_expense"] = 50.0
            rows.append(termination)
            if reinstatement_date:
                reinstatement_cfg = cfg.get("reinstatement", {})
                reinstatement = _base_row(
                    policy, world_state, "Policy Servicing",
                    str(reinstatement_cfg.get("transaction_type", "Policy Reinstated")), reinstatement_date,
                )
                reinstatement["other_expense"] = 75.0
                rows.append(reinstatement)

    if bool(cfg.get("emit_policy_expired_event", True)):
        rows.append(_base_row(policy, world_state, "Policy Servicing", "Policy Expired", end_dt))


def _claim_type_and_details(policy: dict[str, Any]) -> tuple[str, str]:
    product_name = str(policy.get("product_name", ""))
    line_of_business = str(policy.get("line_of_business", ""))
    product_text = f"{product_name} {line_of_business}".lower()
    if "auto" in product_text or "motor" in product_text:
        return "Vehicle Damage", "Motor vehicle damage claim"
    if "marine" in product_text:
        return "Cargo Loss", "Marine cargo loss or transit damage claim"
    if "business interruption" in product_text:
        return "Business Interruption", "Business interruption loss claim"
    if "liability" in product_text or "umbrella" in product_text:
        return "Liability", "Third-party liability claim"
    if "renters" in product_text or "condo" in product_text or "homeowners" in product_text:
        return "Property Damage", "Personal property damage claim"
    if "health" in product_text:
        return "Hospitalization", "Health hospitalization claim"
    if "property" in product_text or "building" in product_text or "home" in product_text:
        return "Property Damage", "Commercial property damage claim"
    return "Loss Event", "Policy claim event"


def _severity_from_incurred(incurred: float) -> str:
    cfg = _load_yaml("config/claim_financials.yaml").get("claim_financials", {})
    bands = cfg.get("severity_bands", {})
    for band in ["Low", "Medium", "High", "Catastrophic"]:
        rule = bands.get(band, {})
        max_incurred = rule.get("max_incurred")
        if max_incurred is None:
            return band
        if incurred <= float(max_incurred):
            return band
    return "Medium"


def _claim_dim_row(policy: dict[str, Any], claim: dict[str, Any], claim_key: int, incurred: float) -> dict[str, Any]:
    claim_type, details = _claim_type_and_details(policy)
    return {
        "claim_key": claim_key,
        "claim_type": claim_type,
        "claim_details": details,
        "claim_date": claim["claim_date"],
        "claim_event_region": policy.get("geography", policy.get("policy_region", "")),
        "event_severity": _severity_from_incurred(incurred),
    }


def _claim_expense(event_type: str, amount_basis: float = 0.0) -> float:
    cfg = _load_yaml("config/claim_financials.yaml").get("claim_financials", {})
    event_expense = cfg.get("event_expense", {})
    minimum_by_event = {
        "Claim Reported": "reported_min",
        "Claim Registered": "registered_min",
        "Claim Reserve Created": "reserve_min",
        "Claim Reserve Revised": "reserve_min",
        "Claim Incurred": "incurred_min",
        "Claim Approved": "approved_min",
        "Claim Paid - Partial": "paid_min",
        "Claim Paid - Final": "paid_min",
        "Claim Recovery Received": "paid_min",
        "Claim Closed": "closed_min",
        "Claim Reopened": "reserve_min",
    }
    key = minimum_by_event.get(event_type, "closed_min")
    minimum = float(event_expense.get(key, 50.0))
    return round(max(minimum, amount_basis * 0.005), 2)


def _append_claim_event(
    policy: dict[str, Any],
    world_state: dict[str, Any],
    rows: list[dict],
    claim_key: int,
    event_type: str,
    claim_dt: date,
    incurred_claim_amount: float = 0.0,
    paid_claim_amount: float = 0.0,
    ibnr_amount: float = 0.0,
    recoveries_amount: float = 0.0,
) -> None:
    event_date = claim_dt + timedelta(days=_event_offset_days(event_type, claim_key, world_state))
    row = _base_row(policy, world_state, "Claims", event_type, event_date, claim_key=claim_key)
    row["incurred_claim_amount"] = round(incurred_claim_amount, 2)
    row["paid_claim_amount"] = round(paid_claim_amount, 2)
    row["ibnr_amount"] = round(ibnr_amount, 2)
    row["recoveries_amount"] = round(recoveries_amount, 2)
    if any(v > 0 for v in [incurred_claim_amount, paid_claim_amount, ibnr_amount, recoveries_amount]) or event_type in {
        "Claim Reported",
        "Claim Registered",
        "Claim Approved",
        "Claim Closed",
        "Claim Reopened",
    }:
        row["other_expense"] = _claim_expense(event_type, max(incurred_claim_amount, paid_claim_amount, ibnr_amount))
    rows.append(row)


def _append_claim_events(
    policy: dict[str, Any],
    claim: dict[str, Any],
    world_state: dict[str, Any],
    rows: list[dict],
) -> None:
    if bool(claim.get("is_zero_claim") or claim.get("zero_claim")):
        return

    _product_name = str(policy.get("product_name", ""))
    _workflow_cfg = _load_yaml("config/claim_event_workflow.yaml").get("claim_event_workflow", {})
    _probs = _workflow_cfg.get("probabilities", {})
    _rare_probs = _probs.get("by_product", {}).get(_product_name, _probs.get("default", {}))

    def _rare_prob(key: str, default: float) -> float:
        return float(_rare_probs.get(key, default))

    claim_dt = _to_date(claim["claim_date"])
    ckey = _claim_key(world_state, str(policy["policy_key"]), claim.get("claim_index"), claim.get("claim_date"))

    financials = generate_financials(policy_obj=policy, claim_obj=claim, world_state=world_state)
    incurred = float(financials.get("incurred_claim_amount") or 0.0)
    paid = float(financials.get("paid_claim_amount") or 0.0)
    ibnr = float(financials.get("ibnr_amount") or 0.0)
    recoveries = float(financials.get("recoveries_amount") or 0.0)
    dim_claim_rows = world_state.setdefault("dim_claim_rows_by_key", {})
    dim_claim_rows[ckey] = _claim_dim_row(policy, claim, ckey, incurred)

    # Apply CAT severity multiplier if the claim is tagged as a CAT claim.
    cat_mult = float(claim.get("cat_severity_multiplier", 1.0))
    if cat_mult != 1.0:
        incurred = round(incurred * cat_mult, 2)
        paid = round(paid * cat_mult, 2)
        ibnr = round(ibnr * cat_mult, 2)
        recoveries = round(recoveries * cat_mult, 2)

    # ------------------------------------------------------------------
    # Common opening events for all workflow types.
    # ------------------------------------------------------------------
    _append_claim_event(policy, world_state, rows, ckey, "Claim Reported", claim_dt)
    _append_claim_event(policy, world_state, rows, ckey, "Claim Registered", claim_dt)
    _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Created", claim_dt, ibnr_amount=ibnr)
    _append_claim_event(policy, world_state, rows, ckey, "Claim Incurred", claim_dt, incurred_claim_amount=incurred, ibnr_amount=ibnr)
    _append_claim_event(policy, world_state, rows, ckey, "Claim Approved", claim_dt)

    # ------------------------------------------------------------------
    # Workflow branching driven by claim_workflow trait on the policy.
    # The trait is set by scenario_engine.apply_lifecycle_traits().
    # Fall back to 'standard' if no trait is present.
    # ------------------------------------------------------------------
    workflow = str(policy.get("claim_workflow") or "standard")

    if workflow == "open":
        # Open claim: reserve raised, no payment yet.  Just a reserve revision.
        reserve_revision_probability = _rare_prob("reserve_revision_probability", 0.20)
        if _event_occurs(reserve_revision_probability, f"{ckey}|reserve_revision", world_state):
            _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", claim_dt,
                                ibnr_amount=round(ibnr * 0.75, 2))
        # No Claim Closed — policy is still open.

    elif workflow == "recoveries":
        # Recoveries: standard payment path + a recovery receipt.
        reserve_revision_probability = _rare_prob("reserve_revision_probability", 0.20)
        if _event_occurs(reserve_revision_probability, f"{ckey}|reserve_revision", world_state):
            _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", claim_dt,
                                ibnr_amount=round(ibnr * 0.75, 2))
        financial_cfg = _load_yaml("config/claim_financials.yaml").get("claim_financials", {})
        paid_pattern = financial_cfg.get("paid_pattern", {})
        partial_probability = float(paid_pattern.get("partial_payment_probability", 0.70))
        min_partial = float(paid_pattern.get("partial_payment_min_pct", 0.40))
        max_partial = float(paid_pattern.get("partial_payment_max_pct", 0.75))
        rng_manager = world_state.get("rng_manager")
        partial_pct = float(rng_manager.uniform(min_partial, max_partial, entity_seed=f"{ckey}|partial_pct")) \
            if rng_manager else (min_partial + max_partial) / 2.0
        has_partial = paid > 0 and _event_occurs(partial_probability, f"{ckey}|partial_paid", world_state)
        partial_paid = round(paid * partial_pct, 2) if has_partial else 0.0
        final_paid = round(paid - partial_paid, 2)
        if partial_paid > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Partial", claim_dt, paid_claim_amount=partial_paid)
        if final_paid > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Final", claim_dt, paid_claim_amount=final_paid)
        # Recovery receipt — always for this workflow type.
        if recoveries > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Recovery Received", claim_dt, recoveries_amount=recoveries)
        _append_reinsurance_claim_events(policy, world_state, rows, ckey, claim_dt, incurred)
        _append_claim_event(policy, world_state, rows, ckey, "Claim Closed", claim_dt)

    elif workflow == "long_tail":
        # Long-tail: two Reserve → Pay cycles, then closed.
        # Cycle 1 (at claim date).
        _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", claim_dt,
                            ibnr_amount=round(ibnr * 0.80, 2))
        cycle1_partial = round(paid * 0.40, 2)
        cycle1_recovery = round(recoveries * 0.50, 2)
        _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Partial", claim_dt, paid_claim_amount=cycle1_partial)
        if cycle1_recovery > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Recovery Received", claim_dt, recoveries_amount=cycle1_recovery)
        # Cycle 2 (~90 days later).
        cycle2_dt = claim_dt + timedelta(days=90)
        _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", cycle2_dt,
                            ibnr_amount=round(ibnr * 0.30, 2))
        cycle2_paid = round(paid - cycle1_partial, 2)
        cycle2_recovery = round(recoveries - cycle1_recovery, 2)
        _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Final", cycle2_dt, paid_claim_amount=cycle2_paid)
        if cycle2_recovery > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Recovery Received", cycle2_dt, recoveries_amount=cycle2_recovery)
        _append_reinsurance_claim_events(policy, world_state, rows, ckey, claim_dt, incurred)
        _append_claim_event(policy, world_state, rows, ckey, "Claim Closed", cycle2_dt)

    else:
        # Standard (default): linear payment path.
        reserve_revision_probability = _rare_prob("reserve_revision_probability", 0.20)
        if _event_occurs(reserve_revision_probability, f"{ckey}|reserve_revision", world_state):
            _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", claim_dt,
                                ibnr_amount=round(ibnr * 0.75, 2))
        financial_cfg = _load_yaml("config/claim_financials.yaml").get("claim_financials", {})
        paid_pattern = financial_cfg.get("paid_pattern", {})
        partial_probability = float(paid_pattern.get("partial_payment_probability", 0.70))
        min_partial = float(paid_pattern.get("partial_payment_min_pct", 0.40))
        max_partial = float(paid_pattern.get("partial_payment_max_pct", 0.75))
        rng_manager = world_state.get("rng_manager")
        partial_pct = float(rng_manager.uniform(min_partial, max_partial, entity_seed=f"{ckey}|partial_pct")) \
            if rng_manager else (min_partial + max_partial) / 2.0
        has_partial = paid > 0 and _event_occurs(partial_probability, f"{ckey}|partial_paid", world_state)
        partial_paid = round(paid * partial_pct, 2) if has_partial else 0.0
        final_paid = round(paid - partial_paid, 2)
        if partial_paid > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Partial", claim_dt, paid_claim_amount=partial_paid)
        if final_paid > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Paid - Final", claim_dt, paid_claim_amount=final_paid)
        if recoveries > 0:
            _append_claim_event(policy, world_state, rows, ckey, "Claim Recovery Received", claim_dt, recoveries_amount=recoveries)
        _append_reinsurance_claim_events(policy, world_state, rows, ckey, claim_dt, incurred)
        reopened_probability = _rare_prob("reopened_claim_probability", 0.03)
        if _event_occurs(reopened_probability, f"{ckey}|reopened", world_state):
            _append_claim_event(policy, world_state, rows, ckey, "Claim Reopened", claim_dt)
            _append_claim_event(policy, world_state, rows, ckey, "Claim Reserve Revised", claim_dt,
                                ibnr_amount=round(ibnr * 0.50, 2))
        _append_claim_event(policy, world_state, rows, ckey, "Claim Closed", claim_dt)


def build_policy_transaction_event_rows(
    policy_rows: list[dict],
    claim_rows: list[dict],
    world_state: Optional[dict[str, Any]] = None,
) -> list[dict]:
    if world_state is None:
        world_state = {}

    claims_by_policy: dict[str, list[dict]] = {}
    for claim in claim_rows:
        claims_by_policy.setdefault(str(claim["policy_key"]), []).append(claim)

    rows: list[dict] = []
    for policy in policy_rows:
        _append_underwriting_events(policy, world_state, rows)
        _append_reinsurance_ceded_event(policy, world_state, rows)
        _append_billing_events(policy, world_state, rows)
        for claim in claims_by_policy.get(str(policy["policy_key"]), []):
            _append_claim_events(policy, claim, world_state, rows)
        _append_policy_servicing_events(policy, world_state, rows)

        # CAT IBNR rows — one per applicable CAT event, independent of reported claims.
        cat_rows = generate_cat_ibnr_rows(
            policy=policy,
            world_state=world_state,
            base_row_fn=_base_row,
            transaction_id_fn=_transaction_id,
        )
        # Strip extra CAT metadata columns before appending (not in _FACT_COLUMNS).
        for cr in cat_rows:
            cr.pop("cat_event_id", None)
            cr.pop("cat_event_name", None)
            cr.pop("cat_peril", None)
            rows.append(cr)

    rows.sort(key=lambda r: (r["date_key"], str(r["policy_key"]), str(r["transaction_id"])))

    for row in rows:
        if list(row.keys()) != _FACT_COLUMNS:
            raise ValueError("FACT_POLICY_TRANSACTION_EVENT row does not match strict schema columns")
    return rows
