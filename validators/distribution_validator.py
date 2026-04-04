"""Distribution-level validation for synthetic insurance outputs."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional


def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for distribution validation") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _pct(n: int, d: int) -> float:
    return (100.0 * n / d) if d > 0 else 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_tolerances() -> dict[str, float]:
    return {
        # Absolute percentage-point tolerance for region target checks.
        "region_abs_pct": 3.0,
        # Extra slack (percentage points) around channel min/max bands.
        "channel_band_pct": 1.0,
        # Absolute percentage-point tolerance around 1% zero-claim rate.
        "zero_claim_abs_pct": 0.35,
        # Absolute percentage-point tolerance for each claim-count bucket by product.
        "claim_count_abs_pct": 6.0,
    }


def _resolve_policy_info(
    fact_rows: list[dict],
    policy_rows: Optional[list[dict]],
    channel_lookup: Optional[dict[str, dict]],
    segment_lookup: Optional[dict[str, dict]],
    product_lookup: Optional[dict[str, dict]],
) -> dict[str, dict]:
    """Build per-policy metadata for distribution checks."""
    info: dict[str, dict] = {}

    if policy_rows:
        for p in policy_rows:
            key = str(p.get("policy_key", ""))
            if not key:
                continue
            info[key] = {
                "policy_key": key,
                "geography": p.get("geography"),
                "channel_type": p.get("channel_type"),
                "product_name": p.get("product_name"),
            }

    for row in fact_rows:
        key = str(row.get("policy_key", ""))
        if not key:
            continue

        entry = info.setdefault(
            key,
            {
                "policy_key": key,
                "geography": None,
                "channel_type": None,
                "product_name": None,
            },
        )

        if entry["channel_type"] is None:
            if row.get("channel_type") is not None:
                entry["channel_type"] = row.get("channel_type")
            elif channel_lookup and row.get("channel_key") is not None:
                ch = channel_lookup.get(str(row.get("channel_key")))
                if ch is not None:
                    entry["channel_type"] = ch.get("channel_type")

        if entry["geography"] is None:
            if row.get("geography") is not None:
                entry["geography"] = row.get("geography")
            elif segment_lookup and row.get("segment_key") is not None:
                seg = segment_lookup.get(str(row.get("segment_key")))
                if seg is not None:
                    entry["geography"] = seg.get("segment_geography")

        if entry["product_name"] is None:
            if row.get("product_name") is not None:
                entry["product_name"] = row.get("product_name")
            elif product_lookup and row.get("product_key") is not None:
                prod = product_lookup.get(str(row.get("product_key")))
                if prod is not None:
                    entry["product_name"] = prod.get("product_name")

    return info


def _is_zero_claim_row(row: dict) -> bool:
    return (
        _safe_float(row.get("incurred_claim_amount")) == 0.0
        and _safe_float(row.get("paid_claim_amount")) == 0.0
        and _safe_float(row.get("outstanding_reserve")) == 0.0
        and _safe_float(row.get("ibnr_amount")) == 0.0
        and _safe_float(row.get("recoveries_amount")) == 0.0
        and _safe_float(row.get("reinsurance_recovery")) == 0.0
    )


def validate_distributions(
    fact_rows: list[dict],
    policy_rows: Optional[list[dict]] = None,
    channel_lookup: Optional[dict[str, dict]] = None,
    segment_lookup: Optional[dict[str, dict]] = None,
    product_lookup: Optional[dict[str, dict]] = None,
    tolerances: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Validate macro-distributions and return a structured report.

    The report includes pass/fail status, computed distributions, and violations.
    """
    tol = _default_tolerances()
    if tolerances:
        tol.update({k: float(v) for k, v in tolerances.items()})

    geo_cfg = _load_yaml("config/geo_distribution.yaml").get("geo_distribution", {})
    channel_cfg = _load_yaml("config/channel_distribution.yaml").get("channel_distribution", {})
    claim_cfg_rows = _load_yaml("config/claim_count_dist.yaml").get("claim_count_distribution", [])

    claim_cfg: dict[str, dict[str, float]] = {}
    for row in claim_cfg_rows:
        claim_cfg[str(row["product_name"])] = {
            "pct_1_claim": float(row["pct_1_claim"]),
            "pct_2_claim": float(row["pct_2_claim"]),
            "pct_3_claim": float(row["pct_3_claim"]),
            "pct_4_plus": float(row["pct_4_plus"]),
        }

    policy_info = _resolve_policy_info(
        fact_rows=fact_rows,
        policy_rows=policy_rows,
        channel_lookup=channel_lookup,
        segment_lookup=segment_lookup,
        product_lookup=product_lookup,
    )

    violations: list[str] = []
    sections: dict[str, Any] = {}

    # 1) Region distribution (policy-level, deduped by policy_key).
    region_target = {str(k): float(v) for k, v in geo_cfg.items()}
    region_counts = Counter(
        str(v["geography"]) for v in policy_info.values() if v.get("geography") is not None
    )
    total_region = sum(region_counts.values())

    region_actual = {
        region: _pct(region_counts.get(region, 0), total_region) for region in region_target
    }
    region_pass = True
    for region, target in region_target.items():
        diff = abs(region_actual.get(region, 0.0) - target)
        if diff > tol["region_abs_pct"]:
            region_pass = False
            violations.append(
                f"Region distribution out of tolerance for '{region}': "
                f"actual={region_actual.get(region, 0.0):.2f}%, target={target:.2f}%, "
                f"diff={diff:.2f}pp, tol={tol['region_abs_pct']:.2f}pp"
            )

    sections["region_distribution"] = {
        "passed": region_pass,
        "target_pct": region_target,
        "actual_pct": region_actual,
        "tolerance_abs_pct": tol["region_abs_pct"],
        "sample_size": total_region,
    }

    # 2) Channel distribution within configured bands (policy-level).
    channel_counts = Counter(
        str(v["channel_type"]) for v in policy_info.values() if v.get("channel_type") is not None
    )
    total_channel = sum(channel_counts.values())
    channel_actual = {
        ch: _pct(channel_counts.get(ch, 0), total_channel) for ch in channel_cfg.keys()
    }

    channel_pass = True
    for ch, band in channel_cfg.items():
        min_allowed = float(band["min_pct"]) - tol["channel_band_pct"]
        max_allowed = float(band["max_pct"]) + tol["channel_band_pct"]
        actual = channel_actual.get(ch, 0.0)
        if actual < min_allowed or actual > max_allowed:
            channel_pass = False
            violations.append(
                f"Channel distribution out of band for '{ch}': actual={actual:.2f}%, "
                f"allowed=[{min_allowed:.2f}%, {max_allowed:.2f}%]"
            )

    sections["channel_distribution"] = {
        "passed": channel_pass,
        "bands_pct": channel_cfg,
        "actual_pct": channel_actual,
        "band_tolerance_pct": tol["channel_band_pct"],
        "sample_size": total_channel,
    }

    # 3) Zero-claim policy rate (~30%).
    by_policy_rows: dict[str, list[dict]] = defaultdict(list)
    for row in fact_rows:
        key = str(row.get("policy_key", ""))
        if key:
            by_policy_rows[key].append(row)

    total_policies = len(by_policy_rows)
    zero_claim_policies = 0
    for rows in by_policy_rows.values():
        if rows and all(_is_zero_claim_row(r) for r in rows):
            zero_claim_policies += 1

    claim_scenario_cfg = _load_yaml("config/scenario.yaml").get("scenario", {}).get("claims", {}).get("zero_claim_rate_by_product", {})
    expected_zeros = 0.0
    valid_policies = 0
    for key in by_policy_rows.keys():
        info = policy_info.get(key, {})
        prod = info.get("product_name")
        if prod in claim_scenario_cfg:
            expected_zeros += float(claim_scenario_cfg[prod])
            valid_policies += 1

    zero_actual_pct = _pct(zero_claim_policies, total_policies)
    if valid_policies > 0:
        zero_target_pct = (expected_zeros / valid_policies) * 100.0
    else:
        zero_target_pct = 30.0
    zero_pass = abs(zero_actual_pct - zero_target_pct) <= tol["zero_claim_abs_pct"]
    if not zero_pass:
        violations.append(
            f"Zero-claim policy rate out of tolerance: actual={zero_actual_pct:.3f}%, "
            f"target={zero_target_pct:.3f}%, tol={tol['zero_claim_abs_pct']:.3f}pp"
        )

    sections["zero_claim_rate"] = {
        "passed": zero_pass,
        "target_pct": zero_target_pct,
        "actual_pct": zero_actual_pct,
        "tolerance_abs_pct": tol["zero_claim_abs_pct"],
        "sample_size": total_policies,
        "zero_claim_policy_count": zero_claim_policies,
    }

    # 4) Claim-count distribution per product (exclude zero-claim policies).
    #    Bucket distribution among policies-with-claims.
    product_bucket_counts: dict[str, Counter] = defaultdict(Counter)

    for policy_key, rows in by_policy_rows.items():
        if not rows:
            continue

        info = policy_info.get(policy_key, {})
        product_name = info.get("product_name")
        if product_name is None and rows:
            product_name = rows[0].get("product_name")
            if product_name is None and product_lookup and rows[0].get("product_key") is not None:
                p = product_lookup.get(str(rows[0].get("product_key")))
                if p is not None:
                    product_name = p.get("product_name")

        if product_name is None or str(product_name) not in claim_cfg:
            continue

        is_zero_policy = all(_is_zero_claim_row(r) for r in rows)
        if is_zero_policy:
            continue

        claim_count = len(rows)
        if claim_count <= 1:
            bucket = "pct_1_claim"
        elif claim_count == 2:
            bucket = "pct_2_claim"
        elif claim_count == 3:
            bucket = "pct_3_claim"
        else:
            bucket = "pct_4_plus"

        product_bucket_counts[str(product_name)][bucket] += 1

    claim_dist_details: dict[str, Any] = {}
    claim_dist_pass = True
    for product_name, target in claim_cfg.items():
        counter = product_bucket_counts.get(product_name, Counter())
        denom = sum(counter.values())
        if denom == 0:
            claim_dist_details[product_name] = {
                "passed": False,
                "reason": "No policies-with-claims found for product",
                "target_pct": target,
                "actual_pct": {
                    "pct_1_claim": 0.0,
                    "pct_2_claim": 0.0,
                    "pct_3_claim": 0.0,
                    "pct_4_plus": 0.0,
                },
                "sample_size": 0,
            }
            claim_dist_pass = False
            violations.append(
                f"Claim-count distribution cannot be validated for '{product_name}': no sample"
            )
            continue

        actual = {
            "pct_1_claim": _pct(counter.get("pct_1_claim", 0), denom),
            "pct_2_claim": _pct(counter.get("pct_2_claim", 0), denom),
            "pct_3_claim": _pct(counter.get("pct_3_claim", 0), denom),
            "pct_4_plus": _pct(counter.get("pct_4_plus", 0), denom),
        }

        p_pass = True
        for bucket, target_pct in target.items():
            diff = abs(actual[bucket] - target_pct)
            if diff > tol["claim_count_abs_pct"]:
                p_pass = False
                claim_dist_pass = False
                violations.append(
                    f"Claim-count distribution out of tolerance for '{product_name}' bucket '{bucket}': "
                    f"actual={actual[bucket]:.2f}%, target={target_pct:.2f}%, "
                    f"diff={diff:.2f}pp, tol={tol['claim_count_abs_pct']:.2f}pp"
                )

        claim_dist_details[product_name] = {
            "passed": p_pass,
            "target_pct": target,
            "actual_pct": actual,
            "sample_size": denom,
        }

    sections["claim_count_distribution"] = {
        "passed": claim_dist_pass,
        "tolerance_abs_pct": tol["claim_count_abs_pct"],
        "by_product": claim_dist_details,
    }

    overall_pass = all(s.get("passed", False) for s in sections.values()) and not violations

    return {
        "passed": bool(overall_pass),
        "tolerances": tol,
        "summary": {
            "policy_sample_size": len(policy_info),
            "fact_row_count": len(fact_rows),
            "violation_count": len(violations),
        },
        "sections": sections,
        "violations": violations,
    }
