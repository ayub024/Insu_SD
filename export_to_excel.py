import os
from pathlib import Path

try:
    import pandas as pd
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference, Series, AreaChart
    from openpyxl.utils.dataframe import dataframe_to_rows
    from openpyxl.chart.marker import DataPoint
    from openpyxl.chart.shapes import GraphicalProperties
except ImportError:
    print("ERROR: pandas and openpyxl are required. Please install them by running:")
    print("pip install pandas openpyxl")
    exit(1)


def add_chart(sheet, chart_obj, title, origin_cell, data_ref, cats_ref=None, width=15, height=7.5, x_title=None, y_title=None):
    """Helper to size and place a chart."""
    chart_obj.title = title
    chart_obj.style = 13
    chart_obj.width = width
    chart_obj.height = height
    if cats_ref:
        chart_obj.set_categories(cats_ref)
    chart_obj.add_data(data_ref, titles_from_data=True)
    if x_title and hasattr(chart_obj, 'x_axis'):
        chart_obj.x_axis.title = x_title
    if y_title and hasattr(chart_obj, 'y_axis'):
        chart_obj.y_axis.title = y_title
    sheet.add_chart(chart_obj, origin_cell)


def generate_dashboards(writer, csv_dir: Path):
    """Generate the aggregated data and the openpyxl charts."""
    dash_sheet_name = "_Dashboards"
    wb = writer.book

    # 1. Load Data
    try:
        fact_df = pd.read_csv(csv_dir / "fact_policy.csv", low_memory=False)
        uw_df = pd.read_csv(csv_dir / "dim_underwriter.csv", low_memory=False)
        brk_df = pd.read_csv(csv_dir / "dim_broker.csv", low_memory=False)
        cust_df = pd.read_csv(csv_dir / "dim_customer.csv", low_memory=False)
        pol_df = pd.read_csv(csv_dir / "dim_policy.csv", low_memory=False)
        prod_df = pd.read_csv(csv_dir / "dim_product.csv", low_memory=False)
    except Exception as e:
        print(f"Skipping dashboards due to missing CSVs: {e}")
        return

    # 2. Prepare Timestamps
    fact_df["month"] = pd.to_datetime(fact_df["date_key"], format="%Y%m%d").dt.to_period("M").astype(str)
    pol_df["month"] = pd.to_datetime(pol_df["policy_start_date"]).dt.to_period("M").astype(str)

    # 3. Aggregations
    # Fact: Financials over time
    fact_trend = fact_df.groupby("month").agg({
        "gross_written_premium": "sum",
        "premium_collected_amount": "sum",
        "incurred_claim_amount": "sum",
        "underwriting_expense": "sum",
        "other_expense": "sum",
        "acquisition_expense": "sum",
    }).reset_index()
    fact_trend["operating_expense"] = fact_trend["underwriting_expense"] + fact_trend["other_expense"]
    fact_trend["Loss_Ratio"] = fact_trend["incurred_claim_amount"] / fact_trend["premium_collected_amount"].replace(0, 1)
    
    # Fact: Renewals
    uw_events = fact_df[fact_df["transaction_domain"].eq("Underwriting")].copy()
    uw_events["renewal_flag_bool"] = uw_events["renewal_flag"].astype(str).str.lower().isin(["true", "1", "yes"])
    fact_ren = uw_events.groupby("month").agg({
        "renewal_flag_bool": "sum"
    }).reset_index()
    fact_ren["new_policy_count"] = (
        uw_events
        .assign(new_policy_flag=lambda df: ~df["renewal_flag_bool"])
        .groupby("month")["new_policy_flag"]
        .sum()
        .reindex(fact_ren["month"])
        .fillna(0)
        .values
    )
    fact_ren = fact_ren.rename(columns={"renewal_flag_bool": "renewal_count"})
    fact_ren = fact_ren[["month", "new_policy_count", "renewal_count"]]

    # Fact: Claim Waterfall (Total)
    wf_data = pd.DataFrame([{
        "Metric": ["Paid", "Incurred", "IBNR"],
        "Amount": [fact_df["paid_claim_amount"].sum(), fact_df["incurred_claim_amount"].sum(), fact_df["ibnr_amount"].sum()]
    }]).explode(["Metric", "Amount"]).reset_index(drop=True)

    domain_mix = fact_df.groupby("transaction_domain").size().reset_index(name="event_count")
    reinsurance_data = pd.DataFrame(
        [
            {"Metric": "Ceded Premium", "Amount": fact_df["ceded_premium"].sum()},
            {"Metric": "Reinsurance Recovery", "Amount": fact_df["reinsurance_recovery"].sum()},
        ]
    )

    # Merge prod_df into fact_df to get product_name
    fact_df = fact_df.merge(prod_df[["product_key", "product_name"]], on="product_key", how="left")
    
    cust_mix = cust_df.groupby("customer_geography").size().reset_index(name="count")
    pol_mix = fact_df.groupby(["month", "product_name"]).size().unstack(fill_value=0).reset_index()

    # 4. Write Aggregations to Hidden Sheet
    dash_ws = wb.create_sheet(dash_sheet_name)
    dash_ws.sheet_state = 'hidden'
    
    r_idx = 1
    # Block 1: Fact Trend (cols A-G)
    for r in dataframe_to_rows(fact_trend, index=False, header=True):
        dash_ws.append(r)
    r_fact_end = dash_ws.max_row
    
    # Block 2: Fact Renewals (cols I-K)
    r_idx = dash_ws.max_row + 2
    for i, r in enumerate(dataframe_to_rows(fact_ren, index=False, header=True)):
        dash_ws.cell(row=r_idx+i, column=9, value=r[0])
        dash_ws.cell(row=r_idx+i, column=10, value=r[1])
        dash_ws.cell(row=r_idx+i, column=11, value=r[2])
    r_ren_end = dash_ws.max_row
        
    # Block 3: Customer Mix (M-N)
    r_idx = dash_ws.max_row + 2
    for i, r in enumerate(dataframe_to_rows(cust_mix, index=False, header=True)):
        dash_ws.cell(row=r_idx+i, column=13, value=r[0])
        dash_ws.cell(row=r_idx+i, column=14, value=r[1])
    r_cust_end = dash_ws.max_row
    
    # Block 4: Waterfall (R-S)
    r_idx = dash_ws.max_row + 2
    for i, r in enumerate(dataframe_to_rows(wf_data, index=False, header=True)):
        dash_ws.cell(row=r_idx+i, column=18, value=r[0])
        dash_ws.cell(row=r_idx+i, column=19, value=r[1])
    r_wf_end = dash_ws.max_row

    # Block 5: Domain Mix (U-V)
    r_idx = dash_ws.max_row + 2
    r_domain_start = r_idx
    for i, r in enumerate(dataframe_to_rows(domain_mix, index=False, header=True)):
        dash_ws.cell(row=r_idx+i, column=21, value=r[0])
        dash_ws.cell(row=r_idx+i, column=22, value=r[1])
    r_domain_end = dash_ws.max_row

    # Block 6: Reinsurance Summary (X-Y)
    r_idx = dash_ws.max_row + 2
    r_reins_start = r_idx
    for i, r in enumerate(dataframe_to_rows(reinsurance_data, index=False, header=True)):
        dash_ws.cell(row=r_idx+i, column=24, value=r[0])
        dash_ws.cell(row=r_idx+i, column=25, value=r[1])
    r_reins_end = dash_ws.max_row

    # --------- DRAW CHARTS ------------
    if "fact_policy" in wb.sheetnames:
        ws_fact = wb["fact_policy"]
        
        # Premium/Loss Line Chart
        lc = LineChart()
        dates = Reference(dash_ws, min_col=1, min_row=2, max_row=r_fact_end)
        data = Reference(dash_ws, min_col=2, min_row=1, max_col=4, max_row=r_fact_end)
        add_chart(ws_fact, lc, "Premium & Incurred Claims Growth\n(Tracking GWP, Collected Premium, and Claims over time)", "Z2", data, dates, x_title="Month", y_title="Amount ($)")
        
        # Loss Ratio Line Chart
        lr = LineChart()
        data_lr = Reference(dash_ws, min_col=7, min_row=1, max_row=r_fact_end)
        add_chart(ws_fact, lr, "Loss Ratio vs. Time\n(Incurred Claims / Collected Premium)", "Z17", data_lr, dates, x_title="Month", y_title="Loss Ratio")
        
        # New vs Renewal Accumulation
        bc = BarChart()
        bc.type = "col"
        bc.grouping = "stacked"
        bc_dates = Reference(dash_ws, min_col=9, min_row=r_fact_end+3, max_row=r_ren_end)
        bc_data = Reference(dash_ws, min_col=10, min_row=r_fact_end+2, max_col=11, max_row=r_ren_end)
        add_chart(ws_fact, bc, "Policy Volume (New vs Renewed)\n(Total policy count stacked by new/renewal)", "Z32", bc_data, bc_dates, x_title="Month", y_title="Number of Policies")
        
        # Claim Waterfall
        wc = BarChart()
        wc.type = "col"
        wc.grouping = "stacked"
        wc_cats = Reference(dash_ws, min_col=18, min_row=r_cust_end+3, max_row=r_wf_end)
        wc_data = Reference(dash_ws, min_col=19, min_row=r_cust_end+2, max_row=r_wf_end)
        add_chart(ws_fact, wc, "Claim Waterfall\n(Breakdown of Paid, Incurred, and IBNR)", "Z47", wc_data, wc_cats, x_title="Claim Component", y_title="Amount ($)")

        dc = PieChart()
        dc_cats = Reference(dash_ws, min_col=21, min_row=r_domain_start+1, max_row=r_domain_end)
        dc_data = Reference(dash_ws, min_col=22, min_row=r_domain_start, max_row=r_domain_end)
        add_chart(ws_fact, dc, "Transaction Domain Mix\n(Event counts by business domain)", "Z62", dc_data, dc_cats)

        rc = BarChart()
        rc.type = "col"
        rc_cats = Reference(dash_ws, min_col=24, min_row=r_reins_start+1, max_row=r_reins_end)
        rc_data = Reference(dash_ws, min_col=25, min_row=r_reins_start, max_row=r_reins_end)
        add_chart(ws_fact, rc, "Reinsurance Summary\n(Ceded premium and recovery amounts)", "Z77", rc_data, rc_cats, x_title="Metric", y_title="Amount ($)")
        
    if "dim_customer" in wb.sheetnames:
        ws_cust = wb["dim_customer"]
        cc = PieChart()
        cats = Reference(dash_ws, min_col=13, min_row=r_ren_end+3, max_row=r_cust_end)
        data = Reference(dash_ws, min_col=14, min_row=r_ren_end+2, max_row=r_cust_end)
        add_chart(ws_cust, cc, "Customer Distribution by Geography\n(Proportion of policies by Region)", "O2", data, cats)


