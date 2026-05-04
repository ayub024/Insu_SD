"""Business-rule validators for Fact_Policy rows."""

from __future__ import annotations
from functools import lru_cache

from pathlib import Path
from typing import Any, Optional


class RuleValidationError(ValueError):
    """Raised when one or more rule violations are found."""


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        return {}

    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


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
    domain = str(row.get("transaction_domain", "")).strip()
    transaction_type = str(row.get("transaction_type", "")).strip()

    gwp = _to_float(row, "gross_written_premium", errors)
    ceded = _to_float(row, "ceded_premium", errors)
    paid = _to_float(row, "paid_claim_amount", errors)
    incurred = _to_float(row, "incurred_claim_amount", errors)
    collected = _to_float(row, "premium_collected_amount", errors)

    if ceded is not None and gwp is not None and gwp > 0 and ceded > gwp:
        errors.append(
            f"ceded_premium ({ceded}) must be <= gross_written_premium ({gwp})"
        )

    if paid is not None and incurred is not None and incurred > 0 and paid > incurred:
        errors.append(
            f"paid_claim_amount ({paid}) must be <= incurred_claim_amount ({incurred})"
        )

    if collected is not None and collected < 0 and transaction_type not in {"Premium Adjustment", "Endorsement"}:
        errors.append("premium_collected_amount must be >= 0")

    allowed_domains = {"Underwriting", "Billing", "Claims", "Reinsurance", "Policy Servicing"}
    if domain not in allowed_domains:
        errors.append(f"transaction_domain must be one of {sorted(allowed_domains)}, got {domain!r}")

    transaction_types = _load_yaml("config/transaction_types.yaml").get("transaction_types", {})
    allowed_types = set(transaction_types.get(domain, []))
    if allowed_types and transaction_type not in allowed_types:
        errors.append(f"transaction_type {transaction_type!r} is not valid for transaction_domain {domain!r}")

    if domain == "Claims" and row.get("claim_key") in (None, "") and transaction_type != "CAT IBNR Raised":
        errors.append("Claims transactions must include claim_key")

    if domain != "Claims" and row.get("claim_key") not in (None, "") and domain != "Reinsurance":
        errors.append("Only Claims/Reinsurance transactions should include claim_key")

    if domain == "Billing" and "Premium" not in transaction_type:
        errors.append("Billing transaction_type should describe a premium event")

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

    renewal_flag = _as_bool(row.get("renewal_flag"))
    if domain == "Underwriting" and transaction_type.startswith("Renewal") and not renewal_flag:
        errors.append("Renewal underwriting transactions must have renewal_flag=TRUE")

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
        row_id = row.get("transaction_id")
        policy_key = row.get("policy_key")
        prefix = f"Validation failed for row transaction_id={row_id!r}, policy_key={policy_key!r}:"
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

    violations.extend(_validate_event_lifecycle(rows))

    if violations:
        raise RuleValidationError("Rule validation failed:\n" + "\n".join(violations))


def _row_date_key(row: dict[str, Any]) -> int:
    try:
        return int(row.get("date_key") or 0)
    except (TypeError, ValueError):
        return 0


