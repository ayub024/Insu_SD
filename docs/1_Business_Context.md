# Business Context & Actuarial Features

This document explains the actuarial rules and logic embedded within this Claim-Level Baseline generator.

## 1. The Claim-Level Paradigm

This synthetic generator is designed around a strict **Claim-Level** grain. It does not track intermediate billing installments, quotes, or endorsements. Instead, it creates a direct, flat mapping between a Policy and its Claims.

### The Zero-Claim Baseline
In the real world, most insurance policies do not experience a claim. 
- To ensure the dataset accurately reflects policy exposure and premium volumes, **30% of policies are explicitly generated to have 0 claims**.
- For these policies, a single row is written to the `Fact_Policy` table. 
- In this row, the `gross_written_premium` and `net_earned_premium` are populated, but all claim-related financials (`incurred_claim_amount`, `paid_claim_amount`, `outstanding_reserve`) are set exactly to `$0.00`.

### Multiple Claims
If a policy experiences multiple claims (e.g., a Commercial Property with 2 distinct loss events):
- The policy will appear as **2 separate rows** in `Fact_Policy`.
- The `gross_written_premium` is repeated on both rows. 
- Analysts must be careful not to natively `SUM()` the premium without first deduplicating by `policy_key`.

## 2. Financial Mechanics

### Premium to Loss Ratios
The engine explicitly models realistic financial ratios:
- `ceded_premium` is modeled as 5-20% of Gross Written Premium (GWP).
- `operating_expense` is simulated as 10-15% of Earned Premium.
- `acquisition_expense` is 12-18% of GWP.

### Lognormal Severity Modeling
When a claim occurs, the financial impact (`incurred_claim_amount`) is not arbitrary. It is sampled via a product-adjusted **Lognormal Distribution**. 
To ensure claims don't breach physical possibility, the severity is capped as a fractional maximum of the GWP (e.g., a claim will rarely exceed configured multiples of the premium).

### Reserve Lifecycles
The engine simulates the accounting lifecycle of a claim:
- **Paid vs Incurred**: `paid_claim_amount` is severity-tiered (smaller claims pay out 75-90% immediately, while large claims pay out 40-65% immediately).
- **Outstanding Reserve**: Makes up 15-30% of the incurred amount.
- **IBNR**: Incurred But Not Reported is explicitly calculated as 5-15% of the incurred value, time-adjusted based on the policy age at the claim date.
