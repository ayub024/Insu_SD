# Technical Architecture & Implementation Guide

This document explains the internal mechanics of the synthetic generator and provides "How-To" guides for modifying the codebase.

## 1. The Generation Execution Pipeline

When `main.py` is executed, the entire process flows sequentially to maintain deterministic state:

1. **Initialization (`scenario_engine.py`)**: Loads `scenario.yaml` and initializes the target limits (e.g., total policies).
2. **Master Data Hydration (`master_data/`)**: Seeds the baseline dimensions. 
   - `underwriter_master.py` assigns underwriters to regions based on `underwriter_rules.yaml`.
   - `policy_master.py` is initialized as an empty registry.
3. **The Simulation Loop (`main.py`)**: The engine iterates month by month.
   - **Step A: Workforce Scaling**: `growth_curves.yaml` triggers hiring waves if policy targets demand it.
   - **Step B: Policy Generation**: `policy_generator.py` spawns new policies. It handles the probabilistic assignments of Product, Geography, and Underwriters.
   - **Step C: Claim Generation**: `claim_generator.py` loops through policies and assigns them 0, 1, or multiple claims based on the `claim_count_dist.yaml` probabilities. It applies lognormal severity math to generate the financial loss.
4. **Validation (`validators/rule_validator.py`)**: The generated events are passed to the validator to ensure accounting parity (e.g., `paid_claim_amount` cannot exceed `incurred_claim_amount`).
5. **Disk Write (`writers/parquet_writer.py`)**: The validated array is flushed to disk via PyArrow chunking.

---

## 2. Developer "How-To" Guides

### How to Add a New Dimension
If you want to add a new `Dim_Agency` dimension to the Star Schema:
1. **Define Schema**: Add `dim_agency` to `DIM_COLUMNS` in `writers/writer_router.py`.
2. **Add FK to Fact**: Add `agency_key` to `FACT_COLUMNS` in `writers/writer_router.py`.
3. **Master Data**: Create `master_data/agency_master.py` to hold the agency cache in memory during generation. Ensure the generator logic populates this mapping.
4. **Write Loop**: Update `main.py`'s finalize block to write the `agency_master` state to parquet.

---

## 3. PyArrow Streaming
The dataset generates massive volumes of rows, which would crash standard pandas DataFrames.
- We use `pyarrow.Table.from_pylist()`.
- Data is written via `pyarrow.parquet.ParquetWriter`.
- The `parquet_writer.py` keeps the file handle open and appends chunks (e.g., 50,000 rows at a time), keeping RAM usage strictly under 1GB even for millions of simulated claims.