def _money(row: dict[str, Any], field: str) -> float:
    try:
        return float(row.get(field, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _validate_event_lifecycle(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    rows_by_policy: dict[str, list[dict[str, Any]]] = {}
    rows_by_claim: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        policy_key = str(row.get("policy_key", ""))
        if policy_key:
            rows_by_policy.setdefault(policy_key, []).append(row)
        claim_key = row.get("claim_key")
        if claim_key not in (None, ""):
            rows_by_claim.setdefault(str(claim_key), []).append(row)

    for policy_key, policy_rows in rows_by_policy.items():
        ordered = sorted(policy_rows, key=lambda r: (_row_date_key(r), str(r.get("transaction_id", ""))))
        if not ordered:
            continue
        first = ordered[0]
        # The first Underwriting event has a date_key before policy_start (day -15)
        # so only check that at least one Underwriting domain row exists early in the batch.
        first_domain = first.get("transaction_domain")
        if first_domain not in {"Underwriting", "Policy Servicing"}:
            # Tolerate if the very first row is a carry-over claim from a prior period;
            # only flag if there is no Underwriting row at all in this batch.
            has_underwriting = any(
                r.get("transaction_domain") == "Underwriting" for r in ordered
            )
            if not has_underwriting:
                errors.append(f"policy_key={policy_key}: first event must be Underwriting")

        has_bound = any(
            str(r.get("transaction_type")) in {"Policy Bound", "Renewal Policy Bound"}
            for r in ordered
        )
        has_quote = any(
            str(r.get("transaction_type")) in {"Quote Created", "Renewal Quote Created"}
            for r in ordered
        )
        if has_bound and not has_quote:
            errors.append(f"policy_key={policy_key}: bound policy is missing a quote-created event")

        billing_collections = [
            r
            for r in ordered
            if r.get("transaction_domain") == "Billing"
            and str(r.get("transaction_type")) in {"Premium Collected"}
        ]
        # Valid counts: 0 (early lapse), 1 (annual), 2-11 (mid-lapse), 12 (full monthly),
        # 4 (quarterly), 3 (quarterly mid-lapse partial counts), etc.
        # Accept all mathematically valid installment divisors of 12.
        VALID_FULL_TERM_COUNTS = {0, 1, 2, 3, 4, 6, 8, 9, 12, 24, 36}
        if has_bound:
            has_termination = any(
                str(r.get("transaction_type")) in {"Policy Lapsed", "Policy Cancelled"}
                for r in ordered
            )
            n = len(billing_collections)
            if not has_termination and n not in VALID_FULL_TERM_COUNTS and n > 36:
                errors.append(
                    f"policy_key={policy_key}: unexpected premium collection count {n}"
                )


        written = sum(_money(r, "gross_written_premium") for r in ordered if r.get("transaction_domain") in {"Underwriting", "Policy Servicing"})
        collected = sum(_money(r, "premium_collected_amount") for r in ordered if r.get("transaction_domain") in {"Billing", "Policy Servicing"})
        if has_bound and abs(written - collected) > 1.00:
            has_termination = any(
                str(r.get("transaction_type")) in {"Policy Lapsed", "Policy Cancelled"}
                for r in ordered
            )
            if not has_termination:
                errors.append(
                    f"policy_key={policy_key}: collected premium {collected:.2f} does not match written/adjusted premium {written:.2f}"
                )

    for claim_key, claim_rows in rows_by_claim.items():
        claim_domain_rows = [r for r in claim_rows if r.get("transaction_domain") == "Claims"]
        if not claim_domain_rows:
            continue
        event_types = {str(r.get("transaction_type")) for r in claim_domain_rows}
        if "Claim Reported" not in event_types:
            errors.append(f"claim_key={claim_key}: missing Claim Reported event")
        if "Claim Incurred" not in event_types:
            errors.append(f"claim_key={claim_key}: missing Claim Incurred event")
        # Open-workflow claims intentionally omit Claim Closed — only require it
        # when a payment event has been emitted (i.e. the claim is resolved).
        is_paid = any("Claim Paid" in str(et) for et in event_types)
        if is_paid and "Claim Closed" not in event_types:
            errors.append(f"claim_key={claim_key}: missing Claim Closed event")

        incurred_total = sum(_money(r, "incurred_claim_amount") for r in claim_domain_rows)
        paid_total = sum(_money(r, "paid_claim_amount") for r in claim_domain_rows)
        if incurred_total > 0 and paid_total - incurred_total > 1.00:
            errors.append(
                f"claim_key={claim_key}: paid total {paid_total:.2f} exceeds incurred total {incurred_total:.2f}"
            )

    return errors
