# Configuration Tuning Guide

This document explains how to safely tune the synthetic engine parameters using YAML configuration files without modifying the underlying Python code.

## Complete YAML Reference

| File | Purpose | Key Attributes to Modify |
|------|---------|--------------------------|
| `scenario.yaml` | The master controller. Sets total targets, run dates, and high-level claim frequencies. | `target_scale.total_policies`, `claims.zero_claim_rate_by_product` |
| `growth_curves.yaml` | Controls the hiring of brokers and underwriters to match policy scale. | `brokers.end_count`, `underwriters.end_count` |
| `claim_count_dist.yaml` | If a policy has a claim, this dictates *how many* claims it has. | `pct_1_claim`, `pct_4_plus` |
| `financial_assumptions.yaml` | Controls all the dollar math via lognormal statistics. | `severity_mean`, `max_multiple_of_gwp` |
| `scenario_traits.yaml` | Controls deterministic routing (billing, lapses, endorsements). | `premium_schedules`, `endorsements` |

---

## Scenario Tuning Walkthroughs

### Scenario A: Simulating a High-Growth Startup
To simulate a company that is experiencing hyper-growth in the Personal Auto space:
1. Open `scenario.yaml`. Change `target_scale.total_policies` to `250000`.
2. Open `growth_curves.yaml`. Change the `hiring_waves` `increment_pct` from `0.10` to `0.45` in the later years.
3. Open `products.yaml`. Adjust the `pct_of_total_policies` for "Personal Auto" to `0.50` (50% of the book).

### Scenario B: Simulating a Severe Catastrophic Year
To test if your BI dashboards can handle massive tail-risk losses on Commercial properties:
1. Open `scenario.yaml`. Lower the `zero_claim_rate_by_product` for "Building & Contents" from `0.60` to `0.40`.
2. Open `financial_assumptions.yaml`. Increase the `max_multiple_of_gwp` for `commercial_lines` from `200.0` to `1000.0`. 
*(This allows a $5k policy to experience a $5 Million claim).*
3. Open `financial_assumptions.yaml`. Increase the `tail_lognormal.mean` to `11.5`.

---

## Resolving Validation Errors
The engine has a strict internal accounting validator (`validators/rule_validator.py`). If you change YAML parameters too aggressively, the generator may fail.

**Common Error**: `distribution_passed=False`
**Reason**: You tightened a parameter (like reducing claim frequencies to 0.01) but the dataset sample size (`target_policies`) was too small for the random generator to hit the statistical target.
**Fix**: Increase `total_policies` so the law of large numbers allows the distribution to stabilize, or widen the tolerance thresholds in `validation_scenario.yaml`.

**Common Error**: `paid_claim_amount exceeds incurred_claim_amount`
**Reason**: You broke the financial physics. Claims cannot pay out more cash than the liability reserve recognizes.
**Fix**: Revert `claim_financials.yaml` or ensure that `Reinsurance` recoveries aren't artificially offsetting the `incurred` metrics.
