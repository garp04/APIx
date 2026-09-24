import subprocess
import time
import sys
from datetime import datetime

def run_step(step_name, command):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting: {step_name}...")
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print(f"Error in {step_name}. Exiting.")
        sys.exit(1)
    print(f"Completed: {step_name} successfully.")

def execute_daily_pipeline():
    print("==================================================")
    print(" AUTOMATED HIGH-FREQUENCY APIx PIPELINE INITIATED ")
    print("==================================================")
    
    # 1. Run live scraper to ingest multi-horizon quotes
    run_step("Live Scraper Engine", "python src/scraper.py")

    # 2. Run index engine to recalculate APIx and back-test
    run_step("Statistical Index Aggregator", "python src/index_engine.py")

    # 3. Run predictive forecasting model
    run_step("ARIMA Inflation Forecaster", "python src/forecaster.py")

    print("\nPipeline execution complete. Streamlit dashboard reflects updated series.")

if __name__ == '__main__':
    # Can be scheduled via cron (e.g. daily at 00:00) or run as continuous daemon
    execute_daily_pipeline()