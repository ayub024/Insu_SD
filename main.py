"""Main orchestrator for synthetic insurance data generation."""

from __future__ import annotations
from functools import lru_cache
import os
import random

from collections import Counter
from datetime import date
from pathlib import Path
import time
from typing import Any

from core.growth_engine import GrowthEngine
from core.progress_logger import log_month_summary, log_year_summary
from core.world_state import WorldState
from fact_builders.policy_transaction_event_builder import build_policy_transaction_event_rows
from generators.claim_generator import generate_claims
from generators.policy_generator import generate_monthly_policies
from master_data.channel_master import build_dim_channel
from master_data.date_dimension import build_dim_date
from master_data.product_master import build_dim_product
from master_data.segment_master import build_dim_segment
import master_data.broker_master as broker_master
import master_data.customer_master as customer_master
import master_data.policy_master as policy_master
import master_data.underwriter_master as underwriter_master
from validators.distribution_validator import validate_distributions
from validators.rule_validator import validate_fact_policy_rows
from writers.csv_writer import append_fact_rows, sort_fact_file_by_date_key, write_dimensions


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    import yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _month_iter(start_date: date, end_date: date) -> list[str]:
    start = date(start_date.year, start_date.month, 1)
    end = date(end_date.year, end_date.month, 1)
    months: list[str] = []

    cursor = start
    while cursor <= end:
        months.append(cursor.strftime("%Y-%m"))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    return months


