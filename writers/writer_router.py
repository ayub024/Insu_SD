"""Shared writer routing utilities and schema metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Strict output column order.
DIM_COLUMNS: dict[str, list[str]] = {
    "dim_policy": [
        "policy_key",
        "policy_number",
        "policy_region",
        "policy_status",
        "policy_start_date",
        "policy_end_date",
        "policy_lapsed_date",
        "tenure_years",
        "renewal_cycle_number",
        "sum_insured",
        "risk_band",
        "insured_asset",
        "insured_asset_details",
        "billing_frequency",
        "billing_installments",
    ],
    "dim_product": [
        "product_key",
        "product_name",
        "line_of_business",
        "coverage_type",
    ],
    "dim_date": [
        "date_key",
        "date",
        "month",
        "quarter",
        "year",
        "fiscal_year",
    ],
    "dim_channel": [
        "channel_key",
        "channel_type",
        "sub_channel",
        "active_flag",
    ],
    "dim_segment": [
        "segment_key",
        "segment",
        "segment_customer_type",
        "segment_geography",
    ],
    "dim_broker": [
        "broker_key",
        "broker_name",
        "broker_type",
        "broker_region",
        "license_number",
    ],
    "dim_customer": [
        "customer_key",
        "customer_name",
        "customer_entity_type",
        "age_group",
        "gender",
        "customer_geography",
        "income_band",
    ],
    "dim_underwriter": [
        "underwriter_key",
        "underwriter_name",
        "team",
        "underwriter_region",
        "seniority_level",
        "manager_name",
    ],
    "dim_claim": [
        "claim_key",
        "claim_type",
        "claim_details",
        "claim_date",
        "claim_event_region",
        "event_severity",
    ],
}

FACT_COLUMNS: list[str] = [
    "transaction_id",
    "transaction_domain",
    "transaction_type",
    "policy_key",
    "date_key",
    "policy_issue_date",
    "product_key",
    "segment_key",
    "underwriter_key",
    "broker_key",
    "customer_key",
    "channel_key",
    "claim_key",
    "pricing_decision_type",
    "quoted_price",
    "bind_price",
    "indicated_premium",
    "expected_loss",
    "underwriting_expense",
    "other_expense",
    "acquisition_expense",
    "gross_written_premium",
    "premium_collected_amount",
    "ibnr_amount",
    "recoveries_amount",
    "ceded_premium",
    "reinsurance_recovery",
    "incurred_claim_amount",
    "paid_claim_amount",
    "renewal_flag",
]

FACT_DECIMAL_COLUMNS: set[str] = {
    "quoted_price",
    "bind_price",
    "indicated_premium",
    "expected_loss",
    "underwriting_expense",
    "other_expense",
    "acquisition_expense",
    "gross_written_premium",
    "premium_collected_amount",
    "ibnr_amount",
    "recoveries_amount",
    "ceded_premium",
    "reinsurance_recovery",
    "incurred_claim_amount",
    "paid_claim_amount",
}


@dataclass(frozen=True)
class RunPaths:
    output_root: Path
    run_id: str
    run_root: Path
    csv_root: Path
    parquet_root: Path


_RUN_CONTEXTS: dict[str, RunPaths] = {}


def _make_run_id() -> str:
    return datetime.now(tz=timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def get_run_paths(output_dir: str | Path = "output") -> RunPaths:
    key = str(Path(output_dir).resolve())
    existing = _RUN_CONTEXTS.get(key)
    if existing is not None:
        return existing

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = _make_run_id()
    run_root = output_root / run_id
    csv_root = run_root / "csv"
    parquet_root = run_root / "parquet"
    csv_root.mkdir(parents=True, exist_ok=True)
    parquet_root.mkdir(parents=True, exist_ok=True)
    (output_root / "latest_run_id.txt").write_text(run_id, encoding="utf-8")

    ctx = RunPaths(
        output_root=output_root,
        run_id=run_id,
        run_root=run_root,
        csv_root=csv_root,
        parquet_root=parquet_root,
    )
    _RUN_CONTEXTS[key] = ctx
    return ctx


def parquet_table_name(table_name: str) -> str:
    if table_name == "fact_policy":
        return "Fact_Policy"
    if table_name.startswith("dim_"):
        suffix = table_name.replace("dim_", "", 1)
        return "Dim_" + "_".join(part.capitalize() for part in suffix.split("_"))
    return table_name


def _format_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    raw = str(value).strip().lower()
    return "TRUE" if raw in {"true", "1", "yes", "y", "t"} else "FALSE"


def _format_decimal(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def csv_format_value(col: str, value: Any) -> str | int:
    if value is None or value == "":
        return ""
    if col in FACT_DECIMAL_COLUMNS:
        return _format_decimal(value)
    if col in {"renewal_flag", "active_flag"}:
        return _format_bool(value)
    if col in {"date_key", "month", "year", "tenure_years", "claim_key", "renewal_cycle_number"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return ""
    return str(value)


def normalize_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for col in columns:
        normalized[col] = csv_format_value(col, row.get(col))
    return normalized


def normalize_fact_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_row(row, FACT_COLUMNS)
    channel_type = row.get("channel_type", row.get("channel"))
    if channel_type == "Direct":
        normalized["broker_key"] = ""
    return normalized


def validate_keys(rows: list[dict[str, Any]], allowed_columns: list[str], table_name: str) -> None:
    allowed = set(allowed_columns)
    for idx, row in enumerate(rows):
        extras = set(row.keys()) - allowed
        if extras:
            raise ValueError(
                f"{table_name} row {idx} has extra columns not in schema: {sorted(extras)}"
            )
