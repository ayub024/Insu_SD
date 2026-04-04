"""CSV writer facade that also writes Parquet outputs."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from writers.parquet_writer import append_fact_parquet, finalize_fact_parquet, write_dimension_parquet
from writers.writer_router import (
    DIM_COLUMNS,
    FACT_COLUMNS,
    get_run_paths,
    normalize_fact_row,
    normalize_row,
    validate_keys,
)

_PARTITION_PART_COUNTERS: dict[tuple[int, int], int] = {}


def _csv_table_path_csv_root(csv_root: Path, table_name: str) -> Path:
    csv_root.mkdir(parents=True, exist_ok=True)
    return csv_root / f"{table_name}.csv"


def _write_dimension_csv(path: Path, dim_name: str, rows: list[dict[str, Any]]) -> None:
    columns = DIM_COLUMNS[dim_name]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", delimiter=",")
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_row(row, columns))


def write_dimension(
    dim_name: str,
    rows: list[dict[str, Any]],
    output_dir: str | Path = "output",
    overwrite: bool = False,
) -> Path:
    if dim_name not in DIM_COLUMNS:
        raise ValueError(f"Unknown dimension '{dim_name}'. Allowed: {sorted(DIM_COLUMNS.keys())}")
    validate_keys(rows, DIM_COLUMNS[dim_name], dim_name)

    run = get_run_paths(output_dir)
    csv_path = _csv_table_path_csv_root(run.csv_root, dim_name)
    if csv_path.exists() and not overwrite:
        raise FileExistsError(f"Dimension file already exists: {csv_path}. Use overwrite=True.")

    _write_dimension_csv(csv_path, dim_name, rows)
    write_dimension_parquet(dim_name, rows, run.parquet_root, overwrite=overwrite)
    return csv_path


def write_dimensions(
    dims: dict[str, list[dict[str, Any]]],
    output_dir: str | Path = "output",
    overwrite: bool = False,
) -> dict[str, Path]:
    written: dict[str, Path] = {}
    for dim_name, rows in dims.items():
        written[dim_name] = write_dimension(
            dim_name=dim_name,
            rows=rows,
            output_dir=output_dir,
            overwrite=overwrite,
        )
    return written


def append_fact_rows(
    rows: list[dict[str, Any]],
    output_dir: str | Path = "output",
    filename: str = "fact_policy.csv",
) -> Path:
    validate_keys(rows, FACT_COLUMNS + ["channel_type", "channel"], "fact_policy")
    run = get_run_paths(output_dir)
    table_name = filename.removesuffix(".csv")
    csv_path = _csv_table_path_csv_root(run.csv_root, table_name)

    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FACT_COLUMNS, extrasaction="ignore", delimiter=",")
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(normalize_fact_row(row))

    append_fact_parquet(
        rows=rows,
        parquet_root=run.parquet_root,
        partition_part_counters=_PARTITION_PART_COUNTERS,
        chunk_size=100_000,
    )
    return csv_path


def sort_fact_file_by_date_key(
    output_dir: str | Path = "output",
    filename: str = "fact_policy.csv",
) -> Path:
    run = get_run_paths(output_dir)
    table_name = filename.removesuffix(".csv")
    csv_path = _csv_table_path_csv_root(run.csv_root, table_name)
    
    # Finalize fact parquet into a single file for the run.
    finalize_fact_parquet(run.parquet_root)
    return csv_path
