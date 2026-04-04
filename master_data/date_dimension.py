"""Date dimension builder for the synthetic insurance star schema."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal


def _fiscal_year_label(year: int) -> str:
    """Map calendar year to spec fiscal year label (e.g., 2022 -> FY22)."""
    return f"FY{year % 100:02d}"


def build_dim_date(
    start_date: str = "2022-01-01",
    end_date: str = "2025-12-31",
    output: Literal["records", "dataframe"] = "records",
):
    """Generate Dim_Date records with strict schema columns.

    Args:
        start_date: Inclusive ISO date string.
        end_date: Inclusive ISO date string.
        output: 'records' for list[dict], 'dataframe' for pandas DataFrame.

    Returns:
        list[dict] or pandas.DataFrame with these columns:
        - date_key (YYYYMMDD int)
        - date (ISO date string)
        - month (1..12)
        - quarter (Q1..Q4)
        - year (int)
        - fiscal_year (FY22..FY25 for spec window)
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    if start > end:
        raise ValueError("start_date must be <= end_date")

    rows: list[dict] = []
    current = start

    while current <= end:
        quarter_num = ((current.month - 1) // 3) + 1
        rows.append(
            {
                "date_key": int(current.strftime("%Y%m%d")),
                "date": current.isoformat(),
                "month": current.month,
                "quarter": f"Q{quarter_num}",
                "year": current.year,
                "fiscal_year": _fiscal_year_label(current.year),
            }
        )
        current += timedelta(days=1)

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
        return pd.DataFrame(rows)

    raise ValueError("output must be either 'records' or 'dataframe'")
