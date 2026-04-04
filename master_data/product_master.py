"""Product dimension builder for the synthetic insurance star schema."""

from __future__ import annotations

from pathlib import Path
from typing import Literal


def _derive_coverage_type(line_of_business: str) -> str:
    """Derive coverage type from the line-of-business label."""
    if "Property" in line_of_business:
        return "Property"
    if "Casualty" in line_of_business:
        return "Casualty"
    raise ValueError(f"Unsupported line_of_business for coverage_type derivation: {line_of_business}")


def _lob_code(line_of_business: str) -> str:
    mapping = {
        "Commercial Property": "COM_PROP",
        "Commercial Casualty": "COM_CAS",
        "Personal Property": "PER_PROP",
        "Personal Casualty": "PER_CAS",
    }
    code = mapping.get(line_of_business)
    if code is None:
        raise ValueError(f"Unsupported line_of_business for product_key coding: {line_of_business}")
    return code


def _name_code(product_name: str) -> str:
    name = product_name.strip().lower()
    explicit = {
        "building & contents": "BLDG",
        "business interruption": "BI",
        "inland marine": "MAR",
        "general liability": "GL",
        "commercial auto liability": "AUTO",
        "umbrella / excess": "UMB",
        "homeowners": "HOME",
        "renters": "RENT",
        "condo": "CONDO",
        "personal auto": "AUTO",
        "personal umbrella": "UMB",
        "motorcycle / recreational": "MOTO",
    }
    if name in explicit:
        return explicit[name]

    # Fallback: compact normalized token for any future product labels.
    chars: list[str] = []
    prev_is_sep = False
    for ch in product_name.upper():
        if ch.isalnum():
            chars.append(ch)
            prev_is_sep = False
        elif not prev_is_sep:
            chars.append("_")
            prev_is_sep = True
    token = "".join(chars).strip("_")
    while "__" in token:
        token = token.replace("__", "_")
    return token


def _product_key(line_of_business: str, product_name: str, seen: set[str], idx: int) -> str:
    base = f"PRD_{_lob_code(line_of_business)}_{_name_code(product_name)}"
    if base not in seen:
        seen.add(base)
        return base

    # Fallback suffix for uniqueness if collisions occur after normalization.
    suffix = idx
    candidate = f"{base}_{suffix:02d}"
    while candidate in seen:
        suffix += 1
        candidate = f"{base}_{suffix:02d}"
    seen.add(candidate)
    return candidate


def build_dim_product(
    products_config_path: str = "config/products.yaml",
    output: Literal["records", "dataframe"] = "records",
):
    """Load products config and generate strict Dim_Product rows.

    Output columns (strict):
    - product_key
    - product_name
    - line_of_business
    - coverage_type
    """
    config_path = Path(products_config_path)

    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to load products.yaml") from exc

    if not config_path.exists():
        raise FileNotFoundError(f"Products config not found: {config_path}")

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    products = payload.get("products", [])

    if not isinstance(products, list):
        raise ValueError("products.yaml must contain a 'products' list")

    rows: list[dict] = []
    seen_keys: set[str] = set()
    for idx, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each product entry must be a mapping")

        # Strictly consume only expected config fields.
        product_name = item.get("product_name")
        line_of_business = item.get("line_of_business")

        if not product_name or not line_of_business:
            raise ValueError("Each product must define product_name and line_of_business")

        rows.append(
            {
                "product_key": _product_key(
                    line_of_business=str(line_of_business),
                    product_name=str(product_name),
                    seen=seen_keys,
                    idx=idx,
                ),
                "product_name": str(product_name),
                "line_of_business": str(line_of_business),
                "coverage_type": _derive_coverage_type(str(line_of_business)),
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
        return pd.DataFrame(rows, columns=["product_key", "product_name", "line_of_business", "coverage_type"])

    raise ValueError("output must be either 'records' or 'dataframe'")
