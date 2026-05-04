"""Assign lifecycle traits to each policy.

Trait dimensions:
  billing_frequency   – 'monthly' | 'annual'
  lapse_type          – None | 'mid' | 'early'
  lapse_after_n       – int  (payments before lapse; 0 for early, 1-8 for mid)
  endorsement_type    – 'none' | 'single_increase' | 'single_decrease' | 'multiple'
  endorsements        – list[dict] with keys: month_offset, direction ('increase'|'decrease')
  claim_workflow      – 'standard' | 'recoveries' | 'open' | 'long_tail'
                        (overrides per-claim; applied in _append_claim_events)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for scenario_engine") from exc
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


import os

def _cfg() -> dict:
    path = os.getenv("TRAITS_PATH", "config/scenario_traits.yaml")
    return _load_yaml(path).get("scenario_traits", {})


def _pick(rng_manager: Any, weights_dict: dict, entity_seed: str) -> str:
    """Return one key from weights_dict using rng_manager weighted choice."""
    keys = list(weights_dict.keys())
    weights = [float(weights_dict[k]) for k in keys]
    return str(rng_manager.choice_weighted(keys, weights, entity_seed=entity_seed))


def apply_lifecycle_traits(
    policy_obj: dict[str, Any],
    rng_manager: Any,
    world_state: Optional[dict[str, Any]] = None,
) -> None:
    """Inject lifecycle traits into policy_obj in-place.

    This function is idempotent — calling it twice on the same policy_obj
    with the same rng seeds produces identical results.

    Correlation rules enforced here:
    - Early lapse  → no endorsements, claim_workflow forced to None
    - Mid lapse    → endorsement month_offset <= lapse_after_n
    """
    cfg = _cfg()
    pk = str(policy_obj["policy_key"])

    # ------------------------------------------------------------------
    # Trait 2 – Lapse
    # ------------------------------------------------------------------
    lapse_cfg = cfg.get("lapse", {})
    lapse_weights = {
        "none":  float(lapse_cfg.get("none",  0.87)),
        "mid":   float(lapse_cfg.get("mid",   0.08)),
        "early": float(lapse_cfg.get("early", 0.05)),
    }
    lapse_type_raw = _pick(rng_manager, lapse_weights, entity_seed=f"{pk}|lapse_type")
    lapse_type: Optional[str] = None if lapse_type_raw == "none" else lapse_type_raw

    lapse_after_n = 0
    if lapse_type == "mid":
        mid_range = lapse_cfg.get("lapse_mid_range", {})
        lo = int(mid_range.get("min_payments", 1))
        hi = int(mid_range.get("max_payments", 8))
        lapse_after_n = int(rng_manager.randint(lo, hi + 1, entity_seed=f"{pk}|lapse_n"))
    policy_obj["lapse_type"] = lapse_type
    policy_obj["lapse_after_n"] = lapse_after_n

    # ------------------------------------------------------------------
    # Trait 3 – Endorsements
    # (blocked for early lapse; constrained for mid lapse)
    # ------------------------------------------------------------------
    end_cfg = cfg.get("endorsements", {})
    if lapse_type == "early":
        endorsement_type = "none"
    else:
        end_weights = {
            "none":            float(end_cfg.get("none",            0.72)),
            "single_increase": float(end_cfg.get("single_increase", 0.12)),
            "single_decrease": float(end_cfg.get("single_decrease", 0.08)),
            "multiple":        float(end_cfg.get("multiple",        0.08)),
        }
        endorsement_type = _pick(rng_manager, end_weights, entity_seed=f"{pk}|endorsement_type")

    policy_obj["endorsement_type"] = endorsement_type

    endorsements: list[dict] = []
    if endorsement_type in ("single_increase", "single_decrease"):
        direction = "increase" if endorsement_type == "single_increase" else "decrease"
        # Month offset: between months 2 and 8 of the term
        max_month = lapse_after_n - 1 if lapse_type == "mid" else 8
        max_month = max(2, max_month)
        month_offset = int(rng_manager.randint(2, max_month + 1, entity_seed=f"{pk}|end_month_0"))
        endorsements.append({"month_offset": month_offset, "direction": direction})

    elif endorsement_type == "multiple":
        count_weights_raw = end_cfg.get("multiple_count", {}).get("weights", [0.70, 0.30])
        count = 2 if float(rng_manager.uniform(0.0, 1.0, entity_seed=f"{pk}|multi_count")) < float(count_weights_raw[0]) else 3
        used_months: set[int] = set()
        max_month = lapse_after_n - 1 if lapse_type == "mid" else 9
        max_month = max(3, max_month)
        for idx in range(count):
            for attempt in range(20):
                mo = int(rng_manager.randint(2, max_month + 1, entity_seed=f"{pk}|multi_month_{idx}_{attempt}"))
                if mo not in used_months:
                    used_months.add(mo)
                    break
            direction = "increase" if float(rng_manager.uniform(0.0, 1.0, entity_seed=f"{pk}|multi_dir_{idx}")) < 0.6 else "decrease"
            endorsements.append({"month_offset": mo, "direction": direction})
        endorsements.sort(key=lambda e: e["month_offset"])

    policy_obj["endorsements"] = endorsements

    # ------------------------------------------------------------------
    # Trait 4 – Claim Workflow Override
    # (None for early lapse; otherwise one of the 4 workflow types)
    # The actual zero-claim gate in claim_generator.py still applies.
    # This trait simply controls the *shape* of the workflow IF a claim fires.
    # ------------------------------------------------------------------
    if lapse_type == "early":
        policy_obj["claim_workflow"] = None
    else:
        wf_cfg = cfg.get("claim_workflow", {})
        wf_weights = {
            "standard":   float(wf_cfg.get("standard",   0.55)),
            "recoveries": float(wf_cfg.get("recoveries", 0.22)),
            "open":       float(wf_cfg.get("open",       0.13)),
            "long_tail":  float(wf_cfg.get("long_tail",  0.10)),
        }
        policy_obj["claim_workflow"] = _pick(rng_manager, wf_weights, entity_seed=f"{pk}|claim_workflow")
