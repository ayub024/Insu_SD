# Configuration Tuning Guide

This document explains how to safely tune the synthetic engine parameters using YAML configuration files without modifying the underlying Python code.

## Complete YAML Reference

| File | Purpose | Key Attributes to Modify |
|------|---------|--------------------------|
| `scenario.yaml` | The master controller. Sets total targets, run dates, and high-level claim frequencies. | `target_scale.total_policies` |
| `growth_curves.yaml` | Controls the hiring of brokers and underwriters to match policy scale. | `brokers.end_count`, `underwriters.end_count` |
| `products.yaml` | Defines the product portfolio and the mix of business lines. | `pct_of_total_policies` |
| `claim_count_dist.yaml` | Dictates the probability of a policy having 1, 2, 3, or 4+ claims. | `pct_1_claim`, `pct_4_plus` |
| `financial_assumptions.yaml` | Controls all the dollar math via lognormal statistics. | `severity_mean`, `max_multiple_of_gwp` |
| `gwp_ranges.yaml` | Controls the minimum and maximum possible Gross Written Premium assigned to a policy by product. | `min_gwp`, `max_gwp` |

---

## Scenario Tuning Walkthroughs

### Scenario A: Simulating a High-Growth Startup
To simulate a company that is experiencing hyper-growth in the Personal Auto space:
1. Open `scenario.yaml`. Change `target_scale.total_policies` to `250000`.
2. Open `growth_curves.yaml`. Change the `hiring_waves` `increment_pct` from `0.10` to `0.45` in the later years.
3. Open `products.yaml`. Adjust the `pct_of_total_policies` for "Personal Auto" to `0.50` (50% of the book).

### Scenario B: Simulating a Severe Catastrophic Year
To test if your BI dashboards can handle massive tail-risk losses on Commercial properties:
1. Open `financial_assumptions.yaml`. Increase the `max_multiple_of_gwp` for `commercial_lines` from `50.0` to `500.0`. 
*(This allows a $5k policy to experience a $2.5 Million claim).*
2. Open `claim_count_dist.yaml`. Shift the "Building & Contents" distribution so that `pct_4_plus` increases, creating a higher frequency of multi-claim policies.

---

## Resolving Validation Errors
The engine has a strict internal accounting validator (`validators/rule_validator.py`). If you change YAML parameters too aggressively, the generator may fail.

**Common Error**: `paid_claim_amount exceeds incurred_claim_amount`
**Reason**: You broke the financial physics. Claims cannot pay out more cash than the liability reserve recognizes.
**Fix**: Ensure your lognormal scaling factors in `financial_assumptions.yaml` respect the rule that `paid_claim_amount + outstanding_reserve <= incurred_claim_amount`.
