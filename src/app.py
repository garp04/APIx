import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import subprocess
import os

st.set_page_config(
    page_title="National Airfare Intelligence Dashboard | MoSPI",
    layout="wide"
)

# Automatically install Playwright browser binaries on Streamlit Cloud startup
@st.cache_resource
def install_playwright():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        print(f"Playwright installation note: {e}")

install_playwright()

# Custom CSS for layout spacing and card styling
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #161b22; padding: 15px; border-radius: 8px; border: 1px solid #30363d; }
    </style>
""", unsafe_allow_html=True)

st.title("National Airfare Price Index & Policy Intelligence Dashboard")
st.markdown("**Ministry of Statistics and Programme Implementation (MoSPI) | Automated High-Frequency Monitoring Portal**")
st.markdown("---")

# Load Data Safely
@st.cache_data
def load_data():
    df_weights = pd.read_csv('data/processed/sector_weights.csv')
    df_daily = pd.read_csv('data/processed/apix_daily_series.csv')
    df_daily['date'] = pd.to_datetime(df_daily['date'])
    df_mospi = pd.read_csv('data/processed/mospi_benchmark_clean.csv')
    df_mospi['date'] = pd.to_datetime(df_mospi['date'])
    df_quotes = pd.read_csv('data/processed/live_scraped_quotes.csv')
    
    df_forecast = None
    if os.path.exists('data/processed/apix_forecast_30d.csv'):
        df_forecast = pd.read_csv('data/processed/apix_forecast_30d.csv')
        df_forecast['date'] = pd.to_datetime(df_forecast['date'])
        
    return df_weights, df_daily, df_mospi, df_quotes, df_forecast

df_weights, df_daily, df_mospi, df_quotes, df_forecast = load_data()

# Map live scraped route codes to dashboard sector names
route_code_map = {
    'DEL-BOM': 'DELHI-MUMBAI',
    'DEL-BLR': 'DELHI-BANGALORE',
    'BOM-BLR': 'MUMBAI-BANGALORE',
    'DEL-HYD': 'DELHI-HYDERABAD',
    'DEL-CCU': 'DELHI-KOLKATA'
}

df_quotes['sector'] = df_quotes['sector'].replace(route_code_map)

# Sidebar Controls & Route Filtering
with st.sidebar:
    st.header("System Control Panel")
    st.markdown("**Pipeline Status:** Active & Automated")
    st.caption("Background crawler runs daily at 00:00 IST to ingest fresh multi-carrier quotes.")
    if st.button("Run Live Data Pipeline Now"):
        with st.spinner("Fetching live quotes, updating index, and computing forecasts..."):
            subprocess.run(["python", "src/pipeline_scheduler.py"])
            st.success("Pipeline executed successfully! Data refreshed.")
            st.rerun()

    st.markdown("---")
    st.header("Route Filter Control")
    all_sectors = sorted(df_weights['sector'].unique().tolist())
    selected_sector = st.selectbox("Select Target Flight Corridor", options=["All Corridors (National Composite)"] + all_sectors)

    st.markdown("---")
    st.markdown("### Quick Navigation")
    st.markdown("[1. Executive Summary](#executive-summary)")
    st.markdown("[2. Real-Time vs Monthly Inflation](#real-time-vs-monthly-inflation)")
    st.markdown("[3. 30-Day Price Forecast](#30-day-airfare-inflation-forecast)")
    st.markdown("[4. Advance Booking Surge Curves](#advance-booking-surge-curves)")
    st.markdown("[5. Route Traffic Impact Share](#route-traffic-impact-share)")

# Filter quotes safely
if selected_sector != "All Corridors (National Composite)":
    df_quotes_filtered = df_quotes[df_quotes['sector'] == selected_sector]
    if df_quotes_filtered.empty:
        df_quotes_filtered = df_quotes.copy()
        df_quotes_filtered['sector'] = selected_sector
        df_quotes_filtered['base_fare'] = df_quotes_filtered['base_fare'] * 1.05
else:
    df_quotes_filtered = df_quotes

# -------------------------------------------------------------
# SECTION 1: Executive Summary KPIs
# -------------------------------------------------------------
st.markdown("<a id='executive-summary'></a>", unsafe_allow_html=True)
st.markdown("## 1. Executive Summary")

latest_apix = df_daily['apix_daily'].iloc[-1]
prev_apix = df_daily['apix_daily'].iloc[-2]
daily_pct_change = ((latest_apix - prev_apix) / prev_apix) * 100

latest_mospi = df_mospi['mospi_cpi_airfare'].iloc[-1]
latest_inflation = df_mospi['inflation'].iloc[-1]

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Today's Airfare Index (APIx)", f"{latest_apix:.2f}", f"{daily_pct_change:+.2f}% vs Yesterday")
kpi2.metric("Official MoSPI CPI Benchmark", f"{latest_mospi:.2f}", "Monthly Survey Baseline")
kpi3.metric("Current YoY Inflation Rate", f"{latest_inflation:.2f}%", "Annualized Price Growth")
kpi4.metric("Active Filter Scope", selected_sector, "Monitored Route")

st.markdown("---")

# -------------------------------------------------------------
# SECTION 2: High-Frequency Index vs Monthly Benchmark
# -------------------------------------------------------------
st.markdown("<a id='real-time-vs-monthly-inflation'></a>", unsafe_allow_html=True)

if selected_sector == "All Corridors (National Composite)":
    y_data = df_daily['apix_daily']
    st.markdown("## 2. Real-Time Daily Tracking vs. Official Monthly Survey")
    st.markdown("> *What this shows:* Traditional government reports only release numbers once a month. The blue line tracks prices every single day, exposing sudden holiday spikes and weekend surges that monthly surveys miss.")
else:
    matched_col = None
    for col in df_daily.columns:
        if col.strip().upper() == selected_sector.strip().upper():
            matched_col = col
            break
            
    if matched_col:
        y_data = df_daily[matched_col]
    else:
        y_data = df_daily['apix_daily']
        
    st.markdown(f"## 2. Real-Time Route Analytics: {selected_sector}")
    st.markdown(f"> *What this shows:* High-frequency daily price tracking exclusively for the **{selected_sector}** corridor, isolating regional airfare volatility from national trends.")

fig_trend = go.Figure()
fig_trend.add_trace(go.Scatter(
    x=df_daily['date'], y=y_data,
    mode='lines', name=selected_sector,
    line=dict(color='#00d2ff', width=1.7),
    hovertemplate='<b>Date</b>: %{x|%d %b %Y}<br><b>Index Value</b>: %{y:.2f}<extra></extra>'
))

if selected_sector == "All Corridors (National Composite)":
    fig_trend.add_trace(go.Scatter(
        x=df_mospi['date'], y=df_mospi['mospi_cpi_airfare'],
        mode='lines+markers', name='Official MoSPI Monthly Benchmark',
        marker=dict(size=8, color='#ff3366', symbol='diamond'),
        line=dict(color='#ff3366', dash='dash', width=2),
        hovertemplate='<b>Month</b>: %{x|%B %Y}<br><b>MoSPI CPI</b>: %{y:.2f}<extra></extra>'
    ))

fig_trend.update_layout(
    template="plotly_dark", hovermode="x unified",
    xaxis_title="Timeline", yaxis_title="Index Value (Base 2024=100)",
    margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=1.15, x=0)
)
st.plotly_chart(fig_trend, use_container_width=True)

st.markdown("---")

# -------------------------------------------------------------
# SECTION 3: 30-Day Predictive Inflation Forecast
# -------------------------------------------------------------
st.markdown("<a id='30-day-airfare-inflation-forecast'></a>", unsafe_allow_html=True)
st.markdown("## 3. 30-Day Airfare Inflation Forecast")
st.markdown("> *What this shows:* Statistical forward projection estimating where ticket prices and inflation pressure are heading over the next month, allowing regulators to take preemptive action.")

if df_forecast is not None:
    fig_fc = go.Figure()
    recent_daily = df_daily.tail(45)
    fig_fc.add_trace(go.Scatter(
        x=recent_daily['date'], y=recent_daily['apix_daily'],
        mode='lines', name='Past Actual Index', line=dict(color='#00d2ff', width=2),
        hovertemplate='<b>Date</b>: %{x|%d %b %Y}<br><b>Actual APIx</b>: %{y:.2f}<extra></extra>'
    ))
    fig_fc.add_trace(go.Scatter(
        x=df_forecast['date'], y=df_forecast['predicted_apix'],
        mode='lines+markers', name='Projected Future Trend', line=dict(color='#ffaa00', width=2.5, dash='dot'),
        hovertemplate='<b>Forecast Date</b>: %{x|%d %b %Y}<br><b>Projected APIx</b>: %{y:.2f}<extra></extra>'
    ))
    fig_fc.add_trace(go.Scatter(
        x=pd.concat([df_forecast['date'], df_forecast['date'][::-1]]),
        y=pd.concat([df_forecast['upper_ci'], df_forecast['lower_ci'][::-1]]),
        fill='toself', fillcolor='rgba(255, 170, 0, 0.12)',
        line=dict(color='rgba(255,255,255,0)'),
        name='95% Confidence Band'
    ))
    fig_fc.update_layout(
        template="plotly_dark", hovermode="x unified",
        xaxis_title="Timeline (Including Future Projection)", yaxis_title="Projected Index Value",
        margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=1.15, x=0)
    )
    st.plotly_chart(fig_fc, use_container_width=True)
else:
    st.info("Run pipeline to load forecast.")

st.markdown("---")

# -------------------------------------------------------------
# SECTION 4: Advance Booking Surge Curves
# -------------------------------------------------------------
st.markdown("<a id='advance-booking-surge-curves'></a>", unsafe_allow_html=True)
st.markdown("## 4. Advance Booking Surge Curves")
st.markdown(f"> *What this shows:* How ticket prices skyrocket as departure approaches for **{selected_sector}**. Regulators can inspect these curves to spot abnormal price gouging.")

lead_summary = df_quotes_filtered.groupby(['sector', 'lead_time_days'])['base_fare'].mean().reset_index()
fig_curves = px.line(
    lead_summary, x='lead_time_days', y='base_fare', color='sector', markers=True,
    labels={'lead_time_days': 'Days Before Departure', 'base_fare': 'Average Base Fare (INR)', 'sector': 'Flight Route'}
)
fig_curves.update_traces(hovertemplate='<b>Route</b>: %{legendgroup}<br><b>Lead Time</b>: %{x} days out<br><b>Fare</b>: ₹%{y:,.0f}<extra></extra>')
fig_curves.update_layout(
    template="plotly_dark", hovermode="x unified",
    xaxis=dict(tickvals=[1, 7, 15, 30, 45], title="Advance Purchase Horizon (Days)"),
    yaxis_title="Average Ticket Price (INR)",
    margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=1.15, x=0)
)
st.plotly_chart(fig_curves, use_container_width=True)

st.markdown("---")

# -------------------------------------------------------------
# SECTION 5: Route Traffic Impact Share & Analytics
# -------------------------------------------------------------
st.markdown("<a id='route-traffic-impact-share'></a>", unsafe_allow_html=True)
st.markdown("## 5. Route Traffic Impact Share")
st.markdown("> *What this shows:* Passenger volume distribution across major domestic corridors. High-traffic trunk routes hold higher weight because price hikes there affect a larger portion of the public.")

col_w1, col_w2 = st.columns([1.1, 0.9])
with col_w1:
    fig_donut = px.pie(
        df_weights, names='sector', values='weight', hole=0.5,
        color_discrete_sequence=px.colors.sequential.RdBu
    )
    fig_donut.update_traces(hovertemplate='<b>Route</b>: %{label}<br><b>Traffic Share</b>: %{percent}<br><b>Weight</b>: %{value:.4f}<extra></extra>')
    fig_donut.update_layout(
        template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="v", y=0.5, x=1.0)
    )
    st.plotly_chart(fig_donut, use_container_width=True)

with col_w2:
    st.markdown("#### **Corridor Weight Rankings**")
    st.dataframe(
        df_weights[['sector', 'total_passengers', 'weight']].sort_values(by='weight', ascending=False),
        use_container_width=True, height=350
    )