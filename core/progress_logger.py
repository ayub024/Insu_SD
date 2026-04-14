"""Non-intrusive progress logging for monthly and year-end KPIs."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _fmt_int(value: int) -> str:
    return f"{int(value):,}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _mix(counter: Counter[str], ordered_keys: list[str]) -> dict[str, float | None]:
    total = sum(counter.values())
    if total <= 0:
        return {k: None for k in ordered_keys}
    return {k: (100.0 * counter.get(k, 0) / total) for k in ordered_keys}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_zero_claim_fact_row(row: dict[str, Any]) -> bool:
    return (
        _safe_float(row.get("incurred_claim_amount")) == 0.0
        and _safe_float(row.get("paid_claim_amount")) == 0.0
        and _safe_float(row.get("ibnr_amount")) == 0.0
        and _safe_float(row.get("recoveries_amount")) == 0.0
        and _safe_float(row.get("reinsurance_recovery")) == 0.0
    )


def log_month_summary(
    month_key: str,
    policies_created: int,
    renewals: int,
    claims_generated: int,
    fact_rows: list[dict[str, Any]],
    policy_rows: list[dict[str, Any]],
    brokers_active: int,
    underwriters_active: int,
    elapsed_seconds: float,
    new_brokers_hired: int = 0,
    new_underwriters_hired: int = 0,
    cumulative_kpis: dict[str, float] | None = None,
) -> None:
    """Print a non-intrusive monthly progress summary."""
    fact_rows_written = len(fact_rows)

    channel_counter = Counter(str(p.get("channel_type")) for p in policy_rows if p.get("channel_type") is not None)
    region_counter = Counter(str(p.get("geography")) for p in policy_rows if p.get("geography") is not None)

    channel_mix = _mix(channel_counter, ["Direct", "Broker", "Bancassurance"])
    region_mix = _mix(region_counter, ["Midwest", "Southeast", "Northeast", "Southwest", "West"])

    avg_gwp = _avg([_safe_float(r.get("gross_written_premium")) for r in fact_rows])
    avg_incurred = _avg([_safe_float(r.get("incurred_claim_amount")) for r in fact_rows])

    loss_ratios: list[float] = []
    combined_ratios: list[float] = []
    for r in fact_rows:
        earned = _safe_float(r.get("premium_collected_amount"))
        if earned <= 0.0:
            continue
        incurred = _safe_float(r.get("incurred_claim_amount"))
        operating = _safe_float(r.get("underwriting_expense")) + _safe_float(r.get("other_expense"))
        acquisition = _safe_float(r.get("acquisition_expense"))
        loss_ratios.append(incurred / earned)
        combined_ratios.append((incurred + operating + acquisition) / earned)

    avg_loss_ratio = _avg(loss_ratios)
    avg_combined_ratio = _avg(combined_ratios)

    print(
        f"[{month_key}] policies_created={_fmt_int(policies_created)} "
        f"renewals={_fmt_int(renewals)} "
        f"claims_generated={_fmt_int(claims_generated)} "
        f"fact_rows_written={_fmt_int(fact_rows_written)}"
    )
    print(
        "          channel_mix: "
        f"Direct={_fmt_pct(channel_mix['Direct'])} "
        f"Broker={_fmt_pct(channel_mix['Broker'])} "
        f"Bancassurance={_fmt_pct(channel_mix['Bancassurance'])}"
    )
    print(
        "          region_mix: "
        f"Midwest={_fmt_pct(region_mix['Midwest'])} "
        f"Southeast={_fmt_pct(region_mix['Southeast'])} "
        f"Northeast={_fmt_pct(region_mix['Northeast'])} "
        f"Southwest={_fmt_pct(region_mix['Southwest'])} "
        f"West={_fmt_pct(region_mix['West'])}"
    )
    print(
        "          "
        f"avg_gwp={_fmt_money(avg_gwp)}  "
        f"avg_incurred={_fmt_money(avg_incurred)}  "
        f"avg_loss_ratio={_fmt_ratio(avg_loss_ratio)}  "
        f"avg_combined_ratio={_fmt_ratio(avg_combined_ratio)}"
    )
    print(
        "          "
        f"brokers_active={_fmt_int(brokers_active)}  "
        f"underwriters_active={_fmt_int(underwriters_active)}"
    )
    print(
        "          "
        f"new_brokers_hired={_fmt_int(new_brokers_hired)}  "
        f"new_underwriters_hired={_fmt_int(new_underwriters_hired)}"
    )
    if cumulative_kpis:
        c_rows = int(cumulative_kpis.get("fact_rows", 0))
        c_avg_incurred = cumulative_kpis.get("avg_incurred")
        c_loss_ratio = cumulative_kpis.get("loss_ratio")
        c_combined_ratio = cumulative_kpis.get("combined_ratio")
        print(
            "          cumulative: "
            f"fact_rows={_fmt_int(c_rows)}  "
            f"avg_incurred={_fmt_money(c_avg_incurred)}  "
            f"loss_ratio={_fmt_ratio(c_loss_ratio)}  "
            f"combined_ratio={_fmt_ratio(c_combined_ratio)}"
        )
    print(f"          completed in {elapsed_seconds:.2f}s")


def log_year_summary(
    year: int,
    year_acc: dict[str, Any],
    brokers_active_end_of_year: int,
    underwriters_active_end_of_year: int,
) -> None:
    """Print a year-end KPI summary using aggregated counters only."""
    total_earned = _safe_float(year_acc.get("financial_totals", {}).get("premium_collected_amount"))
    total_incurred = _safe_float(year_acc.get("financial_totals", {}).get("incurred_claim_amount"))
    total_operating = (
        _safe_float(year_acc.get("financial_totals", {}).get("underwriting_expense"))
        + _safe_float(year_acc.get("financial_totals", {}).get("other_expense"))
    )
    total_acquisition = _safe_float(year_acc.get("financial_totals", {}).get("acquisition_expense"))

    loss_ratio = (total_incurred / total_earned) if total_earned > 0 else None
    expense_ratio = ((total_operating + total_acquisition) / total_earned) if total_earned > 0 else None
    combined_ratio = (loss_ratio + expense_ratio) if loss_ratio is not None and expense_ratio is not None else None

    fact_rows = int(year_acc.get("fact_rows", 0))
    unique_policies = len(year_acc.get("policy_keys", set()))
    unique_customers = len(year_acc.get("customer_keys", set()))

    channel_mix = _mix(year_acc.get("channel_counter", Counter()), ["Direct", "Broker", "Bancassurance"])
    region_mix = _mix(year_acc.get("region_counter", Counter()), ["Midwest", "Southeast", "Northeast", "Southwest", "West"])

    avg_claims_per_policy = (fact_rows / unique_policies) if unique_policies > 0 else None

    policy_stats = year_acc.get("policy_claim_stats", {})
    zero_claim_policies = 0
    for stats in policy_stats.values():
        if not stats.get("has_non_zero_claim", False):
            zero_claim_policies += 1
    zero_claim_rate = (100.0 * zero_claim_policies / unique_policies) if unique_policies > 0 else None

    avg_incurred_per_claim = (total_incurred / fact_rows) if fact_rows > 0 else None
    total_paid = _safe_float(year_acc.get("financial_totals", {}).get("paid_claim_amount"))
    avg_paid_per_claim = (total_paid / fact_rows) if fact_rows > 0 else None

    ft = year_acc.get("financial_totals", {})

    print(f"==================== YEAR-END KPI SUMMARY: {year} ====================")
    print(
        f"fact_rows={_fmt_int(fact_rows)}   "
        f"unique_policies={_fmt_int(unique_policies)}   "
        f"unique_customers={_fmt_int(unique_customers)}"
    )
    print(
        "channel_mix: "
        f"Direct={_fmt_pct(channel_mix['Direct'])} "
        f"Broker={_fmt_pct(channel_mix['Broker'])} "
        f"Bancassurance={_fmt_pct(channel_mix['Bancassurance'])}"
    )
    print(
        "region_mix: "
        f"Midwest={_fmt_pct(region_mix['Midwest'])} "
        f"Southeast={_fmt_pct(region_mix['Southeast'])} "
        f"Northeast={_fmt_pct(region_mix['Northeast'])} "
        f"Southwest={_fmt_pct(region_mix['Southwest'])} "
        f"West={_fmt_pct(region_mix['West'])}"
    )
    print("")
    print("Financial KPIs (annual totals):")
    print(f"- total_gwp = {_fmt_money(_safe_float(ft.get('gross_written_premium')))}")
    print(f"- total_collected_premium = {_fmt_money(_safe_float(ft.get('premium_collected_amount')))}")
    print(f"- total_incurred = {_fmt_money(_safe_float(ft.get('incurred_claim_amount')))}")
    print(f"- total_paid = {_fmt_money(_safe_float(ft.get('paid_claim_amount')))}")
    print(f"- total_ibnr = {_fmt_money(_safe_float(ft.get('ibnr_amount')))}")
    print(f"- total_underwriting_expense = {_fmt_money(_safe_float(ft.get('underwriting_expense')))}")
    print(f"- total_other_expense = {_fmt_money(_safe_float(ft.get('other_expense')))}")
    print(f"- total_acquisition_expense = {_fmt_money(_safe_float(ft.get('acquisition_expense')))}")
    print(f"- total_ceded_premium = {_fmt_money(_safe_float(ft.get('ceded_premium')))}")
    print(f"- total_reinsurance_recovery = {_fmt_money(_safe_float(ft.get('reinsurance_recovery')))}")
    print(f"- total_recoveries_amount = {_fmt_money(_safe_float(ft.get('recoveries_amount')))}")
    print("")
    print("Ratios:")
    print(f"- loss_ratio = {_fmt_ratio(loss_ratio)}")
    print(f"- expense_ratio = {_fmt_ratio(expense_ratio)}")
    print(f"- combined_ratio = {_fmt_ratio(combined_ratio)}")
    print("")
    print("Claim KPIs:")
    print(f"- avg_claims_per_policy (use fact rows per policy) = {_fmt_ratio(avg_claims_per_policy)}")
    print(f"- 0-claim policy rate (should be ~30%) = {_fmt_pct(zero_claim_rate)}")
    print(f"- avg_incurred_per_claim = {_fmt_money(avg_incurred_per_claim)}")
    print(f"- avg_paid_per_claim = {_fmt_money(avg_paid_per_claim)}")
    print("")
    print("Workforce KPIs:")
    print(f"- brokers_active_end_of_year = {_fmt_int(brokers_active_end_of_year)}")
    print(f"- underwriters_active_end_of_year = {_fmt_int(underwriters_active_end_of_year)}")
    print("====================================================================")
