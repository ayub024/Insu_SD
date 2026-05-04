# Technical Architecture & Implementation Guide

This document explains the internal mechanics of the synthetic generator and provides "How-To" guides for modifying the codebase.

## 1. The Generation Execution Pipeline

When `main.py` is executed, the entire process flows sequentially to maintain deterministic state:

1. **Initialization (`scenario_engine.py`)**: Loads `scenario.yaml` and initializes the target limits (e.g., total policies) and the global timeline `date_range`.
2. **Master Data Hydration (`master_data/`)**: Seeds the baseline dimensions. 
   - `underwriter_master.py` assigns underwriters to regions.
   - `policy_master.py` is initialized as an empty registry.
3. **The Simulation Loop (`main.py`)**: The engine enters a strict `while current_month <= end_month:` loop.
   - **Step A: Workforce Scaling**: `growth_curves.yaml` triggers hiring waves in `_ensure_workforce()` if policy targets demand it.
   - **Step B: Policy Generation**: `policy_generator.py` spawns new policies. It handles the probabilistic assignments of Product, Tenure, Geography, and Premium Schedules.
   - **Step C: Billing Generation**: Iterates through active policies in `policy_master` and generates scheduled `Premium Collected` events based on their installment integers.
   - **Step D: Claim Generation**: `claim_generator.py` loops through active policies, applying lognormal severity math to generate new claims and updates reserves on open claims.
   - **Step E: Renewals**: Evaluates expiring policies and triggers `Renewal Bound` events.
4. **Validation (`validators/rule_validator.py`)**: The monthly batch of events is passed to the validator to ensure accounting parity (e.g., `paid_claim_amount` cannot exceed `incurred_claim_amount`).
5. **Disk Write (`writers/parquet_writer.py`)**: The validated array is flushed to disk via PyArrow chunking.

---

## 2. Developer "How-To" Guides

### How to Add a New Transaction Event
If you need to simulate a new domain event (e.g., `Audit Premium Adjustment`):
1. **Add to Schema**: Add the name to `config/transaction_types.yaml`.
2. **Update the Generator**: Inside `generators/policy_generator.py` (or similar), add the logic that probabilistically triggers this event and creates a raw dictionary payload.
3. **Map to Fact**: Open `fact_builders/policy_transaction_event_builder.py`. Write a mapping method that takes your raw dictionary and outputs the strict `FACT_COLUMNS` format. Be sure to map financial amounts to their respective columns and `None` to everything else.
4. **Validate**: If your event has strict business rules, add a check to `validators/rule_validator.py`.

### How to Add a New Dimension
If you want to add `Dim_Agency`:
1. **Define Schema**: Add `dim_agency` to `DIM_COLUMNS` in `writers/writer_router.py`.
2. **Add FK to Fact**: Add `agency_key` to `FACT_COLUMNS` in `writers/writer_router.py`.
3. **Master Data**: Create `master_data/agency_master.py` to hold the agency cache in memory during generation.
4. **Write Loop**: Update `main.py`'s finalize block to write `agency_master.get_all()` to parquet.

---

## 3. Idempotent Randomization (`RandomManager`)

To ensure the dataset is 100% reproducible for AI Knowledge Graph testing, we use a custom wrapper around Python's `random`. 

**The Rule**: You can NEVER call `random.randint()`. You must call `rng.randint(entity_seed="unique_string")`.

The `RandomManager` hashes the `entity_seed` (e.g., `hash(policy_id + "|claim_severity")`) and temporarily seeds the random generator with that integer. This guarantees that Policy #1234 will *always* experience a $50k claim on Day 150, regardless of whether you run the simulation for 1 year or 5 years.

## 4. PyArrow Streaming
The dataset generates millions of rows, which would crash standard pandas DataFrames.
- We use `pyarrow.Table.from_pylist()`.
- Data is written via `pyarrow.parquet.ParquetWriter`.
- The `parquet_writer.py` keeps the file handle open and appends chunks (e.g., 50,000 rows at a time), keeping RAM usage strictly under 1GB.