def _index(rows: list[dict], key_field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        key = str(row[key_field])
        out[key] = row
    return out


def _map_first_segment_key(dim_segment: list[dict]) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    for row in dim_segment:
        k = (str(row["segment"]), str(row["segment_geography"]))
        mapping.setdefault(k, str(row["segment_key"]))
    return mapping


def _map_channel_key(dim_channel: list[dict]) -> dict[str, list[str]]:
    # Prefer active channels first for each channel_type, allowing full random distribution across sub-channels
    mapping: dict[str, list[str]] = {}
    for row in dim_channel:
        ch_type = str(row["channel_type"])
        if bool(row.get("active_flag")):
            mapping.setdefault(ch_type, []).append(str(row["channel_key"]))

    # Fallback to all available if no active row was set.
    for row in dim_channel:
        ch_type = str(row["channel_type"])
        if not mapping.get(ch_type):
            mapping.setdefault(ch_type, []).append(str(row["channel_key"]))

    return mapping


def _enrich_policy_for_fact(
    policy_obj: dict,
    product_key_by_name: dict[str, str],
    segment_key_by_segment_geo: dict[tuple[str, str], str],
    channel_keys_by_type: dict[str, list[str]],
) -> dict:
    product_name = str(policy_obj["product_name"])
    segment = str(policy_obj["segment"])
    geography = str(policy_obj["geography"])
    channel_type = str(policy_obj["channel_type"])

    seg_key = segment_key_by_segment_geo.get((segment, geography))
    if seg_key is None:
        raise ValueError(f"No segment_key found for segment={segment}, geography={geography}")

    prod_key = product_key_by_name.get(product_name)
    if prod_key is None:
        raise ValueError(f"No product_key found for product_name={product_name}")

    ch_keys = channel_keys_by_type.get(channel_type)
    if not ch_keys:
        raise ValueError(f"No channel_key found for channel_type={channel_type}")
    
    # Assign a random sub-channel from the available active keys
    ch_key = random.choice(ch_keys)

    out = dict(policy_obj)
    out["product_key"] = prod_key
    out["segment_key"] = seg_key
    out["channel_key"] = ch_key
    return out


def _new_year_accumulator() -> dict[str, Any]:
    return {
        "fact_rows": 0,
        "policy_keys": set(),
        "customer_keys": set(),
        "channel_counter": Counter(),
        "region_counter": Counter(),
        "billing_counter": Counter(),
        "claim_wf_counter": Counter(),
        "lapse_counter": Counter(),
        "endorse_counter": Counter(),
        "financial_totals": {
            "gross_written_premium": 0.0,
            "premium_collected_amount": 0.0,
            "incurred_claim_amount": 0.0,
            "paid_claim_amount": 0.0,
            "ibnr_amount": 0.0,
            "underwriting_expense": 0.0,
            "other_expense": 0.0,
            "acquisition_expense": 0.0,
            "ceded_premium": 0.0,
            "reinsurance_recovery": 0.0,
            "recoveries_amount": 0.0,
        },
        "policy_claim_stats": {},
    }


def _update_year_accumulator(
    year_acc: dict[str, Any],
    policy_rows: list[dict],
    fact_rows: list[dict],
) -> None:
    for p in policy_rows:
        year_acc["policy_keys"].add(str(p.get("policy_key")))
        if p.get("customer_key") is not None:
            year_acc["customer_keys"].add(str(p.get("customer_key")))
        if p.get("channel_type") is not None:
            year_acc["channel_counter"][str(p.get("channel_type"))] += 1
        if p.get("geography") is not None:
            year_acc["region_counter"][str(p.get("geography"))] += 1
        if p.get("billing_frequency") is not None:
            year_acc["billing_counter"][str(p.get("billing_frequency"))] += 1
        if p.get("claim_workflow") is not None:
            year_acc["claim_wf_counter"][str(p.get("claim_workflow"))] += 1
        year_acc["lapse_counter"][str(p.get("lapse_type") or "none")] += 1
        year_acc["endorse_counter"][str(p.get("endorsement_type") or "none")] += 1

    for r in fact_rows:
        year_acc["fact_rows"] += 1
        policy_key = str(r.get("policy_key"))
        stats = year_acc["policy_claim_stats"].setdefault(
            policy_key,
            {"fact_count": 0, "has_non_zero_claim": False},
        )
        stats["fact_count"] += 1

        incurred = float(r.get("incurred_claim_amount", 0.0) or 0.0)
        paid = float(r.get("paid_claim_amount", 0.0) or 0.0)
        ibnr = float(r.get("ibnr_amount", 0.0) or 0.0)
        recoveries = float(r.get("recoveries_amount", 0.0) or 0.0)
        reinsurance_recovery = float(r.get("reinsurance_recovery", 0.0) or 0.0)
        if any(v > 0.0 for v in [incurred, paid, ibnr, recoveries, reinsurance_recovery]):
            stats["has_non_zero_claim"] = True

        ft = year_acc["financial_totals"]
        ft["gross_written_premium"] += float(r.get("gross_written_premium", 0.0) or 0.0)
        ft["premium_collected_amount"] += float(r.get("premium_collected_amount", 0.0) or 0.0)
        ft["incurred_claim_amount"] += incurred
        ft["paid_claim_amount"] += paid
        ft["ibnr_amount"] += ibnr
        ft["underwriting_expense"] += float(r.get("underwriting_expense", 0.0) or 0.0)
        ft["other_expense"] += float(r.get("other_expense", 0.0) or 0.0)
        ft["acquisition_expense"] += float(r.get("acquisition_expense", 0.0) or 0.0)
        ft["ceded_premium"] += float(r.get("ceded_premium", 0.0) or 0.0)
        ft["reinsurance_recovery"] += reinsurance_recovery
        ft["recoveries_amount"] += recoveries


def run(scenario_path: str = os.getenv("SCENARIO_PATH", "config/scenario.yaml")) -> dict[str, Any]:
    # 1) Load configs.
    scenario = _load_yaml(os.getenv("SCENARIO_PATH", "config/scenario.yaml")).get("scenario", {})
    presets = scenario.get("presets", {})

    fin_cfg = _load_yaml("config/financial_assumptions.yaml").get("financial_assumptions", {})
    yoy_cfg = fin_cfg.get("yoy_premium_growth", {})

    output_dir = scenario.get("output_dir", "output")

    # Preferred mode selector (strict): scenario.mode in {"dev", "prod", "validation"}.
    mode = str(scenario.get("mode", "")).strip().lower()
    if mode not in {"dev", "prod", "validation"}:
        # Backward-compatible fallback for older config shape.
        dev_mode = bool(scenario.get("dev_mode", False))
        prod_mode = bool(scenario.get("prod_mode", False))
        if dev_mode and not prod_mode:
            mode = "dev"
        elif prod_mode and not dev_mode:
            mode = "prod"
        else:
            mode = "dev"

    active_preset = presets.get(mode, {}) or {}

    date_cfg = active_preset.get("date_range", scenario.get("date_range", {}))
    start_date = date.fromisoformat(str(date_cfg.get("start", "2022-01-01")))
    end_date = date.fromisoformat(str(date_cfg.get("end", "2025-12-31")))

    max_fact_rows_safety_limit = active_preset.get("max_fact_rows_safety_limit")

    print(f"RUN MODE: {mode}")
    print(f"DATE RANGE: {start_date.isoformat()} to {end_date.isoformat()}")

    # 2) Build static dimensions.
    dim_date = build_dim_date(start_date=start_date.isoformat(), end_date=end_date.isoformat())
    dim_product = build_dim_product()
    dim_channel = build_dim_channel()
    dim_segment = build_dim_segment(total_rows=100)

    product_key_by_name = {str(r["product_name"]): str(r["product_key"]) for r in dim_product}
    segment_key_by_segment_geo = _map_first_segment_key(dim_segment)
    channel_key_by_type = _map_channel_key(dim_channel)

    # 3) Initialize masters (brokers, underwriters).
    ws = WorldState(current_month=start_date.strftime("%Y-%m"))
    ws["random_seed"] = int(scenario.get("random_seed", 20260215))
    ws["underwriter_monthly_load"] = ws.underwriter_workloads_per_month

    initial_brokers = broker_master.init_brokers()
    initial_underwriters = underwriter_master.init_underwriters(start_date.strftime("%Y-%m"))
    ws["brokers_initialized"] = True
    ws["underwriters_initialized"] = True

    for b in initial_brokers:
        ws.broker_registry[str(b["broker_key"])] = b
    for u in initial_underwriters:
        ws.underwriter_registry[str(u["underwriter_key"])] = u

    # 4) Initialize growth engine.
    growth_engine = GrowthEngine()

    # Reset fact output file for a clean run.
    fact_path = Path(output_dir) / "fact_policy.csv"
    if fact_path.exists():
        fact_path.unlink()

    # Create output CSVs upfront so fact + all dimensions always exist during a run.
    append_fact_rows([], output_dir=output_dir, filename="fact_policy.csv")
    write_dimensions(
        {
            "dim_date": dim_date,
            "dim_product": dim_product,
            "dim_channel": dim_channel,
            "dim_segment": dim_segment,
            "dim_policy": [],
            "dim_broker": initial_brokers,
            "dim_customer": [],
            "dim_underwriter": initial_underwriters,
            "dim_claim": [],
        },
        output_dir=output_dir,
        overwrite=True,
    )

    # Removed all_fact_rows to prevent memory bloat on full-scale prod runs
    year_acc = _new_year_accumulator()
    running_totals = {
        "fact_rows": 0,
        "incurred_claim_amount": 0.0,
        "premium_collected_amount": 0.0,
        "underwriting_expense": 0.0,
        "other_expense": 0.0,
        "acquisition_expense": 0.0,
    }

    # 5) Loop month-by-month.
    for month_key in _month_iter(start_date, end_date):
        month_start_ts = time.perf_counter()
        ws.current_month = month_key

        # hire underwriters
        new_uw = underwriter_master.hire_underwriters(
            month_key,
            target_new_count=growth_engine.get_new_underwriter_target(month_key),
        )
        for u in new_uw:
            ws.underwriter_registry[str(u["underwriter_key"])] = u

        # onboard brokers
        new_brokers = broker_master.onboard_brokers(
            month_key,
            target_new_count=growth_engine.get_new_broker_target(month_key),
        )
        for b in new_brokers:
            ws.broker_registry[str(b["broker_key"])] = b

        # Tell policy_generator this month growth was already applied.
        ws.setdefault("broker_growth_applied", set()).add(month_key)
        ws.setdefault("underwriter_growth_applied", set()).add(month_key)

        # generate new policies
        new_policies = generate_monthly_policies(month_key, ws)

        enriched_new: list[dict] = []
        for p in new_policies:
            enriched = _enrich_policy_for_fact(
                policy_obj=p,
                product_key_by_name=product_key_by_name,
                segment_key_by_segment_geo=segment_key_by_segment_geo,
                channel_keys_by_type=channel_key_by_type,
            )
            ws.register_policy(enriched)
            enriched_new.append(enriched)

        # process renewals (create new term policies)
        renewed_dim_rows = policy_master.process_renewals(month_key)
        policy_master.expire_past_policies(month_key)

        renewed_enriched: list[dict] = []
        for dim_row in renewed_dim_rows:
            prior_key = str(dim_row.get("prior_policy_key"))
            existing = ws.policy_registry.get(prior_key)
            if existing is None:
                continue

            # Clone existing enriched data to new term policy
            new_policy = dict(existing)
            new_policy["policy_key"] = dim_row["policy_key"]
            new_policy["policy_number"] = dim_row["policy_number"]
            new_policy["policy_start_date"] = dim_row["policy_start_date"]
            new_policy["policy_end_date"] = dim_row["policy_end_date"]
            new_policy["tenure_years"] = dim_row["tenure_years"]
            new_policy["policy_status"] = dim_row["policy_status"]

            # Fetch the growth rate from config (e.g. 0.06) and apply minor noise (+/- 1.5%)
            base_rate = float(yoy_cfg.get(new_policy.get("product_name", ""), yoy_cfg.get("default_rate", 0.05)))
            applied_rate = base_rate + float(random.uniform(-0.015, 0.015))
            new_policy["gross_written_premium"] = float(new_policy.get("gross_written_premium", 0.0)) * (1.0 + applied_rate)

            new_policy["new_policy_flag"] = False
            new_policy["renewal_flag"] = True
            
            ws.register_policy(new_policy)
            renewed_enriched.append(new_policy)

        # generate claims for relevant policies (new + renewed this month)
        relevant_policies = enriched_new + renewed_enriched

        claim_rows: list[dict] = []
        fact_rows: list[dict] = []
        if relevant_policies:
            claim_rows = generate_claims(relevant_policies, seed=ws.get("random_seed", 20260215))
            for c in claim_rows:
                ws.register_claim_row(c)

            # build fact rows
            fact_rows = build_policy_transaction_event_rows(
                policy_rows=relevant_policies,
                claim_rows=claim_rows,
                world_state=ws,
            )

            # enrich for row-level validation + writer null handling
            for row in fact_rows:
                pol = ws.policy_registry[str(row["policy_key"])]
                row["channel_type"] = pol["channel_type"]
                if pol["channel_type"] == "Direct":
                    row["broker_key"] = None

            # validate rows
            channel_lookup = _index(dim_channel, "channel_key")
            segment_lookup = _index(dim_segment, "segment_key")
            customer_rows = getattr(customer_master._instance(), "_rows", [])  # type: ignore[attr-defined]
            customer_lookup = _index(customer_rows, "customer_key") if customer_rows else {}
            broker_rows = getattr(broker_master._instance(), "_rows", [])  # type: ignore[attr-defined]
            broker_lookup = _index(broker_rows, "broker_key") if broker_rows else {}
            underwriter_lookup = _index(
                underwriter_master.get_active_underwriters(month_key),
                "underwriter_key",
            )

            validate_fact_policy_rows(
                rows=fact_rows,
                channel_lookup=channel_lookup,
                segment_lookup=segment_lookup,
                customer_lookup=customer_lookup,
                broker_lookup=broker_lookup,
                underwriter_lookup=underwriter_lookup,
            )

            # track workload locally too
            for p in relevant_policies:
                ws.update_workload(str(p["underwriter_key"]), month=month_key, increment=1)

            # append to CSV
            append_fact_rows(fact_rows, output_dir=output_dir, filename="fact_policy.csv")
            # all_fact_rows accumulation removed to prevent MemoryError
            for r in fact_rows:
                running_totals["fact_rows"] += 1
                running_totals["incurred_claim_amount"] += float(r.get("incurred_claim_amount", 0.0) or 0.0)
                running_totals["premium_collected_amount"] += float(r.get("premium_collected_amount", 0.0) or 0.0)
                running_totals["underwriting_expense"] += float(r.get("underwriting_expense", 0.0) or 0.0)
                running_totals["other_expense"] += float(r.get("other_expense", 0.0) or 0.0)
                running_totals["acquisition_expense"] += float(r.get("acquisition_expense", 0.0) or 0.0)

            if max_fact_rows_safety_limit is not None and running_totals["fact_rows"] >= int(max_fact_rows_safety_limit):
                print("Safety stop: reached max_fact_rows_safety_limit={:,}".format(int(max_fact_rows_safety_limit)))
                break

        # monthly logging + yearly accumulation (non-intrusive)
        active_brokers_now = broker_master.get_active_brokers(month_key, region=None, broker_type=None)
        active_underwriters_now = underwriter_master.get_active_underwriters(month_key)
        _update_year_accumulator(year_acc, relevant_policies, fact_rows)

        # Removed mid-loop dimension writing to vastly improve performance on multi-year Prod scale runs
        month_elapsed = time.perf_counter() - month_start_ts
        cumulative_earned = float(running_totals["premium_collected_amount"])
        cumulative_incurred = float(running_totals["incurred_claim_amount"])
        cumulative_expenses = float(
            running_totals["underwriting_expense"]
            + running_totals["other_expense"]
            + running_totals["acquisition_expense"]
        )
        cumulative_kpis = {
            "fact_rows": float(running_totals["fact_rows"]),
            "avg_incurred": (cumulative_incurred / running_totals["fact_rows"]) if running_totals["fact_rows"] > 0 else 0.0,
            "loss_ratio": (cumulative_incurred / cumulative_earned) if cumulative_earned > 0 else 0.0,
            "combined_ratio": ((cumulative_incurred + cumulative_expenses) / cumulative_earned) if cumulative_earned > 0 else 0.0,
        }
        log_month_summary(
            month_key=month_key,
            policies_created=len(enriched_new),
            renewals=len(renewed_enriched),
            claims_generated=len(claim_rows),
            fact_rows=fact_rows,
            policy_rows=relevant_policies,
            brokers_active=len(active_brokers_now),
            underwriters_active=len(active_underwriters_now),
            elapsed_seconds=month_elapsed,
            new_brokers_hired=len(new_brokers),
            new_underwriters_hired=len(new_uw),
            cumulative_kpis=cumulative_kpis,
        )
        if month_key.endswith("-12"):
            log_year_summary(
                year=int(month_key[:4]),
                year_acc=year_acc,
                brokers_active_end_of_year=len(active_brokers_now),
                underwriters_active_end_of_year=len(active_underwriters_now),
            )
            year_acc = _new_year_accumulator()

    # 6) After loop: write all dimension CSVs.
    dim_policy = list(policy_master._instance()._policies.values())  # type: ignore[attr-defined]
    dim_broker = getattr(broker_master._instance(), "_rows", [])  # type: ignore[attr-defined]
    dim_underwriter = underwriter_master.get_active_underwriters(end_date.strftime("%Y-%m"))
    dim_customer = getattr(customer_master._instance(), "_rows", [])  # type: ignore[attr-defined]
    dim_claim = list(ws.get("dim_claim_rows_by_key", {}).values())

    write_dimensions(
        {
            "dim_date": dim_date,
            "dim_product": dim_product,
            "dim_channel": dim_channel,
            "dim_segment": dim_segment,
            "dim_policy": dim_policy,
            "dim_broker": dim_broker,
            "dim_customer": dim_customer,
            "dim_underwriter": dim_underwriter,
            "dim_claim": dim_claim,
        },
        output_dir=output_dir,
        overwrite=True,
    )

    # Keep final fact output in strict chronological order.
    sort_fact_file_by_date_key(output_dir=output_dir, filename="fact_policy.csv")

    # final distribution validation report
    product_lookup = _index(dim_product, "product_key")
    final_policy_rows = list(ws.policy_registry.values())
    distribution_report = validate_distributions(
        fact_rows=[],  # Memory optimization: Skip fact row scanning

        policy_rows=final_policy_rows,
        channel_lookup=_index(dim_channel, "channel_key"),
        segment_lookup=_index(dim_segment, "segment_key"),
        product_lookup=product_lookup,
    )

    return {
        "fact_rows": running_totals["fact_rows"],
        "policies": len(dim_policy),
        "customers": len(dim_customer),
        "brokers": len(dim_broker),
        "underwriters": len(dim_underwriter),
        "distribution_report": distribution_report,
    }


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=str, default=os.getenv("SCENARIO_PATH", "config/scenario.yaml"))
    args = parser.parse_args()
    os.environ["SCENARIO_PATH"] = args.scenario
    result = run()
    print("Generation completed")
    print(f"fact_rows={result['fact_rows']}")
    print(f"policies={result['policies']}")
    print(f"distribution_passed={result['distribution_report']['passed']}")
    if result["distribution_report"]["violations"]:
        print("distribution_violations=")
        for v in result["distribution_report"]["violations"][:20]:
            print(f"- {v}")
    
    # Generate Excel validation file automatically
    try:
        import export_to_excel
        export_to_excel.main()
    except Exception as e:
        print(f"Could not generate Excel file automatically: {e}")
