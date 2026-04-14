"""Claim generation for policy-level inputs with product-specific distributions."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np


def _load_claim_count_dist(path: str = "config/claim_count_dist.yaml") -> dict[str, dict[str, float]]:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load claim_count_dist.yaml") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Claim count config not found: {p}")

    payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = payload.get("claim_count_distribution", [])
    if not isinstance(rows, list):
        raise ValueError("claim_count_dist.yaml must contain claim_count_distribution as list")

    mapping: dict[str, dict[str, float]] = {}
    for row in rows:
        product = str(row.get("product_name", "")).strip()
        if not product:
            raise ValueError("Each claim_count_distribution row must include product_name")
        mapping[product] = {
            "pct_1_claim": float(row.get("pct_1_claim", 0.0)),
            "pct_2_claim": float(row.get("pct_2_claim", 0.0)),
            "pct_3_claim": float(row.get("pct_3_claim", 0.0)),
            "pct_4_plus": float(row.get("pct_4_plus", 0.0)),
        }
    return mapping


_CLAIM_DIST = _load_claim_count_dist()

_ZERO_CLAIM_RATE_BY_PRODUCT = {
    "Building & Contents": 0.60,
    "Business Interruption": 0.75,
    "Inland Marine": 0.65,
    "General Liability": 0.70,
    "Commercial Auto Liability": 0.55,
    "Umbrella / Excess": 0.85,
    "Homeowners": 0.75,
    "Renters": 0.85,
    "Condo": 0.80,
    "Personal Auto": 0.55,
    "Personal Umbrella": 0.90,
    "Motorcycle / Recreational": 0.65,
}


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _get_rng(rng: Any = None, seed: int = 20260215) -> np.random.Generator:
    if rng is None:
        return np.random.default_rng(int(seed))
    if hasattr(rng, "choice") and hasattr(rng, "integers"):
        return rng
    raise TypeError("rng must be a numpy.random.Generator-compatible object")


def _claim_count_for_product(product_name: str, rng: np.random.Generator) -> int:
    if product_name not in _CLAIM_DIST:
        raise ValueError(f"No claim-count distribution configured for product '{product_name}'")

    dist = _CLAIM_DIST[product_name]
    bucket = str(
        rng.choice(
            ["1", "2", "3", "4+"],
            p=np.array(
                [
                    dist["pct_1_claim"],
                    dist["pct_2_claim"],
                    dist["pct_3_claim"],
                    dist["pct_4_plus"],
                ],
                dtype=float,
            )
            / 100.0,
        )
    )

    if bucket == "1":
        return 1
    if bucket == "2":
        return 2
    if bucket == "3":
        return 3

    # 4+ bucket expands to realistic finite counts.
    return int(rng.choice([4, 5, 6], p=[0.60, 0.30, 0.10]))


def _uniform_claim_date(start_date: date, end_date: date, rng: np.random.Generator) -> date:
    if start_date > end_date:
        raise ValueError("policy_start_date must be <= policy_end_date")
    span_days = (end_date - start_date).days
    offset = int(rng.integers(0, span_days + 1))
    return start_date + timedelta(days=offset)


def generate_claims(
    policies: list[dict],
    rng: Any = None,
    seed: int = 20260215,
) -> list[dict]:
    """Generate claim objects for policies.

    Rules:
    - Product-specific share of policies produce no claim rows.
    - Remaining policies sample claim count by product from claim_count_dist.yaml.
    - 4+ claim bucket expands to {4,5,6} with weighted sampling.
    - Claim date is uniform random in [policy_start_date, policy_end_date].

    Returned claim object fields (strict):
    - policy_key
    - claim_date
    - claim_index
    """
    np_rng = _get_rng(rng=rng, seed=seed)

    claims: list[dict] = []
    for policy in policies:
        policy_key = str(policy["policy_key"])
        start_dt = _to_date(policy["policy_start_date"])
        end_dt = _to_date(policy["policy_end_date"])
        product_name = str(policy.get("product_name", "")).strip()
        if product_name not in _CLAIM_DIST:
            raise ValueError(f"No claim-count distribution configured for product '{product_name}'")

        zero_claim_rate = float(_ZERO_CLAIM_RATE_BY_PRODUCT.get(product_name, 0.30))
        if np_rng.uniform(0.0, 1.0) < zero_claim_rate:
            continue

        claim_count = _claim_count_for_product(product_name, np_rng)

        for claim_idx in range(1, claim_count + 1):
            claims.append(
                {
                    "policy_key": policy_key,
                    "claim_date": _uniform_claim_date(start_dt, end_dt, np_rng).isoformat(),
                    "claim_index": claim_idx,
                }
            )

    return claims
