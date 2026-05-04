# Synthetic Insurance Generator - AI Context & Documentation

**[ATTENTION FUTURE LLMs / AI AGENTS]**  
If you are reading this file, you have been tasked with understanding, querying, or analyzing this synthetic insurance dataset. Read this document carefully. It contains the exact mathematical rules, architectural constraints, and intentional blindspots built into the data. **Do not hallucinate columns or metrics that contradict this document.**

---

## 1. Project Purpose & Execution
This project generates a highly realistic, actuarially sound synthetic Property & Casualty (P&C) insurance dataset using a strict Star Schema.

*   **To Run:** Execute `python main.py` in the root directory.
*   **Speed/Scale:** Target volume is controlled in `config/scenario.yaml`. The `prod` setting generates approximately 300,000 to 400,000 fact rows across 4 years (2022-2025) in ~3-4 minutes.
*   **Outputs:** CSV files are written to the `output/` directory.

## 2. Core Architecture & Grain
*   **Fact Table Grain (`fact_policy.csv`):** The grain is strictly **CLAIM-LEVEL**. 
    *   1 Row = 1 Claim. 
    *   If a policy has 0 claims, it will appear as exactly **1 row** with all claim financial amounts set to `$0.00`.
*   **Strict Schema:** There is exactly 1 Fact table (`fact_policy`) and 8 Dimension tables (`dim_policy`, `dim_product`, `dim_date`, `dim_channel`, `dim_segment`, `dim_broker`, `dim_customer`, `dim_underwriter`).

## 3. Mathematical Business Rules (Actuarial Realism)
We engineered specific actuarial phenomena into the data. If you write queries analyzing this data, you will find these exact trends:

1.  **Term-Level Policy Architecture:** 
    *   `policy_number` represents the base insurance contract (e.g., `POL-12345`).
    *   `policy_key` represents the specific 12-month annual term (e.g., `POL-12345-01`, `POL-12345-02`). 
    *   Renewals are tracked by the suffix.
2.  **Premium Inflation (Upward Pricing):** 
    *   There is a hardcoded compound inflation rate. `gross_written_premium` **always increases** by +4% to +8% upon every policy renewal. Premium deflation does not exist.
3.  **Expense Dynamics & New Business Strain:** 
    *   **Acquisition Expenses** are heavily penalized on Year 1 (New Business), ranging from 8% to 15%. 
    *   Upon renewal (`tenure_years > 0`), acquisition expenses drastically drop to ~2%. 
    *   *Result:* Older, renewed cohorts are mathematically guaranteed to have a lower Expense Ratio and higher profitability than new cohorts.
4.  **Macroeconomic Shock & Volatility:** 
    *   To prevent the "Law of Large Numbers" from perfectly flattening the data, claim severity is subjected to a **12-month cyclical Sine wave** combined with aggressive (+/- 25%) deterministic monthly noise. 
    *   Crucially, this shock is seeded by the **Claim Occurrence Date**, meaning aggregated month-to-month Loss Ratios will bounce violently and realistically (e.g., dropping from 0.72 to 0.54, then spiking to 0.81).
5.  **Net Earned Premium:** 
    *   We use `net_earned_premium`, which is calculated dynamically as `(GWP - ceded_premium) * elapsed_time_ratio`.

## 4. Intentional Blindspots (Do Not Hallucinate)
The following data dimensions and operational workflows were **intentionally omitted** from the synthetic generator. If a user asks a question requiring this data, you must gracefully explain that the schema does not support it:

*   **Cause of Loss / Perils:** The data records the financial `incurred_claim_amount`, but there is no `dim_peril` table. You cannot query for "Hurricane", "Fire", or "Water Damage".
*   **Reinsurance Treaties:** While the math for `ceded_premium` exists, there is no `dim_reinsurance` table. You cannot query specific Reinsurance Companies, Treaty Names, or Attachment Points.
*   **Settlement Lag / Claim Lifecycle:** The fact table records a single `date_key` representing the Claim Occurrence Date. There is no `reporting_date` or `closed_date`, making it impossible to calculate "Days to Settle".
*   **Mid-Term Cancellations & Billing:** The data captures the final financial snapshot of the policy term. There is no data regarding mid-term cancellation refunds, unpaid premium defaults, or late payment fees.
*   **Underwriting Exceptions & Quoting:** There is no pipeline data. You cannot query win/loss quoting ratios, competitor pricing, or declined applications.

## 5. Configuration Files
To adjust the behavior of the generator, modify the YAML files in the `config/` directory:
*   `scenario.yaml`: Adjust date ranges, target policy scales, channel distribution targets, and target loss ratios.
*   `financial_assumptions.yaml`: Adjust baseline severity scaling, expense caps, regional multipliers, and IBNR (Incurred But Not Reported) reserve targets.
