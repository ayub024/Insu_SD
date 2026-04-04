Create a markdown document docs/SPEC.md that contains the full requirements for the synthetic insurance generator project.

The project generates synthetic data for an insurance star schema.
The output tables must strictly follow the schema below (no extra columns, no missing columns).

DATE RANGE:
- 2022-01-01 to 2025-12-31 (4 years)

GEOGRAPHY:
Use only 5 regions (ignore international and ignore 46 states):
- Midwest 40%
- Southeast 25%
- Northeast 15%
- Southwest 10%
- West 10%

FACT TABLE RULE:
Fact_Policy is the ONLY fact table.
Fact_Policy stores CLAIM-LEVEL rows.
Add a primary key column: id (string or int).
Grain: 1 row = 1 claim for a policy.
Multiple rows per policy_key are allowed because a policy can have multiple claims.
30% of policies must have 0 claims and still appear in Fact_Policy with exactly 1 row where claim amounts are 0.

FACT_Policy columns (STRICT):
- id
- policy_key
- date_key (YYYYMMDD) = claim occurrence date (transaction date)
- product_key
- segment_key
- underwriter_key
- broker_key (nullable if channel is Direct)
- customer_key
- channel_key
- gross_written_premium
- ibnr_amount
- recoveries_amount
- ceded_premium
- reinsurance_recovery
- net_earned_premium
- incurred_claim_amount
- paid_claim_amount
- outstanding_reserve
- operating_expense
- acquisition_expense
- new_policy_flag
- renewal_flag

DIMENSIONS (STRICT):
Dim_Policy:
- policy_key
- policy_number
- policy_status
- policy_start_date
- policy_end_date
- tenure_years

Dim_Product:
- product_key
- product_name
- line_of_business
- coverage_type

Dim_Date:
- date_key
- date
- month
- quarter
- year
- fiscal_year

Dim_Channel:
- channel_key
- channel_type
- sub_channel
- active_flag

Dim_Segment:
- segment_key
- segment
- customer_type
- geography

Dim_Broker:
- broker_key
- broker_name
- broker_type
- region
- license_number

Dim_Customer:
- customer_key
- customer_name
- customer_type
- age_group
- gender
- geography
- income_band

Dim_Underwriter:
- underwriter_key
- underwriter_name
- team
- region
- seniority_level
- manager_name

PRODUCTS:
Commercial Property:
- Building & Contents (15% claims mix, GWP 8000-25000)
- Business Interruption (9%, 3000-12000)
- Inland Marine (6%, 2000-10000)

Commercial Casualty:
- General Liability (12%, 5000-20000)
- Commercial Auto Liability (8%, 4000-15000)
- Umbrella / Excess (5%, 2500-12000)

Personal Property:
- Homeowners (18%, 900-2500)
- Renters (4%, 150-400)
- Condo (3%, 400-1200)

Personal Casualty:
- Personal Auto (15%, 1200-3500)
- Personal Umbrella (3%, 250-800)
- Motorcycle / Recreational (2%, 300-1200)

CLAIM COUNT DISTRIBUTION (per product, applies to policies with claims):
Building & Contents: 70% 1-claim, 20% 2-claim, 8% 3-claim, 2% 4+.
Business Interruption: 85/12/3/0.
Inland Marine: 75/18/7/0.
General Liability: 80/15/5/0.
Commercial Auto Liability: 65/25/8/2.
Umbrella/Excess: 90/8/2/0.
Homeowners: 75/18/7/0.
Renters: 85/12/3/0.
Condo: 80/15/5/0.
Personal Auto: 60/25/10/5.
Personal Umbrella: 92/6/2/0.
Motorcycle/Recreational: 70/20/10/0.

CLAIM OCCURRENCE DATE:
Uniform random between policy_start_date and policy_end_date.

CHANNEL DISTRIBUTION:
- Direct 40-60%
- Broker 20-40%
- Bancassurance 10-20%

CHANNEL/BROKER RULES:
- If channel_type = Broker => broker_key MUST be present.
- If channel_type = Direct => broker_key MUST be NULL.
- active_flag = TRUE required for channel usage.
- Broker region must match customer geography and segment geography.

SEGMENT RULES:
Segments:
- Retail => Individual
- SME => Business
- Enterprise => Business

CUSTOMER RULES:
- Personal lines => Customer Type = Individual
- Commercial lines => Customer Type = Business Entity
- Customer geography must align with segment geography.

UNDERWRITER RULES:
- Underwriter region must align with customer or segment geography.
- Underwriters have team specialization aligned with LOB.
- Senior underwriters handle higher premium policies and complex products.
- If GWP >= 18000 => seniority_level must be Senior or Lead.
- If GWP >= 9000 => seniority_level should be Mid or Senior.
- Else => seniority_level should be Junior or Mid.
- Complexity products that bias toward senior assignment:
  - Business Interruption
  - Umbrella / Excess
  - Commercial Auto Liability
- Do not introduce premium outliers beyond configured product ranges.

PREMIUM / CLAIM METRIC RANGES:
- ceded_premium = 5-20% of GWP
- reinsurance_recovery tied to incurred using ceded share:
  - ceded_share = ceded_premium / GWP
  - reinsurance_recovery = incurred_claim_amount * ceded_share * noise
  - noise in [0.9, 1.1]
  - clamp to [0, incurred_claim_amount]
- incurred_claim_amount sampled via product-adjusted lognormal severity (not a fixed 55-65% GWP rule), with per-product cap as fraction of GWP
- recoveries_amount = 5-20% of incurred (product-adjusted)
- paid_claim_amount is severity-tiered:
  - small: incurred < 2000 => paid ratio 0.75-0.90
  - medium: 2000 <= incurred < 10000 => paid ratio 0.60-0.75
  - large: incurred >= 10000 => paid ratio 0.40-0.65
- outstanding_reserve = 15-30% of incurred, with paid + outstanding <= incurred
- ibnr_amount = 5-15% of incurred (time-adjusted based on policy age at claim date)
- operating_expense = 10-15% of earned premium (segment/product-adjusted; biased lower by segment in implementation)
- acquisition_expense = 12-18% of GWP (depends on channel and new/renewal; biased by context in implementation)

PREMIUM RULES:
- earned_premium is policy-level and repeated on every claim row
- earned_premium uses full-term ratio (not earned-to-date at claim date):
  - new policy: GWP * earned_ratio, earned_ratio in [0.95, 1.00]
  - renewal: GWP * earned_ratio, earned_ratio in [0.98, 1.02], clamped to <= GWP
- earned_premium <= gross_written_premium
- ceded_premium <= gross_written_premium

FLAG RULES:
- new_policy_flag XOR renewal_flag (never both true)
- renewal uses option 2: same policy_key, extend policy_end_date

POLICY TERM:
- 12 months
- tenure_years must vary realistically (not always 1)

NO CANCELLATIONS/LAPSES.

REALISM TARGETS:
- Combined ratio typically between 80% and 140%
- Data must pass validation gates.

GROWTH REQUIREMENT:
- Company scale must be realistic and consistent:
  - policies >> underwriters
  - policies >> brokers
- brokers, underwriters, and policies must evolve over time using non-static growth curves (logistic / ramp / hiring waves).
- No constant increments.

OUTPUT:
Write CSVs (and optionally parquet) for all dims and fact.

Do not add any extra tables or columns beyond the schema.
