"""Business-rule validators for Fact_Policy rows."""

from __future__ import annotations

from typing import Any, Optional


class RuleValidationError(ValueError):
    """Raised when one or more rule violations are found."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        txt = value.strip().lower()
        if txt in {"true", "1", "yes", "y"}:
            return True
        if txt in {"false", "0", "no", "n"}:
            return False
    return bool(value)


def _to_float(row: dict, field: str, errors: list[str]) -> Optional[float]:
    value = row.get(field)
    if value is None:
        errors.append(f"Missing required numeric field '{field}'")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"Field '{field}' must be numeric, got {value!r}")
        return None


def _lookup_attr(
    row: dict,
    row_attr: str,
    key_attr: str,
    dim_lookup: Optional[dict[str, dict]],
    dim_attr: str,
    errors: list[str],
) -> Optional[Any]:
    """Resolve an attribute either from row or from dimension lookup by key."""
    if row_attr in row and row.get(row_attr) is not None:
        return row.get(row_attr)

    key = row.get(key_attr)
    if key is None:
        errors.append(f"Missing '{key_attr}' needed to resolve '{row_attr or dim_attr}'")
        return None

    if not dim_lookup:
        errors.append(
            f"No lookup provided for key '{key_attr}' to resolve '{dim_attr}'. "
            f"Provide dimension lookup or include '{row_attr}' in row context."
        )
        return None

    dim_row = dim_lookup.get(str(key))
    if dim_row is None:
        errors.append(f"No dimension row found for {key_attr}={key!r}")
        return None

    if dim_attr not in dim_row:
        errors.append(f"Dimension row for {key_attr}={key!r} missing '{dim_attr}'")
        return None

    return dim_row.get(dim_attr)


def validate_fact_policy_row(
    row: dict,
    channel_lookup: Optional[dict[str, dict]] = None,
    segment_lookup: Optional[dict[str, dict]] = None,
    customer_lookup: Optional[dict[str, dict]] = None,
    broker_lookup: Optional[dict[str, dict]] = None,
    underwriter_lookup: Optional[dict[str, dict]] = None,
) -> None:
    """Validate one Fact_Policy row against business rules.

    Lookups should be keyed by their *_key values as strings.
    """
    errors: list[str] = []

    earned = _to_float(row, "net_earned_premium", errors)
    gwp = _to_float(row, "gross_written_premium", errors)
    ceded = _to_float(row, "ceded_premium", errors)
    paid = _to_float(row, "paid_claim_amount", errors)
    incurred = _to_float(row, "incurred_claim_amount", errors)
    outstanding = _to_float(row, "outstanding_reserve", errors)

    if earned is not None and gwp is not None and earned > gwp:
        errors.append(
            f"net_earned_premium ({earned}) must be <= gross_written_premium ({gwp})"
        )

    if ceded is not None and gwp is not None and ceded > gwp:
        errors.append(
            f"ceded_premium ({ceded}) must be <= gross_written_premium ({gwp})"
        )

    if paid is not None and incurred is not None and paid > incurred:
        errors.append(
            f"paid_claim_amount ({paid}) must be <= incurred_claim_amount ({incurred})"
        )

    if outstanding is not None and incurred is not None and outstanding > incurred:
        errors.append(
            f"outstanding_reserve ({outstanding}) must be <= incurred_claim_amount ({incurred})"
        )

    channel_type = _lookup_attr(
        row=row,
        row_attr="channel_type",
        key_attr="channel_key",
        dim_lookup=channel_lookup,
        dim_attr="channel_type",
        errors=errors,
    )

    broker_key = row.get("broker_key")
    if channel_type == "Direct" and broker_key is not None:
        errors.append("If channel_type = Direct then broker_key must be NULL")

    if channel_type == "Broker" and broker_key is None:
        errors.append("If channel_type = Broker then broker_key must be NOT NULL")

    new_flag = _as_bool(row.get("new_policy_flag"))
    renewal_flag = _as_bool(row.get("renewal_flag"))
    if new_flag == renewal_flag:
        errors.append(
            "new_policy_flag XOR renewal_flag violated: exactly one must be TRUE"
        )

    segment_geography = _lookup_attr(
        row=row,
        row_attr="segment_geography",
        key_attr="segment_key",
        dim_lookup=segment_lookup,
        dim_attr="segment_geography",
        errors=errors,
    )

    underwriter_region = _lookup_attr(
        row=row,
        row_attr="underwriter_region",
        key_attr="underwriter_key",
        dim_lookup=underwriter_lookup,
        dim_attr="underwriter_region",
        errors=errors,
    )

    if segment_geography is not None and underwriter_region is not None:
        if str(underwriter_region) != str(segment_geography):
            errors.append(
                "Underwriter region must match segment geography: "
                f"underwriter_region={underwriter_region!r}, segment_geography={segment_geography!r}"
            )

    customer_geography = _lookup_attr(
        row=row,
        row_attr="customer_geography",
        key_attr="customer_key",
        dim_lookup=customer_lookup,
        dim_attr="customer_geography",
        errors=errors,
    )

    broker_region = _lookup_attr(
        row=row,
        row_attr="broker_region",
        key_attr="broker_key",
        dim_lookup=broker_lookup,
        dim_attr="broker_region",
        errors=errors,
    ) if broker_key is not None else None

    if errors:
        row_id = row.get("id")
        policy_key = row.get("policy_key")
        prefix = f"Validation failed for row id={row_id!r}, policy_key={policy_key!r}:"
        raise RuleValidationError(prefix + " " + " | ".join(errors))


def validate_fact_policy_rows(
    rows: list[dict],
    channel_lookup: Optional[dict[str, dict]] = None,
    segment_lookup: Optional[dict[str, dict]] = None,
    customer_lookup: Optional[dict[str, dict]] = None,
    broker_lookup: Optional[dict[str, dict]] = None,
    underwriter_lookup: Optional[dict[str, dict]] = None,
) -> None:
    """Validate all rows and raise one aggregated error if any row fails."""
    violations: list[str] = []
    for idx, row in enumerate(rows):
        try:
            validate_fact_policy_row(
                row=row,
                channel_lookup=channel_lookup,
                segment_lookup=segment_lookup,
                customer_lookup=customer_lookup,
                broker_lookup=broker_lookup,
                underwriter_lookup=underwriter_lookup,
            )
        except RuleValidationError as exc:
            violations.append(f"row_index={idx}: {exc}")

    if violations:
        raise RuleValidationError("Rule validation failed:\n" + "\n".join(violations))
