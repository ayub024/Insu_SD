# Business Context & Actuarial Features

This document explains *what* the synthetic engine simulates and *why* it is designed this way. It provides the necessary context on the realism embedded in the dataset.

## 1. The Event-Driven Lifecycle Simulation

Unlike simplistic data generators that create static "snapshots" of policies, this engine generates a chronological, event-driven ledger. This ensures that point-in-time analysis (e.g., "What was our exposure on March 15th?") is mathematically accurate.

### Example Policy Timeline View
Consider a 1-year Commercial Property policy billed quarterly that experiences a mid-term claim:

- **Day 1 (Jan 1)**: `Quote Created` event is logged.
- **Day 5 (Jan 5)**: `Policy Bound` event is logged. Gross Written Premium (GWP) of $12,000 is recognized.
- **Day 5 (Jan 5)**: `Premium Collected` event logs the first $3,000 installment.
- **Day 90 (Apr 1)**: `Premium Collected` event logs the second $3,000 installment.
- **Day 150 (May 30)**: `Claim Reported` event is logged (no financials yet).
- **Day 152 (Jun 1)**: `Claim Incurred` event is logged with $50,000 in reserves (liability recognized).
- **Day 180 (Jul 1)**: `Premium Collected` event logs the third $3,000 installment.
- **Day 200 (Jul 20)**: `Claim Paid - Final` event logs a $48,000 cash outflow and releases the remaining reserve.
- **Day 365 (Dec 31)**: `Policy Expired` event marks the end of the term.
- **Day 365 (Dec 31)**: `Renewal Policy Bound` triggers the next 12-month cycle, linking to the same `policy_number`.

Because every action is an explicit row in the `Fact_Policy` table, BI tools can accurately recreate this timeline and calculate metrics like Net Earned Premium as time progresses.

## 2. Advanced Actuarial Features

### Multi-Year Policy Tenures
The engine intelligently assigns non-standard term lengths based on the Line of Business:
- **Commercial Lines**: Assigned 12, 24, or 36-month terms.
- **Personal Lines**: Assigned 6, 9, or 12-month terms.
The `renewal_cycle_number` tracks consecutive terms for customer retention analysis.

### Dynamic Premium Schedules
Premium is rarely collected all at once. The engine assigns a `premium_schedule` (Annual, Semi-Annual, Quarterly, Monthly) and mathematically distributes the `premium_collected_amount` across `billing_installments` throughout the active months of the policy.

### Catastrophic Large Loss Modeling (The Math)
To ensure the dataset exhibits realistic long-tail risk, we simulate severe outliers using a mathematically capped Lognormal distribution.

When a claim is triggered, the engine pulls from a standard lognormal curve ($$\mu, \sigma$$). However, for commercial lines, a tail-risk event can spike up to `200.0x` the policy's premium. For example:
- **Policy Premium**: $5,000
- **Standard Claim**: $2,500
- **Catastrophic Claim**: $850,000 (A factory fire).

Without these massive outliers, the data would look perfectly normally distributed, which fails to test the resilience of downstream analytics tools that must handle unpredictable loss ratios.

### Underwriting Manager Attribution
Organizational hierarchy is deterministically simulated. An underwriter is mapped to a specific Team and Region. The engine uses a hashing algorithm on those traits to assign a permanent Manager name (e.g., "Reese Brown" is always the manager for "Personal Lines - Midwest"). This allows BI developers to build robust drill-down dashboards (Manager -> Underwriter -> Policy).
