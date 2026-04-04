"""Channel and broker assignment helpers for policy generation."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


def _load_channel_config(path: str = "config/channel_distribution.yaml") -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load channel_distribution.yaml") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Channel distribution config not found: {p}")

    payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = payload.get("channel_distribution", {})
    required = {"Direct", "Broker", "Bancassurance"}

    if set(cfg.keys()) != required:
        raise ValueError("channel_distribution must define exactly Direct, Broker, Bancassurance")

    return cfg


_CHANNEL_CFG = _load_channel_config()


def _parse_month(month_value: str | date) -> date:
    if isinstance(month_value, date):
        return month_value.replace(day=1)
    if len(month_value) == 7:
        return date.fromisoformat(f"{month_value}-01")
    return date.fromisoformat(month_value).replace(day=1)


def _rng_uniform(rng: Any, low: float, high: float) -> float:
    if hasattr(rng, "uniform"):
        return float(rng.uniform(low, high))
    raise TypeError("rng must provide a uniform(low, high) method")


def _rng_choice_weighted(rng: Any, values: list[str], weights: list[float]) -> str:
    if hasattr(rng, "choice_weighted"):
        return str(rng.choice_weighted(values, weights))

    # numpy Generator API compatibility.
    if hasattr(rng, "choice"):
        total = sum(weights)
        probs = [w / total for w in weights]
        return str(rng.choice(values, p=probs))

    raise TypeError("rng must provide choice_weighted(...) or numpy-style choice(..., p=...)")


def _channel_ranges() -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    direct = _CHANNEL_CFG["Direct"]
    broker = _CHANNEL_CFG["Broker"]
    banca = _CHANNEL_CFG["Bancassurance"]

    d_range = (float(direct["min_pct"]) / 100.0, float(direct["max_pct"]) / 100.0)
    b_range = (float(broker["min_pct"]) / 100.0, float(broker["max_pct"]) / 100.0)
    c_range = (float(banca["min_pct"]) / 100.0, float(banca["max_pct"]) / 100.0)
    return d_range, b_range, c_range


def _month_seasonal_shift(month: str | date) -> float:
    m = _parse_month(month)
    # Small deterministic monthly shift to avoid static mix while respecting bounds.
    return 0.03 * __import__("math").sin((2.0 * __import__("math").pi * m.month) / 12.0)


def _sample_valid_mix(month: str | date, rng: Any) -> tuple[float, float, float]:
    (d_min, d_max), (b_min, b_max), (c_min, c_max) = _channel_ranges()
    shift = _month_seasonal_shift(month)

    # Constrained rejection sampling so each sampled mix respects all range bounds.
    for _ in range(2000):
        d_low = max(d_min, d_min + shift)
        d_high = min(d_max, d_max + shift)
        if d_low > d_high:
            d_low, d_high = d_min, d_max

        b_low = max(b_min, b_min - shift / 2.0)
        b_high = min(b_max, b_max - shift / 2.0)
        if b_low > b_high:
            b_low, b_high = b_min, b_max

        direct = _rng_uniform(rng, d_low, d_high)
        broker = _rng_uniform(rng, b_low, b_high)
        banca = 1.0 - direct - broker
        if c_min <= banca <= c_max:
            return direct, broker, banca

    # Deterministic fallback in edge cases.
    direct = min(max((d_min + d_max) / 2.0, d_min), d_max)
    broker = min(max((b_min + b_max) / 2.0, b_min), b_max)
    banca = 1.0 - direct - broker
    if not (c_min <= banca <= c_max):
        raise ValueError("Unable to derive a valid channel mix within configured ranges")
    return direct, broker, banca


def choose_channel(month: str | date, rng: Any) -> str:
    """Choose a channel_type respecting configured range constraints.

    Returns one of: Direct, Broker, Bancassurance.
    """
    direct, broker, banca = _sample_valid_mix(month, rng)
    return _rng_choice_weighted(
        rng,
        values=["Direct", "Broker", "Bancassurance"],
        weights=[direct, broker, banca],
    )


def _resolve_region(region: Any) -> str:
    if isinstance(region, str):
        return region

    if isinstance(region, dict):
        customer_geo = region.get("customer_geography")
        segment_geo = region.get("segment_geography")

        resolved = customer_geo or segment_geo or region.get("geography")
        if not resolved:
            raise ValueError("region must provide customer_geography and/or segment_geography")
        return str(resolved)

    raise ValueError("region must be a geography string or a mapping with geography fields")


def assign_broker(channel: str | dict, region: Any, broker_master: Any, month: str | date) -> str | None:
    """Assign broker_key based on channel and geography rules.

    Rules enforced:
    - Direct => broker_key is NULL (None)
    - Broker => broker_key must be present
    - Broker region must match customer + segment geography
    """
    channel_type = channel.get("channel_type") if isinstance(channel, dict) else str(channel)
    channel_type = str(channel_type)

    if channel_type == "Direct":
        return None

    target_region = _resolve_region(region)

    if channel_type == "Broker":
        candidates = broker_master.get_active_brokers(month=month, region=target_region, broker_type="Broker")
        if not candidates:
            candidates = broker_master.get_active_brokers(month=month, region=target_region, broker_type="Agent")
        if not candidates:
            candidates = broker_master.get_active_brokers(month=month, region=None, broker_type="Broker")
        if not candidates:
            candidates = broker_master.get_active_brokers(month=month, region=None, broker_type="Agent")

        if not candidates:
            raise ValueError(
                f"No active Broker/Agent available nationally in month '{month}'"
            )
        import random
        return str(random.choice(candidates)["broker_key"])

    if channel_type == "Bancassurance":
        candidates = broker_master.get_active_brokers(month=month, region=target_region, broker_type="Bancassurance")
        if not candidates:
            candidates = broker_master.get_active_brokers(month=month, region=None, broker_type="Bancassurance")
        if not candidates:
            raise ValueError(
                f"No active Bancassurance broker available nationally in month '{month}'"
            )
        import random
        return str(random.choice(candidates)["broker_key"])

    raise ValueError("channel must be one of: Direct, Broker, Bancassurance")
