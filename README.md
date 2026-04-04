# Synthetic Insurance Generator

## What this project does
This project will generate synthetic insurance data for a star-schema model defined in `docs/spec.md`.
The target output includes one fact table (`Fact_Policy`) at claim-level grain and strict dimension tables, with no extra or missing columns.

## Project structure
- `config/`: Scenario and generation configuration files.
- `core/`: Shared core utilities and orchestration modules.
- `master_data/`: Reference/master data builders (for example: products, channels, underwriters, brokers).
- `generators/`: Data generation modules.
- `fact_builders/`: Fact table construction modules.
- `validators/`: Data quality and business-rule validation modules.
- `writers/`: Output writing/export modules (CSV and optional parquet).
- `output/`: Generated datasets.
- `docs/`: Specifications and project documentation.

## How to run it
Generation logic is intentionally not implemented yet.
Once the runner script is added, run from the project root:

```bash
python run.py --scenario config/default_scenario.yaml --out output/
```

## Generated files
When implementation is added, expected outputs in `output/` are:
- `Fact_Policy.csv`
- `Dim_Policy.csv`
- `Dim_Product.csv`
- `Dim_Date.csv`
- `Dim_Channel.csv`
- `Dim_Segment.csv`
- `Dim_Broker.csv`
- `Dim_Customer.csv`
- `Dim_Underwriter.csv`

(Optional parquet versions may also be produced.)

## Config locations
All runtime/scenario configuration should live in `config/`.
Recommended initial files:
- `config/default_scenario.yaml`
- `config/test_scenario.yaml`
