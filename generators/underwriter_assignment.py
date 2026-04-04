"""Underwriter assignment with rule enforcement and scoring-based selection."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import master_data.underwriter_master as underwriter_master


def _load_rules(path: str = "config/underwriter_rules.yaml") -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load underwriter_rules.yaml") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Underwriter rules config not found: {p}")

    payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rules = payload.get("underwriter_rules", {})
    if not rules:
        raise ValueError("underwriter_rules.yaml missing 'underwriter_rules' root")
    return rules


_RULES = _load_rules()


def _parse_month(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    text = str(value)
    if len(text) >= 7:
        return text[:7]
    raise ValueError("month must be date or YYYY-MM/ YYYY-MM-DD string")


def _rng(world_state: dict[str, Any]) -> np.random.Generator:
    seeded = world_state.get("rng")
    if seeded is not None:
        return seeded
    seed = int(world_state.get("random_seed", 20260215))
    generator = np.random.default_rng(seed)
    world_state["rng"] = generator
    return generator


def _resolve_lob(policy_context: dict[str, Any]) -> str:
    lob = policy_context.get("line_of_business") or policy_context.get("lob")
    if not lob:
        raise ValueError("policy_context must include line_of_business/lob")
    return str(lob)


def _resolve_region(policy_context: dict[str, Any]) -> str:
    customer_geo = (
        policy_context.get("customer_geography")
        or policy_context.get("customer", {}).get("customer_geography")
        or policy_context.get("customer", {}).get("geography")
    )
    segment_geo = (
        policy_context.get("segment_geography")
        or policy_context.get("segment", {}).get("segment_geography")
        or policy_context.get("segment", {}).get("geography")
    )

    if customer_geo and segment_geo and str(customer_geo) != str(segment_geo):
        raise ValueError("customer geography must align with segment geography")

    geography = customer_geo or segment_geo or policy_context.get("geography")
    if not geography:
        raise ValueError("policy_context must include customer/segment geography")
    return str(geography)


def _team_for_lob(lob: str) -> str:
    mapping = {
        "Commercial Property": "Commercial Property UW",
        "Commercial Casualty": "Commercial Casualty UW",
        "Personal Property": "Personal Lines UW",
        "Personal Casualty": "Personal Lines UW",
    }
    if lob not in mapping:
        raise ValueError(f"Unsupported line_of_business '{lob}'")
    return mapping[lob]


def _workload_map(world_state: dict[str, Any], month_key: str) -> dict[str, int]:
    loads = world_state.setdefault("underwriter_monthly_load", {})
    return loads.setdefault(month_key, {})


def _resolve_gwp(policy_context: dict[str, Any]) -> float:
    return float(policy_context.get("gross_written_premium", policy_context.get("gwp", 0.0)) or 0.0)


def _allowed_seniority_for_gwp(gwp: float) -> set[str]:
    cfg = _RULES.get("seniority_thresholds", {})
    high = cfg.get("high", {})
    medium = cfg.get("medium", {})
    low = cfg.get("low", {})

    high_min = float(high.get("min_gwp", 18000.0))
    medium_min = float(medium.get("min_gwp", 9000.0))

    if gwp >= high_min:
        return {str(x) for x in high.get("allowed_seniority_levels", ["Senior", "Lead"])}
    if gwp >= medium_min:
        return {str(x) for x in medium.get("allowed_seniority_levels", ["Mid", "Senior"])}
    return {str(x) for x in low.get("allowed_seniority_levels", ["Junior", "Mid"])}


def _normalize_product_name(name: str) -> str:
    return " ".join(name.replace("/", " / ").split())


def _is_complex_product(policy_context: dict[str, Any]) -> bool:
    product_name = _normalize_product_name(str(policy_context.get("product_name", "")))
    complex_cfg = _RULES.get("complexity_rules", {}).get("complex_products", [])
    normalized = {_normalize_product_name(str(p)) for p in complex_cfg}

    # Default fallback if config is missing.
    if not normalized:
        normalized = {
            _normalize_product_name("Business Interruption"),
            _normalize_product_name("Umbrella / Excess"),
            _normalize_product_name("Commercial Auto Liability"),
        }

    return product_name in normalized


def _seniority_score(level: str) -> float:
    base = {
        "Junior": 0.35,
        "Mid": 0.65,
        "Senior": 0.90,
        "Lead": 1.00,
    }
    return base.get(level, 0.40)


def _complexity_score(policy_context: dict[str, Any], underwriter: dict, gwp: float) -> float:
    level = str(underwriter.get("seniority_level", ""))
    is_complex = _is_complex_product(policy_context)
    if not is_complex:
        return 0.70

    rules = _RULES.get("complexity_rules", {})
    moderate_min = float(rules.get("moderate_premium_min_gwp", 9000.0))
    seniority_bonus = float(rules.get("seniority_bonus", 0.15))

    # Base complexity fit by seniority.
    if level == "Lead":
        score = 1.00
    elif level == "Senior":
        score = 0.95
    elif level == "Mid":
        score = 0.70
    else:
        score = 0.35

    # Bias toward Senior on complex business at moderate premium and above.
    if gwp >= moderate_min:
        if level == "Senior":
            score += seniority_bonus
        elif level == "Lead":
            score += seniority_bonus * 0.60
        elif level == "Mid":
            score -= seniority_bonus * 0.30

    return float(max(0.0, min(score, 1.0)))


def _weights() -> dict[str, float]:
    w = _RULES.get("scoring_weights", {})
    raw = {
        "region": float(w.get("region_match", 0.30)),
        "team": float(w.get("team_specialization_match", 0.25)),
        "seniority": float(w.get("seniority_fit", 0.20)),
        "workload": float(w.get("workload_balance", 0.10)),
        "complexity": float(w.get("complexity_fit", 0.15)),
    }
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("invalid scoring weights")
    return {k: v / total for k, v in raw.items()}


def assign_underwriter(policy_context: dict, underwriters: list[dict], world_state: dict) -> dict:
    """Assign one underwriter using scoring and update monthly workload.

    score = w_region + w_team + w_seniority + w_workload + w_complexity
    """
    if not underwriters:
        raise ValueError("underwriters list is empty")

    rng = _rng(world_state)
    month_key = _parse_month(policy_context.get("month") or world_state.get("month"))

    lob = _resolve_lob(policy_context)
    region = _resolve_region(policy_context)
    required_team = _team_for_lob(lob)
    gwp = _resolve_gwp(policy_context)

    allowed_seniority = _allowed_seniority_for_gwp(gwp)

    # Hard constraints first: region/team alignment + premium-band seniority eligibility.
    candidates: list[dict] = []
    for uw in underwriters:
        uw_region = str(uw.get("underwriter_region", uw.get("region")))
        if uw_region != region:
            continue
        if str(uw.get("team")) != required_team:
            continue

        level = str(uw.get("seniority_level", ""))
        if level not in allowed_seniority:
            continue

        candidates.append(uw)

    if not candidates:
        # Fallback: create one matching underwriter when the active pool cannot satisfy constraints.
        preferred_level = "Senior" if "Senior" in allowed_seniority else sorted(allowed_seniority)[0]
        created = underwriter_master.create_underwriter_for_criteria(
            month=month_key,
            region=region,
            team=required_team,
            seniority_level=preferred_level,
        )
        candidates = [created]

    weights = _weights()
    month_load = _workload_map(world_state, month_key)
    max_load = max([int(month_load.get(str(u["underwriter_key"]), 0)) for u in candidates] + [1])

    scored: list[tuple[float, dict]] = []
    for uw in candidates:
        uw_key = str(uw["underwriter_key"])
        level = str(uw.get("seniority_level", ""))

        region_component = 1.0
        team_component = 1.0
        seniority_component = _seniority_score(level)

        current_load = int(month_load.get(uw_key, 0))
        workload_component = 1.0 - (current_load / max_load)
        complexity_component = _complexity_score(policy_context, uw, gwp)

        score = (
            weights["region"] * region_component
            + weights["team"] * team_component
            + weights["seniority"] * seniority_component
            + weights["workload"] * workload_component
            + weights["complexity"] * complexity_component
        )

        # Small jitter for deterministic tie-breaking through seeded RNG.
        score += float(rng.uniform(0.0, 1e-6))
        scored.append((score, uw))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[0][1]
    if str(selected.get("underwriter_region", selected.get("region"))) != region:
        raise ValueError(
            "Selected underwriter region does not match policy geography: "
            f"selected_region={selected.get('underwriter_region', selected.get('region'))!r}, "
            f"required_region={region!r}"
        )

    selected_key = str(selected["underwriter_key"])
    month_load[selected_key] = int(month_load.get(selected_key, 0)) + 1

    return selected
