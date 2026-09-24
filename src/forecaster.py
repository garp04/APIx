from datetime import timedelta
import holidays
import json
import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX


def generate_apix_forecast(forecast_horizon_days=30):
  print(
      "Training SARIMAX Forecasting Engine with Seasonal & Festival Exogenous"
      " Features..."
  )

  # 1. Load calibrated daily index series and festival configuration rules
  df_daily = pd.read_csv("data/processed/apix_daily_series.csv")
  df_daily["date"] = pd.to_datetime(df_daily["date"])
  df_daily = df_daily.sort_values("date").reset_index(drop=True)

  with open("data/raw/festival.json", "r") as f:
    festivals_config = json.load(f)

  india_holidays = holidays.India(years=[2025, 2026, 2027])

  # 2. Build exogenous feature extractor for festivals
  def get_festival_flag(check_date):
    rules = festivals_config.get("surge_rules", {})
    for fest_keyword, rule in rules.items():
      b_days = rule.get("window_days_before", 2)
      a_days = rule.get("window_days_after", 2)
      for offset in range(-b_days, a_days + 1):
        target_date = check_date + timedelta(days=offset)
        if target_date in india_holidays:
          h_name = india_holidays.get(target_date)
          if fest_keyword.lower() in h_name.lower():
            return 1.0  # Active festival indicator flag
    if check_date in india_holidays:
      return 1.0
    return 0.0

  # Attach historical exog feature (festival flag + weekend flag)
  df_daily["is_festival"] = df_daily["date"].apply(get_festival_flag)
  df_daily["is_weekend"] = df_daily["date"].dt.weekday.isin([4, 6]).astype(float)

  y_train = df_daily["apix_daily"].values
  exog_train = df_daily[["is_festival", "is_weekend"]].values

  # 3. Fit SARIMAX model (incorporating weekly seasonality period=7 and exogenous variables)
  model = SARIMAX(
      y_train,
      exog=exog_train,
      order=(1, 1, 1),
      seasonal_order=(1, 1, 1, 7),
      enforce_stationarity=False,
      enforce_invertibility=False,
  )
  model_fit = model.fit(disp=False)

  # 4. Generate future dates and future exogenous feature matrix for the next 30 days
  last_date = df_daily["date"].iloc[-1]
  future_dates = [
      last_date + timedelta(days=i) for i in range(1, forecast_horizon_days + 1)
  ]

  future_exog_list = []
  for f_date in future_dates:
    fest_flag = get_festival_flag(f_date)
    wknd_flag = 1.0 if f_date.weekday() in [4, 6] else 0.0
    future_exog_list.append([fest_flag, wknd_flag])

  exog_forecast = np.array(future_exog_list)

  # 5. Forecast next N days passing future exogenous drivers
  forecast_res = model_fit.get_forecast(
      steps=forecast_horizon_days, exog=exog_forecast
  )
  forecast_values = forecast_res.predicted_mean
  conf_int = forecast_res.conf_int(alpha=0.05)

  df_forecast = pd.DataFrame({
      "date": future_dates,
      "predicted_apix": forecast_values,
      "lower_ci": conf_int[:, 0],
      "upper_ci": conf_int[:, 1],
  })

  current_level = y_train[-1]
  expected_level = forecast_values[-1]
  expected_growth = ((expected_level - current_level) / current_level) * 100

  output_path = "data/processed/apix_forecast_30d.csv"
  df_forecast.to_csv(output_path, index=False)
  print(
      f"SARIMAX Forecast computed successfully: {forecast_horizon_days}-day"
      f" projected change: {expected_growth:+.2f}%"
  )
  print(f"Saved projections with active festival flags to: {output_path}")


if __name__ == "__main__":
  generate_apix_forecast(30)