"""Financial metric generation for Fact_Policy claim-level rows (Option A)."""

from __future__ import annotations
from functools import lru_cache

from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load financial configs") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _clamp(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def _rng(world_state: dict[str, Any], rng: Optional[np.random.Generator] = None) -> np.random.Generator:
    if rng is not None:
        return rng

    stored = world_state.get("financial_rng")
    if stored is not None:
        return stored

    seed = int(world_state.get("random_seed", 20260215))
    generated = np.random.default_rng(seed)
    world_state["financial_rng"] = generated
    return generated


def _gwp_map() -> dict[str, tuple[float, float]]:
    rows = _load_yaml("config/gwp_ranges.yaml").get("gwp_ranges", [])
    result: dict[str, tuple[float, float]] = {}
    for row in rows:
        result[str(row["product_name"])] = (float(row["min_gwp"]), float(row["max_gwp"]))
    return result


def _config_cache(world_state: dict[str, Any]) -> dict[str, Any]:
    """Load static financial configs once per run and reuse."""
    cache = world_state.get("financial_config_cache")
    if cache is not None:
        return cache

    cache = {
        "fin_cfg": _load_yaml("config/financial_assumptions.yaml").get("financial_assumptions", {}),
        "uw_rules": _load_yaml("config/underwriter_rules.yaml").get("underwriter_rules", {}),
        "gwp_ranges": _gwp_map(),
    }
    world_state["financial_config_cache"] = cache
    return cache


def _commercial_outlier_gwp(
    base_gwp: float,
    line_of_business: str,
    rng: np.random.Generator,
    underwriter_rules: dict,
) -> float:
    outlier_cfg = underwriter_rules.get("outlier_tail", {})
    high_cfg = underwriter_rules.get("high_premium_rule", {})

    threshold = float(high_cfg.get("gwp_threshold", 50000.0))
    if base_gwp > threshold:
        return base_gwp

    if not bool(outlier_cfg.get("enabled", False)):
        return base_gwp

    applies = {str(x) for x in outlier_cfg.get("applies_to_lob", [])}
    if line_of_business not in applies:
        return base_gwp

    prob = float(outlier_cfg.get("probability", 0.02))
    if rng.uniform(0.0, 1.0) >= prob:
        return base_gwp

    uplifted = float(rng.lognormal(mean=np.log(threshold + 10000.0), sigma=0.28))
    uplifted = _clamp(uplifted, threshold + 1.0, threshold * 2.3)
    return uplifted


