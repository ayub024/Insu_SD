"""Segment dimension builder for the synthetic insurance star schema."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

_ALLOWED_REGIONS = ["Midwest", "Southeast", "Northeast", "Southwest", "West"]
_SEGMENT_MAP = [
    ("Retail", "Individual"),
    ("SME", "Business"),
    ("Enterprise", "Business"),
]


def _allocate_counts_by_pct(distribution: dict[str, float], total_rows: int) -> dict[str, int]:
    """Allocate integer counts by percentage while preserving exact total_rows."""
    raw = {k: (v / 100.0) * total_rows for k, v in distribution.items()}
    counts = {k: int(raw[k]) for k in raw}
    remainder = total_rows - sum(counts.values())

    # Largest remainder method for deterministic completion.
    order = sorted(raw.keys(), key=lambda k: (raw[k] - counts[k]), reverse=True)
    for i in range(remainder):
        counts[order[i % len(order)]] += 1

    return counts


def build_dim_segment(
    geo_distribution_path: str = "config/geo_distribution.yaml",
    total_rows: int = 100,
    output: Literal["records", "dataframe"] = "records",
):
    """Build Dim_Segment using fixed segments and spec geography distribution.

    Output columns (strict):
    - segment_key (SEG001, SEG002, ...)
    - segment
    - segment_customer_type
    - segment_geography
    """
    if total_rows <= 0:
        raise ValueError("total_rows must be > 0")

    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load geo_distribution.yaml") from exc

    config_path = Path(geo_distribution_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Geo distribution config not found: {config_path}")

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    geo_distribution = payload.get("geo_distribution", {})

    if set(geo_distribution.keys()) != set(_ALLOWED_REGIONS):
        raise ValueError(
            "geo_distribution.yaml must contain exactly these regions: "
            f"{', '.join(_ALLOWED_REGIONS)}"
        )

    total_pct = sum(float(v) for v in geo_distribution.values())
    if abs(total_pct - 100.0) > 1e-9:
        raise ValueError("Geography percentages must sum to 100")

    geo_counts = _allocate_counts_by_pct(
        {k: float(v) for k, v in geo_distribution.items()}, total_rows=total_rows
    )

    geography_sequence: list[str] = []
    for region in _ALLOWED_REGIONS:
        geography_sequence.extend([region] * geo_counts[region])

    rows: list[dict] = []
    for idx, geography in enumerate(geography_sequence, start=1):
        segment, customer_type = _SEGMENT_MAP[(idx - 1) % len(_SEGMENT_MAP)]
        rows.append(
            {
                "segment_key": f"SEG{idx:03d}",
                "segment": segment,
                "segment_customer_type": customer_type,
                "segment_geography": geography,
            }
        )

    if output == "records":
        return rows

    if output == "dataframe":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for output='dataframe'. "
                "Use output='records' or install pandas."
            ) from exc
        return pd.DataFrame(
            rows,
            columns=["segment_key", "segment", "segment_customer_type", "segment_geography"],
        )

    raise ValueError("output must be either 'records' or 'dataframe'")