def main():
    print("Starting Excel Output Generation...")
    output_dir = Path("output")
    if not output_dir.exists():
        print("No output directory found. Please run main.py first.")
        return

    # Find the most recently generated run directory
    run_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir() and d.name.startswith("run_")])
    if not run_dirs:
        print("No run directories found in output/.")
        return

    latest_run = run_dirs[-1]
    csv_dir = latest_run / "csv"
    
    if not csv_dir.exists():
        print(f"No csv directory found in {latest_run}")
        return

    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        return

    excel_path = latest_run / f"{latest_run.name}_validation.xlsx"
    print(f"Aggregating {len(csv_files)} CSV files into Excel Workbook: {excel_path.name}")

    try:
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            for csv_file in csv_files:
                table_name = csv_file.stem
                sheet_name = table_name[:31]
                print(f"  -> Writing {table_name}.csv to sheet '{sheet_name}'...")
                
                df = pd.read_csv(csv_file, low_memory=False)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                
            print("  -> Generating Analytical Dashboards...")
            generate_dashboards(writer, csv_dir)
            
        print("\nSuccess! Validation Excel file generated at:")
        print(excel_path.absolute())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nError generating Excel file: {e}")
        print("Make sure openpyxl is installed (pip install openpyxl)")

if __name__ == "__main__":
    main()