def _policy_cache(world_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return world_state.setdefault("financial_policy_cache", {})


def _is_zero_claim(claim_obj: dict[str, Any]) -> bool:
    return bool(
        claim_obj.get("is_zero_claim")
        or claim_obj.get("zero_claim")
        or claim_obj.get("zero_claim_policy")
    )


def _round2(x: float) -> float:
    return float(round(float(x), 2))


def _nullify_zero(val: float | None) -> float | None:
    if val is None or val == 0.0:
        return None
    return val


def _default_severity_config() -> dict[str, dict[str, float]]:
    """Fallback severity params if config mapping is missing.

    mean/sigma are numpy lognormal parameters.
    """
    return {
        "Building & Contents": {"severity_mean": 8.30, "severity_sigma": 0.65, "max_pct_of_gwp_cap": 0.90},
        "Business Interruption": {"severity_mean": 8.95, "severity_sigma": 0.95, "max_pct_of_gwp_cap": 1.20},
        "Inland Marine": {"severity_mean": 7.90, "severity_sigma": 0.60, "max_pct_of_gwp_cap": 0.80},
        "General Liability": {"severity_mean": 8.55, "severity_sigma": 0.75, "max_pct_of_gwp_cap": 1.00},
        "Commercial Auto Liability": {"severity_mean": 8.70, "severity_sigma": 0.90, "max_pct_of_gwp_cap": 1.10},
        "Umbrella / Excess": {"severity_mean": 9.05, "severity_sigma": 1.00, "max_pct_of_gwp_cap": 1.35},
        "Homeowners": {"severity_mean": 7.35, "severity_sigma": 0.55, "max_pct_of_gwp_cap": 0.70},
        "Renters": {"severity_mean": 6.35, "severity_sigma": 0.45, "max_pct_of_gwp_cap": 0.45},
        "Condo": {"severity_mean": 6.65, "severity_sigma": 0.48, "max_pct_of_gwp_cap": 0.50},
        "Personal Auto": {"severity_mean": 7.45, "severity_sigma": 0.58, "max_pct_of_gwp_cap": 0.75},
        "Personal Umbrella": {"severity_mean": 6.90, "severity_sigma": 0.55, "max_pct_of_gwp_cap": 0.60},
        "Motorcycle / Recreational": {"severity_mean": 7.05, "severity_sigma": 0.57, "max_pct_of_gwp_cap": 0.65},
    }


def _severity_params(fin_cfg: dict, product_name: str) -> dict[str, float]:
    """Resolve per-product severity parameters from config with fallback defaults.

    Expected config path:
    financial_assumptions.claim_severity[product_name]:
      severity_mean
      severity_sigma
      max_pct_of_gwp_cap
    """
    configured = fin_cfg.get("claim_severity", {})
    defaults = _default_severity_config()

    if product_name in configured and isinstance(configured[product_name], dict):
        row = configured[product_name]
        return {
            "severity_mean": float(row.get("severity_mean", defaults.get(product_name, {}).get("severity_mean", 7.5))),
            "severity_sigma": float(row.get("severity_sigma", defaults.get(product_name, {}).get("severity_sigma", 0.6))),
            "max_pct_of_gwp_cap": float(row.get("max_pct_of_gwp_cap", defaults.get(product_name, {}).get("max_pct_of_gwp_cap", 0.8))),
        }

    return defaults.get(product_name, {"severity_mean": 7.5, "severity_sigma": 0.6, "max_pct_of_gwp_cap": 0.8})


def _product_group(fin_cfg: dict, product_name: str, line_of_business: str) -> str:
    group_map = fin_cfg.get("product_group_map", {})
    if isinstance(group_map, dict) and product_name in group_map:
        return str(group_map[product_name])

    name = product_name.lower()
    if "auto" in name or "motorcycle" in name or "recreational" in name:
        return "auto"
    if "liability" in name or "umbrella" in name or "excess" in name:
        return "liability_umbrella"
    if "property" in line_of_business.lower():
        return "property"
    return "liability_umbrella"


def _seasonality_multiplier(fin_cfg: dict, product_group: str, claim_month: int) -> float:
    by_group = fin_cfg.get("seasonality_factors_by_product_group", {})
    factors = by_group.get(product_group, {}) if isinstance(by_group, dict) else {}
    if not isinstance(factors, dict):
        return 1.0
    value = factors.get(claim_month, factors.get(str(claim_month), 1.0))
    return max(0.0, float(value))


def _region_multiplier(fin_cfg: dict, region: str, claim_month: int) -> float:
    region_cfg = fin_cfg.get("region_month_shock_factors", {})
    if not isinstance(region_cfg, dict):
        return 1.0
    month_map = region_cfg.get(region, {})
    if not isinstance(month_map, dict):
        return 1.0
    value = month_map.get(claim_month, month_map.get(str(claim_month), 1.0))
    return max(0.0, float(value))


def _year_trend_multiplier(fin_cfg: dict, claim_year: int) -> float:
    yoy = fin_cfg.get("severity_inflation_yoy", {})
    if isinstance(yoy, dict):
        base_year = int(yoy.get("base_year", 2022))
        annual_rate = float(yoy.get("annual_rate", 0.05))
    else:
        base_year = 2022
        annual_rate = float(yoy) if yoy is not None else 0.05

    years = max(0, int(claim_year) - base_year)
    return max(0.0, (1.0 + annual_rate) ** years)


def _cat_shock_multiplier(fin_cfg: dict, product_group: str, claim_year: int) -> float:
    shock_year = fin_cfg.get("cat_shock_year")
    if shock_year is None:
        return 1.0
    if int(claim_year) != int(shock_year):
        return 1.0
    multipliers = fin_cfg.get("cat_shock_multiplier_by_product_group", {})
    if not isinstance(multipliers, dict):
        return 1.0
    return max(0.0, float(multipliers.get(product_group, 1.0)))


def _is_personal_line(line_of_business: str) -> bool:
    return str(line_of_business).strip().startswith("Personal")


def generate_financials(
    policy_obj: dict[str, Any],
    claim_obj: dict[str, Any],
    world_state: Optional[dict[str, Any]] = None,
    rng: Optional[np.random.Generator] = None,
) -> dict[str, Any]:
    """Generate financial measures for one Fact_Policy claim row.

    Option A behavior:
    - Policy-level values are computed once per policy_key and repeated on all claim rows.

    Returns all Fact_Policy financial/flag fields (excluding id and key columns).
    """
    if world_state is None:
        world_state = {}

    np_rng = _rng(world_state, rng=rng)

    cfg = _config_cache(world_state)
    fin_cfg = cfg["fin_cfg"]
    uw_rules = cfg["uw_rules"]
    gwp_ranges = cfg["gwp_ranges"]

    policy_key = str(policy_obj.get("policy_key"))
    if not policy_key:
        raise ValueError("policy_obj must include policy_key")

    product_name = str(policy_obj.get("product_name", "")).strip()
    if product_name not in gwp_ranges:
        raise ValueError(f"Unknown product_name '{product_name}' for GWP range lookup")

    line_of_business = str(policy_obj.get("line_of_business", "")).strip()
    segment = str(policy_obj.get("segment", "Retail")).strip() or "Retail"
    channel_type = str(policy_obj.get("channel_type", "Direct")).strip() or "Direct"

    start_dt = _to_date(policy_obj["policy_start_date"])
    end_dt = _to_date(policy_obj["policy_end_date"])
    claim_dt = _to_date(claim_obj["claim_date"])

    term_days = max(1, (end_dt - start_dt).days)
    elapsed_days = _clamp((claim_dt - start_dt).days, 0.0, float(term_days))
    age_ratio = elapsed_days / float(term_days)

    ranges = fin_cfg.get("ranges", {})
    severity_mult_cfg = fin_cfg.get("product_adjustments", {}).get("severity_multiplier", {})
    recoveries_mult = fin_cfg.get("product_adjustments", {}).get("recoveries_multiplier", {})
    segment_mult = fin_cfg.get("segment_adjustments", {}).get("operating_expense_multiplier", {})
    channel_mult = fin_cfg.get("channel_adjustments", {}).get("acquisition_expense_multiplier", {})
    policy_state_mult = (
        fin_cfg.get("policy_state_adjustments", {}).get("acquisition_expense_multiplier", {})
    )

    cache = _policy_cache(world_state)
    tenure_years = int(policy_obj.get("tenure_years", 1))
    cache_key = f"{policy_key}_{tenure_years}"
    cached = cache.get(cache_key)

    if cached is None:
        min_gwp, max_gwp = gwp_ranges[product_name]
        gwp = float(policy_obj.get("gross_written_premium", np_rng.uniform(min_gwp, max_gwp)))
        gwp = _commercial_outlier_gwp(gwp, line_of_business, np_rng, uw_rules)
        # Hard clamp to product range from spec config.
        gwp = _clamp(gwp, min_gwp, max_gwp)

        is_new = bool(policy_obj.get("new_policy_flag", True))
        ceded_min = float(ranges["ceded_premium_pct_of_gwp"]["min"])
        ceded_max = float(ranges["ceded_premium_pct_of_gwp"]["max"])
        ceded_base = np_rng.uniform(ceded_min, ceded_max)
        ceded_pct = _clamp(ceded_base + np_rng.normal(0.0, 0.005), ceded_min, ceded_max)
        ceded = _clamp(gwp * ceded_pct, 0.0, gwp)

        # Net Earned Premium is the definitive premium retained.
        net_earned = _clamp(gwp - ceded, 0.0, gwp)

        acq_min = float(ranges["acquisition_expense_pct_of_gwp"]["min"])
        acq_max = float(ranges["acquisition_expense_pct_of_gwp"]["max"])

        # Biased acquisition target: keep typical values lower, except new+broker near upper band.
        if is_new and channel_type == "Broker":
            acq_target = float(np_rng.uniform(0.15, 0.20))
        elif (not is_new) and channel_type == "Broker":
            acq_target = float(np_rng.uniform(0.08, 0.12))  # Renewal drop
        elif is_new and channel_type == "Direct":
            acq_target = float(np_rng.uniform(0.10, 0.15))
        else: # NOT new and Direct
            acq_target = float(np_rng.uniform(0.03, 0.06))  # Near-zero renewal acq

        acq_noise = float(np_rng.normal(0.0, 0.004))
        acq_pct = _clamp(acq_target + acq_noise, acq_min, acq_max)
        acquisition_expense = gwp * acq_pct

        op_min = float(ranges["operating_expense_pct_of_earned"]["min"])
        op_max = float(ranges["operating_expense_pct_of_earned"]["max"])

        # Biased operating target by segment profile.
        if segment == "Enterprise":
            op_target = float(np_rng.uniform(0.12, 0.15)) if is_new else float(np_rng.uniform(0.09, 0.11))
        elif segment == "SME":
            op_target = float(np_rng.uniform(0.11, 0.13)) if is_new else float(np_rng.uniform(0.06, 0.08))
        else: # Retail / Personal
            op_target = float(np_rng.uniform(0.10, 0.12)) if is_new else float(np_rng.uniform(0.04, 0.06)) # Fully automated

        op_noise = float(np_rng.normal(0.0, 0.003))
        op_pct = _clamp(op_target + op_noise, op_min, op_max)
        operating_expense = net_earned * op_pct

        cached = {
            "gross_written_premium": _round2(gwp),
            "net_earned_premium": _round2(_clamp(net_earned, 0.0, gwp)),
            "ceded_premium": _round2(_clamp(ceded, 0.0, gwp)),
            "acquisition_expense": _round2(max(0.0, acquisition_expense)),
            "operating_expense": _round2(max(0.0, operating_expense)),
            "new_policy_flag": bool(policy_obj.get("new_policy_flag", True)),
            "renewal_flag": bool(policy_obj.get("renewal_flag", False)),
        }
        cache[policy_key] = cached

    # Claim-level computations.
    if _is_zero_claim(claim_obj):
        incurred = None
        paid = None
        outstanding = None
        ibnr = None
        recoveries = None
        reinsurance_recovery = None
    else:
        gwp = float(cached["gross_written_premium"])
        ceded = float(cached["ceded_premium"])

        # NEW RULE: claim severity sampled from product-level lognormal distribution.
        sev = _severity_params(fin_cfg, product_name)
        product_group = _product_group(fin_cfg, product_name, line_of_business)
        region = str(policy_obj.get("geography", "")).strip() or str(
            policy_obj.get("customer_geography", "")
        ).strip()
        claim_month = int(claim_dt.month)
        claim_year = int(claim_dt.year)

        severity_scale = max(0.0, float(fin_cfg.get("severity_scale", 1.0)))
        product_multiplier = max(0.0, float(severity_mult_cfg.get(product_name, 1.0)))
        seasonal_multiplier = _seasonality_multiplier(fin_cfg, product_group, claim_month)
        climate_multiplier = _region_multiplier(fin_cfg, region, claim_month)
        trend_multiplier = _year_trend_multiplier(fin_cfg, claim_year)
        cat_multiplier = _cat_shock_multiplier(fin_cfg, product_group, claim_year)

        # ADD GLOBAL MONTH SHOCK (Seeded by CLAIM DATE to prevent Law of Large Numbers washout)
        import math
        # Calculate continuous months since Jan 2022 (our baseline start year) using the claim date
        months_since_start = ((claim_year - 2022) * 12) + (claim_month - 1)
        
        # 1. The Slow Wave (Macro Trend): A 12-month full cycle (+/- 15% amplitude)
        # We compress the wave to 12 months so every calendar year contains an equal peak and trough, balancing the annual averages.
        slow_amplitude = 0.15
        slow_frequency = (2 * math.pi) / 12.0
        slow_wave = slow_amplitude * math.sin(slow_frequency * months_since_start)
        
        # 2. The Fast Wave (High-Variance Noise): deterministic randomness based on the claim month
        month_seed = (claim_year * 100) + claim_month
        noise_rng = np.random.default_rng(month_seed)
        fast_wave = float(noise_rng.uniform(-0.15, 0.15))
        
        # 3. The Cohort Shock (Seeded by POLICY START DATE): To make the user's console logs violently jagged!
        # Because the user console groups metrics by policy-start-month, we need structural volatility on the cohort itself.
        policy_start_month = int(start_dt.month)
        policy_start_year = int(start_dt.year)
        cohort_seed = (policy_start_year * 100) + policy_start_month
        cohort_rng = np.random.default_rng(cohort_seed)
        cohort_shock = float(cohort_rng.uniform(-0.30, 0.30))  # Violent +/- 30% jumps
        
        # Combine them around the 1.0 multiplier baseline
        macro_month_shock = max(0.10, 1.0 + slow_wave + fast_wave + cohort_shock)

        ll_cfg = fin_cfg.get("large_loss_model", {})
        prob_map = ll_cfg.get("large_loss_probability_by_product", {})
        default_large_prob = float(ll_cfg.get("default_large_loss_probability", 0.03))
        large_prob = float(prob_map.get(product_name, default_large_prob)) if isinstance(prob_map, dict) else default_large_prob
        large_prob = _clamp(large_prob, 0.0, 1.0)
        is_large_loss = bool(np_rng.uniform(0.0, 1.0) < large_prob)

        if is_large_loss:
            tail_cfg = ll_cfg.get("tail_lognormal", {})
            tail_mean = float(tail_cfg.get("mean", 10.20))
            tail_sigma = float(tail_cfg.get("sigma", 1.10))
            severity_sample = float(np_rng.lognormal(mean=tail_mean, sigma=tail_sigma))
        else:
            severity_sample = float(
                np_rng.lognormal(mean=float(sev["severity_mean"]), sigma=float(sev["severity_sigma"]))
            )

        severity_sample *= (
            severity_scale
            * product_multiplier
            * seasonal_multiplier
            * climate_multiplier
            * trend_multiplier
            * cat_multiplier
            * macro_month_shock
        )

        cap_fraction = max(0.0, float(sev["max_pct_of_gwp_cap"]))
        if is_large_loss:
            mult_cfg = ll_cfg.get("max_multiple_of_gwp", {})
            personal_mult = float(mult_cfg.get("personal_lines", 10.0))
            commercial_mult = float(mult_cfg.get("commercial_lines", 20.0))
            large_cap_mult = personal_mult if _is_personal_line(line_of_business) else commercial_mult
            cap_value = max(0.0, gwp * max(0.0, large_cap_mult))
        else:
            # Non-large bucket remains bounded to cap fraction but removed the 1.0 limit.
            cap_value = max(0.0, gwp * cap_fraction)

        incurred = min(max(0.0, severity_sample), cap_value)

        # Severity-dependent paid ratio.
        if incurred < 2000.0:
            paid_ratio = float(np_rng.uniform(0.75, 0.90))
        elif incurred < 10000.0:
            paid_ratio = float(np_rng.uniform(0.60, 0.75))
        else:
            paid_ratio = float(np_rng.uniform(0.40, 0.65))
        paid = _clamp(incurred * paid_ratio, 0.0, incurred)

        # Outstanding reserve: 15-30% of incurred, constrained with paid.
        outstanding_ratio = float(np_rng.uniform(0.15, 0.30))
        outstanding = _clamp(incurred * outstanding_ratio, 0.0, incurred)
        if paid + outstanding > incurred:
            outstanding = _clamp(incurred - paid, 0.0, incurred)

        # IBNR: 5-15% of incurred, time-adjusted (early higher, late lower).
        ibnr_min = float(ranges["ibnr_pct_of_incurred"]["min"])
        ibnr_max = float(ranges["ibnr_pct_of_incurred"]["max"])
        ibnr_base = ibnr_max - (ibnr_max - ibnr_min) * age_ratio
        ibnr_pct = _clamp(ibnr_base + np_rng.normal(0.0, 0.01), ibnr_min, ibnr_max)
        ibnr = _clamp(incurred * ibnr_pct, 0.0, incurred)

        # Enforce combined constraint with small tolerance for rounding.
        tolerance = 0.01
        total_components = paid + outstanding + ibnr
        max_total = incurred + tolerance
        if total_components > max_total:
            excess = total_components - incurred
            # Reduce IBNR first, then outstanding to preserve paid behavior.
            ibnr = max(0.0, ibnr - excess)
            total_components = paid + outstanding + ibnr
            if total_components > max_total:
                outstanding = max(0.0, outstanding - (total_components - incurred))

        paid = _clamp(paid, 0.0, incurred)
        outstanding = _clamp(outstanding, 0.0, incurred)
        ibnr = _clamp(ibnr, 0.0, incurred)

        rec_min = float(ranges["recoveries_pct_of_incurred"]["min"])
        rec_max = float(ranges["recoveries_pct_of_incurred"]["max"])
        rec_mult = float(recoveries_mult.get(product_name, 1.0))
        recoveries_pct = _clamp(np_rng.uniform(rec_min, rec_max) * rec_mult, rec_min, rec_max)
        recoveries = _clamp(incurred * recoveries_pct, 0.0, incurred)

        ceded_share = (ceded / gwp) if gwp > 0 else 0.0
        reins_noise = float(np_rng.uniform(0.90, 1.10))
        reinsurance_recovery = incurred * ceded_share * reins_noise
        reinsurance_recovery = _clamp(reinsurance_recovery, 0.0, incurred)

    return {
        "gross_written_premium": _nullify_zero(_round2(max(0.0, float(cached["gross_written_premium"])))),
        "ibnr_amount": _nullify_zero(_round2(max(0.0, ibnr)) if ibnr is not None else None),
        "recoveries_amount": _nullify_zero(_round2(max(0.0, recoveries)) if recoveries is not None else None),
        "ceded_premium": _nullify_zero(_round2(_clamp(float(cached["ceded_premium"]), 0.0, float(cached["gross_written_premium"])))),
        "reinsurance_recovery": _nullify_zero(_round2(max(0.0, reinsurance_recovery)) if reinsurance_recovery is not None else None),
        "net_earned_premium": _nullify_zero(_round2(_clamp(float(cached["net_earned_premium"]), 0.0, float(cached["gross_written_premium"])))),
        "incurred_claim_amount": _nullify_zero(_round2(max(0.0, incurred)) if incurred is not None else None),
        "paid_claim_amount": _nullify_zero(_round2(_clamp(paid, 0.0, incurred)) if paid is not None else None),
        "outstanding_reserve": _nullify_zero(_round2(_clamp(outstanding, 0.0, incurred)) if outstanding is not None else None),
        "operating_expense": _nullify_zero(_round2(max(0.0, float(cached["operating_expense"])))),
        "acquisition_expense": _nullify_zero(_round2(max(0.0, float(cached["acquisition_expense"])))),
        "new_policy_flag": bool(cached["new_policy_flag"]),
        "renewal_flag": bool(cached["renewal_flag"]),
    }
