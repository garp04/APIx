import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from datetime import timedelta

def generate_apix_forecast(forecast_horizon_days=30):
    print("Training ARIMA Forecasting Engine on daily APIx trajectory...")
    
    # Load calibrated daily index series
    df_daily = pd.read_csv('data/processed/apix_daily_series.csv')
    df_daily['date'] = pd.to_datetime(df_daily['date'])
    df_daily = df_daily.sort_values('date').reset_index(drop=True)

    # Fit ARIMA model on the historical index series
    series = df_daily['apix_daily'].values
    # (p=2, d=1, q=2) captures short-term momentum and cyclical mean reversion
    model = ARIMA(series, order=(2, 1, 2))
    model_fit = model.fit()

    # Forecast next N days
    forecast_res = model_fit.get_forecast(steps=forecast_horizon_days)
    forecast_values = forecast_res.predicted_mean
    conf_int = forecast_res.conf_int(alpha=0.05) # 95% confidence interval

    last_date = df_daily['date'].iloc[-1]
    forecast_dates = [last_date + timedelta(days=i) for i in range(1, forecast_horizon_days + 1)]

    df_forecast = pd.DataFrame({
        'date': forecast_dates,
        'predicted_apix': forecast_values,
        'lower_ci': conf_int[:, 0],
        'upper_ci': conf_int[:, 1]
    })

    # Estimate expected monthly inflation impact
    current_level = series[-1]
    expected_level = forecast_values[-1]
    expected_growth = ((expected_level - current_level) / current_level) * 100

    output_path = 'data/processed/apix_forecast_30d.csv'
    df_forecast.to_csv(output_path, index=False)
    print(f"Forecast successfully computed: {forecast_horizon_days}-day projected change: {expected_growth:+.2f}%")
    print(f"Saved projections to: {output_path}")

if __name__ == '__main__':
    generate_apix_forecast(30)