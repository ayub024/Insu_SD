"""Channel dimension builder for the synthetic insurance star schema."""

from __future__ import annotations

from typing import Literal

import numpy as np


_CHANNELS = [
    ("Direct", "Digital"),
    ("Direct", "Call Center"),
    ("Broker", "Independent Broker"),
    ("Broker", "Wholesale Broker"),
    ("Bancassurance", "Bank Branch"),
    ("Bancassurance", "Bank Digital"),
]


def build_dim_channel(
    inactive_pct: float = 0.05,
    seed: int = 20260215,
    output: Literal["records", "dataframe"] = "records",
):
    """Build Dim_Channel rows with strict schema columns.

    Output columns (strict):
    - channel_key
    - channel_type
    - sub_channel
    - active_flag

    Args:
        inactive_pct: Share of channels to mark inactive for realism (small value).
        seed: Deterministic seed for reproducible active/inactive assignment.
        output: 'records' for list[dict], 'dataframe' for pandas DataFrame.
    """
    if not 0.0 <= inactive_pct < 1.0:
        raise ValueError("inactive_pct must be in [0.0, 1.0)")

    total = len(_CHANNELS)
    inactive_count = int(round(total * inactive_pct))

    rng = np.random.default_rng(int(seed))
    inactive_indices = set(
        rng.choice(np.arange(total), size=inactive_count, replace=False).tolist()
        if inactive_count > 0
        else []
    )

    rows: list[dict] = []
    for idx, (channel_type, sub_channel) in enumerate(_CHANNELS, start=1):
        rows.append(
            {
                "channel_key": f"CHN{idx:03d}",
                "channel_type": channel_type,
                "sub_channel": sub_channel,
                "active_flag": (idx - 1) not in inactive_indices,
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
        return pd.DataFrame(rows, columns=["channel_key", "channel_type", "sub_channel", "active_flag"])

    raise ValueError("output must be either 'records' or 'dataframe'")


def active_channels(channel_rows: list[dict]) -> list[dict]:
    """Return only active channel rows for policy creation use."""
    return [row for row in channel_rows if bool(row["active_flag"])]
