"""
dashboard/app.py
GreenScheduler monitoring dashboard v2.

Sections:
  1. Live environmental snapshot (all regions)
  2. Cross-region comparison chart
  3. Carbon & renewable 48-hour forecast
  4. Cumulative impact counter
  5. Carbon budget gauge
  6. Interactive schedule simulator with what-if comparison
  7. Live job queue with lifecycle controls
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import requests

API_BASE_URL = "https://carbon-water-aware-ai-workload-sche.vercel.app"

# ── Black theme, yellow → orange accents ───────────────────────────────────
COLOR_GRAD_START = "#FBBF24"    # amber / yellow — gradient start
COLOR_GRAD_END = "#F97316"      # orange — gradient end
COLOR_PRIMARY = "#F97316"       # orange — primary accent
COLOR_PRIMARY_DARK = "#FDF1E3"  # warm off-white — headings/labels on dark cards
COLOR_ACCENT = "#F97316"        # orange — positive / highlight signals
COLOR_WARN = "#D97706"          # amber — caution
COLOR_DANGER = "#EF4444"        # red — hard limits / negative
COLOR_BG_PAGE = "#0A0A0A"       # true black page background
COLOR_BG_CARD = "#151515"       # dark card background, one step lighter than page
COLOR_BORDER = "#2B2B2B"        # subtle dark border
COLOR_TEXT = "#EDEAE3"          # primary body text (warm off-white)
COLOR_TEXT_MUTED = "#9B948A"    # muted warm grey

# Chart panels use a dark background + light text — this stays legible
# regardless of the visitor's browser/OS theme (Streamlit's automatic
# chart theming otherwise silently overrides light-mode chart colours).
CHART_BG = COLOR_BG_CARD
CHART_GRID = "rgba(255,255,255,0.10)"
CHART_FONT = COLOR_TEXT
CHART_MUTED = COLOR_TEXT_MUTED


def api_get(endpoint):
    response = requests.get(
        f"{API_BASE_URL}{endpoint}",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def api_post(endpoint, data=None):
    response = requests.post(
        f"{API_BASE_URL}{endpoint}",
        json=data,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def api_delete(endpoint):
    response = requests.delete(
        f"{API_BASE_URL}{endpoint}",
        timeout=30
    )
    response.raise_for_status()
    return response


from config.loader import get_settings
from data.carbon import _mock_forecast
from data.renewable import _mock_renewable_forecast
from data.water import WaterDataService
from scheduler.engine import SchedulingEngine
from workloads.budget import carbon_budget
from workloads.job import JobRequest, Priority
from workloads.queue import job_queue

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GreenScheduler | Sustainability Operations Console",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global styling ────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}

/* Override Streamlit's core theme accent variable so native widgets
   (sliders, checkboxes, radios, focus rings) follow it automatically,
   instead of relying only on DOM-structure guesses below. */
:root, .stApp {{
    --primary-color: {COLOR_GRAD_END} !important;
}}

/* ── Force one consistent black background app-wide (prevents it clashing
      with the browser/OS theme) and set a light default text colour ── */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
    background: {COLOR_BG_PAGE} !important;
    color: {COLOR_TEXT} !important;
}}
.block-container {{
    padding-top: 1.6rem;
    max-width: 1300px;
}}
[data-testid="stAppViewContainer"] * {{
    color: {COLOR_TEXT};
}}
p, span, label, div {{
    color: inherit;
}}

/* Sidebar */
section[data-testid="stSidebar"] {{
    background: {COLOR_BG_CARD} !important;
    border-right: 1px solid {COLOR_BORDER};
}}
section[data-testid="stSidebar"] * {{
    color: {COLOR_TEXT} !important;
}}

/* Header banner */
.dashboard-header {{
    padding: 22px 26px;
    background: linear-gradient(120deg, {COLOR_GRAD_START} 0%, {COLOR_GRAD_END} 100%);
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 4px 14px rgba(249, 115, 22, 0.25);
}}
.dashboard-header h1 {{
    margin: 0;
    font-size: 1.7rem;
    font-weight: 800;
    color: #FFFFFF;
    letter-spacing: -0.01em;
    text-shadow: 0 1px 2px rgba(0,0,0,0.08);
}}
.dashboard-header p {{
    margin: 6px 0 0 0;
    font-size: 0.92rem;
    color: rgba(255,255,255,0.92);
}}

/* Section headers */
.section-label {{
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {COLOR_PRIMARY_DARK};
    margin-bottom: 6px;
    padding-bottom: 4px;
    border-bottom: 3px solid {COLOR_PRIMARY};
    display: inline-block;
}}

/* Metric / region cards */
.region-card {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 12px;
    padding: 14px 16px;
    margin: 4px 0;
    background: {COLOR_BG_CARD};
    box-shadow: 0 1px 3px rgba(124, 45, 18, 0.05);
    transition: box-shadow 0.15s ease;
}}
.region-card.leading {{
    border: 1px solid {COLOR_GRAD_END};
    border-left: 4px solid {COLOR_GRAD_END};
    box-shadow: 0 4px 14px rgba(249, 115, 22, 0.16);
}}
.region-card .region-name {{
    font-weight: 700;
    font-size: 0.95rem;
    color: {COLOR_PRIMARY_DARK};
}}
.region-card .badge {{
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #FFFFFF;
    background: linear-gradient(120deg, {COLOR_GRAD_START}, {COLOR_GRAD_END});
    padding: 2px 9px;
    border-radius: 20px;
    margin-left: 6px;
}}
.region-card .metric-row {{
    font-size: 0.85rem;
    color: {COLOR_TEXT};
    margin-top: 6px;
    line-height: 1.65;
}}
.region-card .metric-row b {{
    font-family: 'IBM Plex Mono', monospace;
    color: {COLOR_PRIMARY_DARK};
}}

.metric-card {{
    background: {COLOR_BG_CARD};
    border-radius: 10px;
    padding: 16px;
    margin: 4px;
    border-left: 4px solid {COLOR_PRIMARY};
    border-top: 1px solid {COLOR_BORDER};
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
}}
.impact-number {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2.2rem;
    font-weight: 700;
    color: {COLOR_PRIMARY_DARK};
}}
.saved-label {{
    font-size: 0.8rem;
    color: {COLOR_TEXT_MUTED};
}}

hr {{
    border-color: {COLOR_BORDER} !important;
}}

/* Buttons */
div.stButton > button[kind="primary"] {{
    background: linear-gradient(120deg, {COLOR_GRAD_START}, {COLOR_GRAD_END});
    color: #FFFFFF !important;
    border: none;
    font-weight: 700;
    box-shadow: 0 2px 8px rgba(249, 115, 22, 0.3);
}}
div.stButton > button[kind="primary"]:hover {{
    filter: brightness(1.06);
    box-shadow: 0 4px 12px rgba(249, 115, 22, 0.4);
}}
div.stButton > button[kind="secondary"] {{
    border: 1px solid {COLOR_BORDER};
    color: {COLOR_PRIMARY_DARK} !important;
    background: {COLOR_BG_CARD};
}}
div.stButton > button[kind="secondary"]:hover {{
    border-color: {COLOR_PRIMARY};
    color: {COLOR_PRIMARY} !important;
}}

/* Sliders (BaseWeb) — fallback in case the --primary-color variable
   above isn't picked up by this Streamlit version */
div[data-testid="stSlider"] div[role="slider"] {{
    background-color: {COLOR_GRAD_END} !important;
    border-color: {COLOR_GRAD_END} !important;
    box-shadow: 0 0 0 4px rgba(249, 115, 22, 0.15) !important;
}}
div[data-testid="stSlider"] div[data-baseweb="slider"] > div > div {{
    background: linear-gradient(90deg, {COLOR_GRAD_START}, {COLOR_GRAD_END}) !important;
}}
div[data-testid="stSlider"] div[data-baseweb="slider"] > div > div:first-child {{
    background: {COLOR_BORDER} !important;
}}
[data-testid="stTickBarMin"], [data-testid="stTickBarMax"] {{
    color: {COLOR_TEXT_MUTED} !important;
}}
[data-testid="stThumbValue"] {{
    color: {COLOR_PRIMARY_DARK} !important;
    font-weight: 600;
}}

/* Tabs */
button[data-baseweb="tab"] {{
    color: {COLOR_TEXT_MUTED};
    font-weight: 600;
}}
button[data-baseweb="tab"][aria-selected="true"] {{
    color: {COLOR_GRAD_END} !important;
}}
div[data-baseweb="tab-highlight"] {{
    background-color: {COLOR_GRAD_END} !important;
}}
div[data-baseweb="tab-border"] {{
    background-color: {COLOR_BORDER} !important;
}}

/* Metrics */
[data-testid="stMetricLabel"] {{
    color: {COLOR_TEXT_MUTED} !important;
}}
[data-testid="stMetricValue"] {{
    color: {COLOR_PRIMARY_DARK} !important;
    font-family: 'IBM Plex Mono', monospace;
}}

/* Expander */
details {{
    border: 1px solid {COLOR_BORDER} !important;
    border-radius: 10px !important;
    background: {COLOR_BG_CARD};
}}
summary {{
    font-weight: 600;
    color: {COLOR_PRIMARY_DARK} !important;
}}

/* Select boxes / number inputs */
div[data-baseweb="select"] > div, .stNumberInput input {{
    border-radius: 8px !important;
}}
</style>
""", unsafe_allow_html=True)


