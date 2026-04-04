"""Portfolio realism checks for loss/expense/combined ratio behavior."""

from __future__ import annotations

from typing import Any, Optional


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compute_row_ratios(row: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    earned = _safe_float(row.get("net_earned_premium"))
    if earned <= 0.0:
        return None, None, None

    incurred = _safe_float(row.get("incurred_claim_amount"))
    operating = _safe_float(row.get("operating_expense"))
    acquisition = _safe_float(row.get("acquisition_expense"))

    loss_ratio = incurred / earned
    expense_ratio = (operating + acquisition) / earned
    combined_ratio = loss_ratio + expense_ratio
    return loss_ratio, expense_ratio, combined_ratio


def validate_realism(
    fact_rows: list[dict],
    min_combined_ratio: float = 0.5,
    max_combined_ratio: float = 1.8,
    max_outlier_share: float = 0.12,
) -> dict:
    """Compute realism metrics and evaluate combined ratio behavior.

    Args:
        fact_rows: Fact_Policy rows with financial measures.
        min_combined_ratio: Lower bound of the typical band.
        max_combined_ratio: Upper bound of the typical band.
        max_outlier_share: Max acceptable share of rows outside the typical band.

    Returns:
        Realism report dict with aggregate ratios, outlier stats, warnings, and pass/fail.
    """
    if min_combined_ratio >= max_combined_ratio:
        raise ValueError("min_combined_ratio must be less than max_combined_ratio")
    if not 0.0 <= max_outlier_share <= 1.0:
        raise ValueError("max_outlier_share must be in [0.0, 1.0]")

    total_earned = 0.0
    total_incurred = 0.0
    total_operating = 0.0
    total_acquisition = 0.0

    row_ratios: list[dict] = []
    outlier_count = 0
    skipped_zero_earned = 0

    for idx, row in enumerate(fact_rows):
        earned = _safe_float(row.get("net_earned_premium"))
        incurred = _safe_float(row.get("incurred_claim_amount"))
        operating = _safe_float(row.get("operating_expense"))
        acquisition = _safe_float(row.get("acquisition_expense"))

        total_earned += earned
        total_incurred += incurred
        total_operating += operating
        total_acquisition += acquisition

        loss_ratio, expense_ratio, combined_ratio = _compute_row_ratios(row)
        if combined_ratio is None:
            skipped_zero_earned += 1
            continue

        is_outlier = combined_ratio < min_combined_ratio or combined_ratio > max_combined_ratio
        if is_outlier:
            outlier_count += 1

        row_ratios.append(
            {
                "row_index": idx,
                "id": row.get("id"),
                "policy_key": row.get("policy_key"),
                "loss_ratio": loss_ratio,
                "expense_ratio": expense_ratio,
                "combined_ratio": combined_ratio,
                "is_outlier": is_outlier,
            }
        )

    if total_earned > 0.0:
        portfolio_loss_ratio = total_incurred / total_earned
        portfolio_expense_ratio = (total_operating + total_acquisition) / total_earned
        portfolio_combined_ratio = portfolio_loss_ratio + portfolio_expense_ratio
    else:
        portfolio_loss_ratio = 0.0
        portfolio_expense_ratio = 0.0
        portfolio_combined_ratio = 0.0

    evaluated_rows = len(row_ratios)
    outlier_share = (outlier_count / evaluated_rows) if evaluated_rows > 0 else 0.0

    warnings: list[str] = []
    if evaluated_rows == 0:
        warnings.append("No rows with net_earned_premium > 0 were available for realism ratio checks.")

    portfolio_in_band = min_combined_ratio <= portfolio_combined_ratio <= max_combined_ratio
    too_many_outliers = outlier_share > max_outlier_share

    if not portfolio_in_band:
        warnings.append(
            "Portfolio combined ratio is outside typical range "
            f"[{min_combined_ratio:.2f}, {max_combined_ratio:.2f}]: "
            f"{portfolio_combined_ratio:.4f}"
        )

    if too_many_outliers:
        warnings.append(
            f"Too many row-level combined ratio outliers: "
            f"{outlier_share:.2%} exceeds threshold {max_outlier_share:.2%}."
        )

    passed = portfolio_in_band and not too_many_outliers

    return {
        "passed": passed,
        "assumptions": {
            "typical_combined_ratio_min": min_combined_ratio,
            "typical_combined_ratio_max": max_combined_ratio,
            "max_outlier_share": max_outlier_share,
        },
        "portfolio": {
            "loss_ratio": portfolio_loss_ratio,
            "expense_ratio": portfolio_expense_ratio,
            "combined_ratio": portfolio_combined_ratio,
            "in_typical_band": portfolio_in_band,
        },
        "row_level": {
            "evaluated_rows": evaluated_rows,
            "skipped_rows_zero_or_missing_earned": skipped_zero_earned,
            "outlier_count": outlier_count,
            "outlier_share": outlier_share,
            "outlier_threshold_exceeded": too_many_outliers,
        },
        "warnings": warnings,
        "sample_outliers": [r for r in row_ratios if r["is_outlier"]][:25],
    }
