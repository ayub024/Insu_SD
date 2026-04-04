"""Parquet writer utilities with ClickHouse-friendly schemas."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from writers.writer_router import (
    DIM_COLUMNS,
    FACT_COLUMNS,
    FACT_DECIMAL_COLUMNS,
    normalize_fact_row,
    normalize_row,
    parquet_table_name,
)

_TWOPLACES = Decimal("0.01")


def _parse_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def _parse_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_decimal(value: Any) -> Decimal:
    if value in ("", None):
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(_TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _parse_date(value: Any):
    if value in ("", None):
        return None
    try:
        from datetime import date

        return date.fromisoformat(str(value))
    except Exception:
        return None


_DIM_SCHEMAS: dict[str, pa.Schema] = {
    "dim_policy": pa.schema(
        [
            ("policy_key", pa.string()),
            ("policy_number", pa.string()),
            ("policy_status", pa.string()),
            ("policy_start_date", pa.date32()),
            ("policy_end_date", pa.date32()),
            ("tenure_years", pa.int32()),
        ]
    ),
    "dim_product": pa.schema(
        [
            ("product_key", pa.string()),
            ("product_name", pa.string()),
            ("line_of_business", pa.string()),
            ("coverage_type", pa.string()),
        ]
    ),
    "dim_date": pa.schema(
        [
            ("date_key", pa.int32()),
            ("date", pa.date32()),
            ("month", pa.int32()),
            ("quarter", pa.string()),
            ("year", pa.int32()),
            ("fiscal_year", pa.string()),
        ]
    ),
    "dim_channel": pa.schema(
        [
            ("channel_key", pa.string()),
            ("channel_type", pa.string()),
            ("sub_channel", pa.string()),
            ("active_flag", pa.bool_()),
        ]
    ),
    "dim_segment": pa.schema(
        [
            ("segment_key", pa.string()),
            ("segment", pa.string()),
            ("segment_customer_type", pa.string()),
            ("segment_geography", pa.string()),
        ]
    ),
    "dim_broker": pa.schema(
        [
            ("broker_key", pa.string()),
            ("broker_name", pa.string()),
            ("broker_type", pa.string()),
            ("broker_region", pa.string()),
            ("license_number", pa.string()),
        ]
    ),
    "dim_customer": pa.schema(
        [
            ("customer_key", pa.string()),
            ("customer_name", pa.string()),
            ("customer_entity_type", pa.string()),
            ("age_group", pa.string()),
            ("gender", pa.string()),
            ("customer_geography", pa.string()),
            ("income_band", pa.string()),
        ]
    ),
    "dim_underwriter": pa.schema(
        [
            ("underwriter_key", pa.string()),
            ("underwriter_name", pa.string()),
            ("team", pa.string()),
            ("underwriter_region", pa.string()),
            ("seniority_level", pa.string()),
            ("manager_name", pa.string()),
        ]
    ),
}

_FACT_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("policy_key", pa.string()),
        ("date_key", pa.int32()),
        ("product_key", pa.string()),
        ("segment_key", pa.string()),
        ("underwriter_key", pa.string()),
        ("broker_key", pa.string()),
        ("customer_key", pa.string()),
        ("channel_key", pa.string()),
        ("gross_written_premium", pa.decimal128(15, 2)),
        ("ibnr_amount", pa.decimal128(15, 2)),
        ("recoveries_amount", pa.decimal128(15, 2)),
        ("ceded_premium", pa.decimal128(15, 2)),
        ("reinsurance_recovery", pa.decimal128(15, 2)),
        ("net_earned_premium", pa.decimal128(15, 2)),
        ("incurred_claim_amount", pa.decimal128(15, 2)),
        ("paid_claim_amount", pa.decimal128(15, 2)),
        ("outstanding_reserve", pa.decimal128(15, 2)),
        ("operating_expense", pa.decimal128(15, 2)),
        ("acquisition_expense", pa.decimal128(15, 2)),
        ("new_policy_flag", pa.bool_()),
        ("renewal_flag", pa.bool_()),
    ]
)


def _cast_row_for_schema(row: dict[str, Any], schema: pa.Schema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in schema:
        name = field.name
        value = row.get(name)
        if pa.types.is_string(field.type):
            out[name] = "" if value in (None, "") else str(value)
        elif pa.types.is_integer(field.type):
            out[name] = _parse_int(value)
        elif pa.types.is_boolean(field.type):
            out[name] = _parse_bool(value)
        elif pa.types.is_date32(field.type):
            out[name] = _parse_date(value)
        elif pa.types.is_decimal(field.type):
            out[name] = _parse_decimal(value)
        else:
            out[name] = value
    return out


def write_dimension_parquet(
    dim_name: str,
    rows: list[dict[str, Any]],
    parquet_root: Path,
    overwrite: bool,
    chunk_size: int = 100_000,
) -> Path:
    schema = _DIM_SCHEMAS[dim_name]
    columns = DIM_COLUMNS[dim_name]
    parquet_root.mkdir(parents=True, exist_ok=True)
    path = parquet_root / f"{parquet_table_name(dim_name)}.parquet"
    if path.exists() and not overwrite:
        raise FileExistsError(f"Parquet file already exists: {path}")

    if not rows:
        # write empty table
        table = pa.Table.from_pylist([], schema=schema)
        pq.write_table(table, path)
        return path

    writer = None
    try:
        for idx in range(0, len(rows), chunk_size):
            chunk = rows[idx : idx + chunk_size]
            normalized = [normalize_row(r, columns) for r in chunk]
            casted = [_cast_row_for_schema(r, schema) for r in normalized]
            table = pa.Table.from_pylist(casted, schema=schema)
            if writer is None:
                writer = pq.ParquetWriter(path, schema=schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()

    return path


def append_fact_parquet(
    rows: list[dict[str, Any]],
    parquet_root: Path,
    partition_part_counters: dict[tuple[int, int], int],
    chunk_size: int = 100_000,
) -> list[Path]:
    if not rows:
        return []

    parquet_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    normalized_rows = [normalize_fact_row(r) for r in rows]
    for idx in range(0, len(normalized_rows), chunk_size):
        chunk = normalized_rows[idx : idx + chunk_size]
        casted: list[dict[str, Any]] = []
        for r in chunk:
            r2 = dict(r)
            for col in FACT_DECIMAL_COLUMNS:
                r2[col] = _parse_decimal(r2.get(col))
            casted.append(_cast_row_for_schema(r2, _FACT_SCHEMA))

        table = pa.Table.from_pylist(casted, schema=_FACT_SCHEMA)
        part_no = partition_part_counters.get((0, 0), 0)
        path = parquet_root / f"Fact_Policy-part-{part_no:03d}.parquet"
        pq.write_table(table, path)
        partition_part_counters[(0, 0)] = part_no + 1
        written.append(path)

    return written


def finalize_fact_parquet(parquet_root: Path) -> Path | None:
    """Merge Fact_Policy part files into one final Parquet file."""
    parquet_root.mkdir(parents=True, exist_ok=True)
    part_files = sorted(parquet_root.glob("Fact_Policy-part-*.parquet"))
    if not part_files:
        # Support older flattened names from previous layout.
        part_files = sorted(parquet_root.glob("Fact_Policy_*__part-*.parquet"))
    if not part_files:
        return None

    target = parquet_root / "Fact_Policy.parquet"

    writer: pq.ParquetWriter | None = None
    try:
        for file_path in part_files:
            table = pq.read_table(file_path, schema=_FACT_SCHEMA)
            if writer is None:
                writer = pq.ParquetWriter(target, schema=table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()

    for file_path in part_files:
        if file_path.exists() and file_path != target:
            file_path.unlink()

    return target