# ── Cached resources ──────────────────────────────────────────────────────────
@st.cache_resource
def load_engine():
    return SchedulingEngine.from_settings()


@st.cache_data(ttl=300)
def fetch_all_forecasts(grid_zones: tuple):
    carbon_rows, renewable_rows = [], []
    for zone in grid_zones:
        for w in _mock_forecast(zone, hours=48):
            carbon_rows.append({"time": w.start, "carbon": w.intensity_gco2_kwh, "zone": zone})
        for w in _mock_renewable_forecast(zone, hours=48):
            renewable_rows.append({"time": w.start, "renewable_pct": w.renewable_fraction * 100, "zone": zone})
    return pd.DataFrame(carbon_rows), pd.DataFrame(renewable_rows)


# ── Sidebar ───────────────────────────────────────────────────────────────────
settings = get_settings()
engine = load_engine()
water_service = WaterDataService()

st.sidebar.markdown(
    f"""
    <div style="padding: 12px 14px; background: linear-gradient(120deg, {COLOR_GRAD_START}, {COLOR_GRAD_END}); border-radius:10px; margin-bottom: 4px; box-shadow: 0 2px 8px rgba(249,115,22,0.25);">
        <div style="font-size:1.1rem; font-weight:800; color:#FFFFFF;">GreenScheduler</div>
        <div style="font-size:0.76rem; color:rgba(255,255,255,0.9);">Sustainability Operations Console</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

try:
    api_status = api_get("/")
    st.sidebar.success("API connection: online")
except Exception as e:
    st.sidebar.error(f"API connection failed: {e}")

st.sidebar.markdown("---")
st.sidebar.markdown('<div class="section-label">Objective Weights</div>', unsafe_allow_html=True)
st.sidebar.caption("Adjust in real time to re-balance the scheduler.")

w_carbon = st.sidebar.slider("Carbon intensity", 0.0, 1.0, settings.weights.carbon, 0.05)
w_water = st.sidebar.slider("Water stress", 0.0, 1.0, settings.weights.water, 0.05)
w_renewable = st.sidebar.slider("Renewable energy", 0.0, 1.0, settings.weights.renewable, 0.05)
w_deadline = st.sidebar.slider("Deadline pressure", 0.0, 1.0, settings.weights.deadline, 0.05)
w_community = st.sidebar.slider("Community priority", 0.0, 1.0, settings.weights.community, 0.05)

total_w = w_carbon + w_water + w_renewable + w_deadline + w_community
if abs(total_w - 1.0) > 0.05:
    st.sidebar.warning(f"Weights sum to {total_w:.2f} — target is 1.00")

st.sidebar.markdown("---")
st.sidebar.markdown('<div class="section-label">Carbon Budget</div>', unsafe_allow_html=True)
budget_limit = st.sidebar.number_input(
    "Monthly ceiling (kgCO₂)", min_value=0.0, value=float(carbon_budget._ceiling / 1000), step=100.0
)
if st.sidebar.button("Apply Budget", use_container_width=True):
    carbon_budget._ceiling = budget_limit * 1000
    st.sidebar.success(f"Budget set to {budget_limit:.0f} kgCO₂/month")

# ── Override settings weights from sidebar ────────────────────────────────────
from config.loader import Weights
settings.weights = Weights(
    carbon=w_carbon,
    water=w_water,
    renewable=w_renewable,
    deadline=w_deadline,
    community=w_community,
) if abs(total_w - 1.0) <= 0.05 else settings.weights

# ── Main ──────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="dashboard-header">
        <h1>GreenScheduler</h1>
        <p>Jointly optimising carbon intensity · water stress · renewable availability · deadlines · community priority</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Section 1: Environmental snapshot ─────────────────────────────────────────
st.markdown('<div class="section-label">Live Environmental Snapshot</div>', unsafe_allow_html=True)
region_names = list(settings.regions.keys())

env_data = []
for rname in region_names:
    cfg = settings.regions[rname]
    now = datetime.now(timezone.utc)
    w_end = now + timedelta(hours=1)
    carbon = engine._carbon.get_intensity_at(cfg.grid_zone, now, w_end)
    renewable = engine._renewable.get_fraction_at(cfg.grid_zone, now, w_end)
    water = water_service.get_stress(cfg.water_basin)
    env_data.append({
        "Region": rname,
        "Carbon (gCO₂/kWh)": round(carbon, 0),
        "Renewable %": round(renewable * 100, 1),
        "Water Stress": round(water.stress_index, 2),
        "Drought Alert": "Yes" if water.drought_alert else "No",
        "Community": cfg.community_score,
        "Green Score": round((1 - carbon / 600) * 0.5 + renewable * 0.3 + (1 - water.stress_index) * 0.2, 3),
    })

env_df = pd.DataFrame(env_data).sort_values("Green Score", ascending=False)

# Region metric cards
cols = st.columns(len(region_names))
for i, row in env_df.iterrows():
    idx = env_df.index.get_loc(i)
    with cols[idx]:
        card_class = "region-card leading" if idx == 0 else "region-card"
        badge = '<span class="badge">★ Top ranked</span>' if idx == 0 else ""
        drought_note = f" · Drought alert" if row["Drought Alert"] == "Yes" else ""
        st.markdown(
            f"""<div class="{card_class}">
            <span class="region-name">{row['Region']}</span>{badge}
            <div class="metric-row">
            Carbon &nbsp;<b>{row['Carbon (gCO₂/kWh)']:.0f}</b> gCO₂/kWh<br>
            Renewable &nbsp;<b>{row['Renewable %']:.1f}%</b><br>
            Water stress &nbsp;<b>{row['Water Stress']:.2f}</b>{drought_note}<br>
            Green score &nbsp;<b>{row['Green Score']:.3f}</b>
            </div>
            </div>""",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Section 2: Cross-region comparison ────────────────────────────────────────
st.markdown('<div class="section-label">Cross-Region Comparison</div>', unsafe_allow_html=True)
grid_zones = tuple(cfg.grid_zone for cfg in settings.regions.values())
carbon_df, renewable_df = fetch_all_forecasts(grid_zones)

zone_to_region = {cfg.grid_zone: rname for rname, cfg in settings.regions.items()}
carbon_df["region"] = carbon_df["zone"].map(zone_to_region)
renewable_df["region"] = renewable_df["zone"].map(zone_to_region)

REGION_COLOR_SEQUENCE = ["#F97316", "#FBBF24", "#EF4444", "#FB923C", "#FDE047", "#F87171"]

tab_c, tab_r, tab_w = st.tabs(["Carbon Intensity", "Renewable Mix", "Water Stress"])

with tab_c:
    fig = px.line(
        carbon_df, x="time", y="carbon", color="region",
        title="48-hour Carbon Intensity Forecast — All Regions",
        labels={"carbon": "gCO₂/kWh", "time": "Time (UTC)", "region": "Region"},
        height=380,
        color_discrete_sequence=REGION_COLOR_SEQUENCE,
    )
    fig.add_hline(y=settings.constraints.max_carbon_intensity, line_dash="dash",
                  line_color="#EF4444", annotation_text="Hard cap")
    fig.add_hline(y=250, line_dash="dot", line_color="#FBBF24", annotation_text="Preferred max")
    fig.update_layout(
        plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
        font=dict(family="Inter, sans-serif", color=CHART_FONT),
        legend_title_text="",
        xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
        yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None)

with tab_r:
    fig2 = px.area(
        renewable_df, x="time", y="renewable_pct", color="region",
        title="48-hour Renewable Fraction Forecast — All Regions",
        labels={"renewable_pct": "Renewable %", "time": "Time (UTC)", "region": "Region"},
        height=380,
        color_discrete_sequence=REGION_COLOR_SEQUENCE,
    )
    fig2.add_hline(y=60, line_dash="dot", line_color="#FDE047", annotation_text="60% preferred")
    fig2.update_layout(
        plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
        font=dict(family="Inter, sans-serif", color=CHART_FONT),
        legend_title_text="",
        xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
        yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
    )
    st.plotly_chart(fig2, use_container_width=True, theme=None)

with tab_w:
    water_rows = []
    for rname in region_names:
        cfg = settings.regions[rname]
        w = water_service.get_stress(cfg.water_basin)
        water_rows.append({"Region": rname, "Stress": w.stress_index, "Basin": cfg.water_basin})
    wdf = pd.DataFrame(water_rows).sort_values("Stress")
    fig3 = px.bar(
        wdf, x="Region", y="Stress", color="Stress",
        color_continuous_scale=["#FBBF24", "#F97316", "#EF4444"],
        title="Current Water Stress by Region",
        range_color=[0, 1], height=350,
    )
    fig3.add_hline(y=settings.constraints.max_water_stress, line_dash="dash",
                   line_color="#EF4444", annotation_text="Hard limit")
    fig3.update_layout(
        plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
        font=dict(family="Inter, sans-serif", color=CHART_FONT),
        xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
        yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
    )
    st.plotly_chart(fig3, use_container_width=True, theme=None)

st.markdown("---")

# ── Section 3: Cumulative Impact ───────────────────────────────────────────────
st.markdown('<div class="section-label">Cumulative Environmental Impact</div>', unsafe_allow_html=True)
lifetime = carbon_budget.lifetime_summary()
total_saved = lifetime["total_saved_gco2"]
total_emitted = lifetime["total_emitted_gco2"]
jobs_sched = lifetime["jobs_scheduled"]

imp_col1, imp_col2, imp_col3, imp_col4, imp_col5 = st.columns(5)
imp_col1.metric("CO₂ Saved", f"{total_saved/1000:.2f} kgCO₂",
                delta=f"{lifetime['saving_rate_pct']:.1f}% saving rate")
imp_col2.metric("CO₂ Emitted", f"{total_emitted/1000:.2f} kgCO₂")
imp_col3.metric("Jobs Scheduled", str(jobs_sched))
imp_col4.metric("Trees Equivalent", f"{total_saved/21000:.1f}",
                delta="trees/year offset")
imp_col5.metric("Car km Avoided", f"{total_saved/120:.0f} km")

# ── Section 4: Carbon Budget Gauge ────────────────────────────────────────────
if carbon_budget._ceiling > 0:
    st.markdown("---")
    st.markdown('<div class="section-label">Carbon Budget</div>', unsafe_allow_html=True)
    bsummary = carbon_budget.current_period_summary()
    util = bsummary["utilisation_pct"]

    gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=util,
        title={"text": f"Budget Utilisation — {bsummary['period']}", "font": {"color": CHART_FONT}},
        delta={"reference": 80, "increasing": {"color": "#EF4444"}, "font": {"color": CHART_FONT}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": CHART_MUTED, "tickfont": {"color": CHART_MUTED}},
            "bar": {"color": COLOR_PRIMARY},
            "bgcolor": CHART_BG,
            "bordercolor": CHART_GRID,
            "steps": [
                {"range": [0, 60], "color": "#3A2A12"},
                {"range": [60, 80], "color": "#5A3A14"},
                {"range": [80, 100], "color": "#5A1E1E"},
            ],
            "threshold": {"line": {"color": "#EF4444", "width": 4}, "value": 90},
        },
        number={"suffix": "%", "font": {"color": CHART_FONT}},
    ))
    gauge.update_layout(height=280, font=dict(family="Inter, sans-serif", color=CHART_FONT),
                         paper_bgcolor=CHART_BG, plot_bgcolor=CHART_BG)
    bcol1, bcol2 = st.columns([1, 2])
    with bcol1:
        st.plotly_chart(gauge, use_container_width=True, theme=None)
    with bcol2:
        st.metric("Ceiling", f"{bsummary['ceiling_gco2']/1000:.1f} kgCO₂")
        st.metric("Spent", f"{bsummary['spent_gco2']/1000:.2f} kgCO₂")
        st.metric("Remaining", f"{bsummary['remaining_gco2']/1000:.2f} kgCO₂",
                  delta="Available" if not bsummary["is_exhausted"] else "Exhausted",
                  delta_color="normal" if not bsummary["is_exhausted"] else "inverse")

st.markdown("---")

# ── Section 5: Schedule Simulator ─────────────────────────────────────────────
st.markdown('<div class="section-label">Interactive Schedule Simulator</div>', unsafe_allow_html=True)
st.caption("Configure a job and compare GreenScheduler's choice vs naive scheduling.")

sim_c1, sim_c2, sim_c3, sim_c4 = st.columns(4)
gpu_hours = sim_c1.slider("GPU-hours", 1.0, 200.0, 10.0, 1.0)
num_gpus = sim_c2.selectbox("# GPUs", [1, 2, 4, 8, 16, 32], index=2)
deadline_hours = sim_c3.slider("Deadline (hrs from now)", 4, 96, 24, 4)
priority = sim_c4.selectbox("Priority", ["critical", "high", "standard", "low", "batch"])

gpu_tdp = st.select_slider(
    "GPU TDP (watts/GPU)",
    options=[150, 250, 300, 350, 400, 700],
    value=300,
    help="A100=300W, H100=700W, A10=150W",
)

wall_clock = gpu_hours / num_gpus
st.caption(
    f"Wall-clock duration: **{wall_clock:.1f} hours** | "
    f"Total power draw: **{num_gpus * gpu_tdp / 1000:.1f} kW**"
)

if st.button("Run GreenScheduler", type="primary", use_container_width=True):
    from config.loader import Settings as S, Weights as W, Constraints, Scheduling, CacheConfig, ServerConfig, LoggingConfig
    # Build custom settings from sidebar weights
    custom_settings = S(
        api_keys=settings.api_keys,
        weights=W(carbon=w_carbon, water=w_water, renewable=w_renewable,
                  deadline=w_deadline, community=w_community)
        if abs(total_w - 1.0) <= 0.05 else settings.weights,
        constraints=settings.constraints,
        scheduling=settings.scheduling,
        regions=settings.regions,
        cache=settings.cache,
        server=settings.server,
        logging=settings.logging,
    )
    custom_engine = SchedulingEngine.from_settings(custom_settings)

    job = JobRequest(
        gpu_hours=gpu_hours,
        num_gpus=num_gpus,
        gpu_tdp_watts=float(gpu_tdp),
        deadline=datetime.now(timezone.utc) + timedelta(hours=deadline_hours),
        priority=Priority(priority),
    )

    with st.spinner("Evaluating candidates across all regions and windows…"):
        result = custom_engine.schedule(job)

    if result.is_feasible:
        b = result.best

        st.success(f"Best window found: **{b.region}** @ {b.window_start.strftime('%Y-%m-%d %H:%M UTC')}")

        # What-if comparison
        st.markdown("##### GreenScheduler vs Naive Scheduling")
        cmp_c1, cmp_c2, cmp_c3 = st.columns(3)

        if result.naive_baseline:
            nb = result.naive_baseline
            cmp_c1.metric(
                "Carbon Intensity",
                f"{b.carbon_intensity:.0f} gCO₂/kWh",
                delta=f"{b.carbon_intensity - nb.carbon_intensity:.0f} vs naive ({nb.carbon_intensity:.0f})",
                delta_color="inverse",
            )
            cmp_c2.metric(
                "CO₂ Emitted",
                f"{result.carbon_emitted_gco2/1000:.3f} kgCO₂",
                delta=f"Saves {result.carbon_saved_gco2/1000:.3f} kgCO₂ ({result.carbon_saved_pct:.1f}%)",
                delta_color="normal",
            )
            cmp_c3.metric(
                "Renewable Mix",
                f"{b.renewable_fraction:.1%}",
                delta=f"{(b.renewable_fraction - nb.renewable_fraction):.1%} vs naive",
                delta_color="normal",
            )

            # Comparison bar chart
            cmp_df = pd.DataFrame([
                {"Scheduler": "GreenScheduler", "Carbon (gCO₂/kWh)": b.carbon_intensity,
                 "Renewable %": b.renewable_fraction * 100, "Water Stress": b.water_stress},
                {"Scheduler": "Naive (immediate)", "Carbon (gCO₂/kWh)": nb.carbon_intensity,
                 "Renewable %": nb.renewable_fraction * 100, "Water Stress": nb.water_stress},
            ])
            fig_cmp = px.bar(
                cmp_df, x="Scheduler", y="Carbon (gCO₂/kWh)",
                color="Scheduler", color_discrete_map={
                    "GreenScheduler": "#F97316",
                    "Naive (immediate)": "#EF4444",
                },
                title="Carbon Intensity: GreenScheduler vs Naive",
                height=300,
            )
            fig_cmp.update_layout(
                plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
                font=dict(family="Inter, sans-serif", color=CHART_FONT),
                xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
                yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
            )
            st.plotly_chart(fig_cmp, use_container_width=True, theme=None)

        # Score breakdown
        with st.expander("Score breakdown"):
            bd_df = pd.DataFrame([
                {"Component": "Carbon", "Contribution": round(b.carbon_contribution, 4)},
                {"Component": "Water", "Contribution": round(b.water_contribution, 4)},
                {"Component": "Renewable", "Contribution": round(b.renewable_contribution, 4)},
                {"Component": "Deadline", "Contribution": round(b.deadline_contribution, 4)},
                {"Component": "Community", "Contribution": round(b.community_contribution, 4)},
            ])
            fig_bd = px.bar(bd_df, x="Component", y="Contribution", color="Contribution",
                            color_continuous_scale=["#FBBF24", CHART_MUTED, "#EF4444"],
                            title="Score Component Breakdown (negative = good)", height=280)
            fig_bd.update_layout(
                plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
                font=dict(family="Inter, sans-serif", color=CHART_FONT),
                xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
                yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
            )
            st.plotly_chart(fig_bd, use_container_width=True, theme=None)

        # Top candidates table
        st.markdown("##### Top Candidate Windows")
        top = [c for c in result.all_candidates if c.feasible][:15]
        if top:
            tdf = pd.DataFrame([{
                "Region": c.region,
                "Start (UTC)": c.window_start.strftime("%m-%d %H:%M"),
                "Score": round(c.total_score, 4),
                "Carbon": round(c.carbon_intensity, 0),
                "Renewable %": f"{c.renewable_fraction:.1%}",
                "Water": round(c.water_stress, 2),
                "Deadline P.": round(c.deadline_pressure, 2),
                "Warnings": len(c.soft_warnings),
            } for c in top])
            st.dataframe(tdf, use_container_width=True, hide_index=True)

        # Timeline view — scatter plot of all feasible windows
        st.markdown("##### Candidate Timeline")
        timeline_data = [
            {
                "Region": c.region,
                "Start": c.window_start,
                "Score": c.total_score,
                "Carbon": c.carbon_intensity,
                "Feasible": c.feasible,
            }
            for c in result.all_candidates if c.feasible
        ]
        if timeline_data:
            tl_df = pd.DataFrame(timeline_data)
            best_key = (b.region, b.window_start)
            tl_df["Best"] = tl_df.apply(
                lambda r: "Best" if (r["Region"], r["Start"]) == best_key else "Other",
                axis=1,
            )
            fig_tl = px.scatter(
                tl_df, x="Start", y="Carbon", color="Region", symbol="Best",
                size=[8 if x == "Best" else 4 for x in tl_df["Best"]],
                title="Feasible Windows — Carbon Intensity Over Time",
                labels={"Carbon": "Carbon (gCO₂/kWh)", "Start": "Window Start (UTC)"},
                height=380,
                color_discrete_sequence=REGION_COLOR_SEQUENCE,
            )
            fig_tl.update_layout(
                plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG,
                font=dict(family="Inter, sans-serif", color=CHART_FONT),
                xaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
                yaxis=dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, color=CHART_MUTED),
            )
            st.plotly_chart(fig_tl, use_container_width=True, theme=None)

    else:
        st.error(result.explanation())

st.markdown("---")
# ── Section 6: Live Job Queue ──────────────────────────────────────────────────
st.markdown('<div class="section-label">Live Job Queue</div>', unsafe_allow_html=True)

try:
    # Get jobs from the deployed API
    all_jobs = api_get("/api/v1/jobs")

    if not all_jobs:
        st.info("No jobs in queue. Submit a job via the API or the simulator above.")

    else:
        job_rows = []

        for j in all_jobs:
            scheduled_start = j.get("scheduled_start")

            if scheduled_start:
                try:
                    scheduled_start_display = datetime.fromisoformat(
                        scheduled_start.replace("Z", "+00:00")
                    ).strftime("%m-%d %H:%M")
                except Exception:
                    scheduled_start_display = scheduled_start
            else:
                scheduled_start_display = "—"

            renewable = j.get("renewable_fraction")

            job_rows.append({
                "Job ID": j.get("job_id", "—"),
                "Name": j.get("name") or "—",
                "Status": j.get("status", "—"),
                "Priority": j.get("priority", "—"),
                "GPU-hrs": j.get("gpu_hours", 0),
                "Region": j.get("assigned_region") or "—",
                "CO₂ Saved (g)": j.get("carbon_saved_gco2") or 0,
                "CO₂ Emitted (g)": j.get("carbon_emitted_gco2") or 0,
                "Renewable %": (
                    f"{renewable * 100:.1f}%"
                    if renewable is not None
                    else "—"
                ),
                "Scheduled Start": scheduled_start_display,
            })

        jdf = pd.DataFrame(job_rows)

        # Colour status — muted, professional palette
        def colour_status(val):
            colours = {
                "scheduled": "background-color: #4A3009; color: #FBBF24;",
                "running": "background-color: #7C2D12; color: #FDBA74;",
                "completed": "background-color: #1F2937; color: #A7F3D0;",
                "deferred": "background-color: #4A3009; color: #FDE68A;",
                "failed": "background-color: #4C1D1D; color: #FCA5A5;",
            }
            return colours.get(val, "")

        st.dataframe(
            jdf.style.applymap(colour_status, subset=["Status"]),
            use_container_width=True,
            hide_index=True,
        )

        # ── Job lifecycle controls ─────────────────────────────────────────────
        st.caption("Transition a job through its lifecycle:")

        job_ids = [j.get("job_id") for j in all_jobs if j.get("job_id")]

        if job_ids:
            lc_col1, lc_col2, lc_col3, lc_col4 = st.columns(4)

            selected_jid = lc_col1.selectbox(
                "Select job",
                job_ids,
                format_func=lambda x: x[:16]
            )

            # Start job
            if lc_col2.button("Start Job", use_container_width=True):
                try:
                    result = api_post(
                        f"/api/v1/jobs/{selected_jid}/start"
                    )
                    st.success(
                        f"Job {selected_jid} is now RUNNING"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not start job: {e}")

            # Complete job
            if lc_col3.button("Complete Job", use_container_width=True):
                try:
                    result = api_post(
                        f"/api/v1/jobs/{selected_jid}/complete"
                    )
                    st.success(
                        f"Job {selected_jid} is now COMPLETED"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not complete job: {e}")

            # Cancel job
            if lc_col4.button("Cancel Job", use_container_width=True):
                try:
                    api_delete(
                        f"/api/v1/jobs/{selected_jid}"
                    )
                    st.success(
                        f"Job {selected_jid} was cancelled"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not cancel job: {e}")

except Exception as e:
    st.error(f"Could not load jobs from deployed API: {e}")

st.markdown("---")
st.caption(
    "GreenScheduler v2.0 · Jointly optimises carbon intensity · water stress · "
    "renewable availability · workload deadlines · community priority"
)
