import pandas as pd
import glob

run_dir = glob.glob('output/run_*')[-1]
brk_df = pd.read_csv(f'{run_dir}/csv/dim_broker.csv')
fact_df = pd.read_csv(f'{run_dir}/csv/fact_policy.csv')

print(f"Total Brokers in dim_broker: {len(brk_df)}")
print(f"Unique Brokers in fact_policy: {fact_df['broker_key'].nunique()}")
