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
    page_title="GreenScheduler",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 12px; padding: 16px; margin: 4px;
    border-left: 4px solid #00d4aa;
}
.impact-number { font-size: 2.5rem; font-weight: 700; color: #00d4aa; }
.saved-label { font-size: 0.85rem; color: #aaa; }
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

st.sidebar.title("🌿 GreenScheduler")
st.sidebar.caption("Environmentally-aware AI infrastructure scheduler")
st.sidebar.markdown("---")

st.sidebar.subheader("⚖️ Live Objective Weights")
st.sidebar.caption("Drag to re-weight the scheduler in real time.")

w_carbon = st.sidebar.slider("Carbon intensity", 0.0, 1.0, settings.weights.carbon, 0.05)
w_water = st.sidebar.slider("Water stress", 0.0, 1.0, settings.weights.water, 0.05)
w_renewable = st.sidebar.slider("Renewable energy", 0.0, 1.0, settings.weights.renewable, 0.05)
w_deadline = st.sidebar.slider("Deadline pressure", 0.0, 1.0, settings.weights.deadline, 0.05)
w_community = st.sidebar.slider("Community priority", 0.0, 1.0, settings.weights.community, 0.05)

total_w = w_carbon + w_water + w_renewable + w_deadline + w_community
if abs(total_w - 1.0) > 0.05:
    st.sidebar.warning(f"Weights sum to {total_w:.2f} — they should sum to 1.0")

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Carbon Budget")
budget_limit = st.sidebar.number_input(
    "Monthly ceiling (kgCO₂)", min_value=0.0, value=float(carbon_budget._ceiling / 1000), step=100.0
)
if st.sidebar.button("Apply Budget"):
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
st.title("🌿 GreenScheduler")
st.caption("Jointly optimising carbon · water · renewables · deadlines · community")
st.markdown("---")

# ── Section 1: Environmental snapshot ─────────────────────────────────────────
st.subheader("🌍 Live Environmental Snapshot")
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
        "Drought ⚠️": "⚠️" if water.drought_alert else "✅",
        "Community": cfg.community_score,
        "Green Score": round((1 - carbon / 600) * 0.5 + renewable * 0.3 + (1 - water.stress_index) * 0.2, 3),
    })

env_df = pd.DataFrame(env_data).sort_values("Green Score", ascending=False)

# Region metric cards
cols = st.columns(len(region_names))
for i, row in env_df.iterrows():
    idx = env_df.index.get_loc(i)
    with cols[idx]:
        border = "#00d4aa" if idx == 0 else "#444"
        label = "🏆 Greenest Now" if idx == 0 else ""
        st.markdown(
            f"""<div style="border:2px solid {border}; border-radius:10px; padding:12px; margin:4px;">
            <b>{row['Region']}</b> {label}<br>
            ⚡ <b>{row['Carbon (gCO₂/kWh)']:.0f}</b> gCO₂/kWh<br>
            🌱 <b>{row['Renewable %']:.1f}%</b> renewable<br>
            💧 Stress: <b>{row['Water Stress']:.2f}</b> {row['Drought ⚠️']}<br>
            📊 Green score: <b>{row['Green Score']:.3f}</b>
            </div>""",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Section 2: Cross-region comparison ────────────────────────────────────────
st.subheader("📊 Cross-Region Comparison")
grid_zones = tuple(cfg.grid_zone for cfg in settings.regions.values())
carbon_df, renewable_df = fetch_all_forecasts(grid_zones)

zone_to_region = {cfg.grid_zone: rname for rname, cfg in settings.regions.items()}
carbon_df["region"] = carbon_df["zone"].map(zone_to_region)
renewable_df["region"] = renewable_df["zone"].map(zone_to_region)

tab_c, tab_r, tab_w = st.tabs(["⚡ Carbon Intensity", "🌱 Renewable Mix", "💧 Water Stress"])

with tab_c:
    fig = px.line(
        carbon_df, x="time", y="carbon", color="region",
        title="48-hour Carbon Intensity Forecast — All Regions",
        labels={"carbon": "gCO₂/kWh", "time": "Time (UTC)", "region": "Region"},
        height=380,
    )
    fig.add_hline(y=settings.constraints.max_carbon_intensity, line_dash="dash",
                  line_color="red", annotation_text="Hard cap")
    fig.add_hline(y=250, line_dash="dot", line_color="orange", annotation_text="Preferred max")
    st.plotly_chart(fig, use_container_width=True)

with tab_r:
    fig2 = px.area(
        renewable_df, x="time", y="renewable_pct", color="region",
        title="48-hour Renewable Fraction Forecast — All Regions",
        labels={"renewable_pct": "Renewable %", "time": "Time (UTC)", "region": "Region"},
        height=380,
    )
    fig2.add_hline(y=60, line_dash="dot", line_color="green", annotation_text="60% preferred")
    st.plotly_chart(fig2, use_container_width=True)

with tab_w:
    water_rows = []
    for rname in region_names:
        cfg = settings.regions[rname]
        w = water_service.get_stress(cfg.water_basin)
        water_rows.append({"Region": rname, "Stress": w.stress_index, "Basin": cfg.water_basin})
    wdf = pd.DataFrame(water_rows).sort_values("Stress")
    fig3 = px.bar(
        wdf, x="Region", y="Stress", color="Stress",
        color_continuous_scale=["#00d4aa", "yellow", "red"],
        title="Current Water Stress by Region",
        range_color=[0, 1], height=350,
    )
    fig3.add_hline(y=settings.constraints.max_water_stress, line_dash="dash",
                   line_color="red", annotation_text="Hard limit")
    st.plotly_chart(fig3, use_container_width=True)

st.markdown("---")

# ── Section 3: Cumulative Impact ───────────────────────────────────────────────
st.subheader("🌱 Cumulative Environmental Impact")
lifetime = carbon_budget.lifetime_summary()
total_saved = lifetime["total_saved_gco2"]
total_emitted = lifetime["total_emitted_gco2"]
jobs_sched = lifetime["jobs_scheduled"]

imp_col1, imp_col2, imp_col3, imp_col4, imp_col5 = st.columns(5)
imp_col1.metric("♻️ CO₂ Saved", f"{total_saved/1000:.2f} kgCO₂",
                delta=f"{lifetime['saving_rate_pct']:.1f}% saving rate")
imp_col2.metric("⚡ CO₂ Emitted", f"{total_emitted/1000:.2f} kgCO₂")
imp_col3.metric("✅ Jobs Scheduled", str(jobs_sched))
imp_col4.metric("🌳 Trees Equivalent", f"{total_saved/21000:.1f}",
                delta="trees/year offset")
imp_col5.metric("🚗 Car km Avoided", f"{total_saved/120:.0f} km")

# ── Section 4: Carbon Budget Gauge ────────────────────────────────────────────
if carbon_budget._ceiling > 0:
    st.markdown("---")
    st.subheader("💰 Carbon Budget")
    bsummary = carbon_budget.current_period_summary()
    util = bsummary["utilisation_pct"]

    gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=util,
        title={"text": f"Budget Utilisation — {bsummary['period']}"},
        delta={"reference": 80, "increasing": {"color": "red"}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#00d4aa"},
            "steps": [
                {"range": [0, 60], "color": "#1a3a2a"},
                {"range": [60, 80], "color": "#3a3a1a"},
                {"range": [80, 100], "color": "#3a1a1a"},
            ],
            "threshold": {"line": {"color": "red", "width": 4}, "value": 90},
        },
        number={"suffix": "%"},
    ))
    gauge.update_layout(height=280)
    bcol1, bcol2 = st.columns([1, 2])
    with bcol1:
        st.plotly_chart(gauge, use_container_width=True)
    with bcol2:
        st.metric("Ceiling", f"{bsummary['ceiling_gco2']/1000:.1f} kgCO₂")
        st.metric("Spent", f"{bsummary['spent_gco2']/1000:.2f} kgCO₂")
        st.metric("Remaining", f"{bsummary['remaining_gco2']/1000:.2f} kgCO₂",
                  delta="Available" if not bsummary["is_exhausted"] else "⛔ Exhausted",
                  delta_color="normal" if not bsummary["is_exhausted"] else "inverse")

st.markdown("---")

# ── Section 5: Schedule Simulator ─────────────────────────────────────────────
st.subheader("🔮 Interactive Schedule Simulator")
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

if st.button("▶ Run GreenScheduler", type="primary", use_container_width=True):
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

        st.success(f"**Best window found:** {b.region} @ {b.window_start.strftime('%Y-%m-%d %H:%M UTC')}")

        # What-if comparison
        st.markdown("### 📊 GreenScheduler vs Naive Scheduling")
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
                {"Scheduler": "🌿 GreenScheduler", "Carbon (gCO₂/kWh)": b.carbon_intensity,
                 "Renewable %": b.renewable_fraction * 100, "Water Stress": b.water_stress},
                {"Scheduler": "⚡ Naive (immediate)", "Carbon (gCO₂/kWh)": nb.carbon_intensity,
                 "Renewable %": nb.renewable_fraction * 100, "Water Stress": nb.water_stress},
            ])
            fig_cmp = px.bar(
                cmp_df, x="Scheduler", y="Carbon (gCO₂/kWh)",
                color="Scheduler", color_discrete_map={
                    "🌿 GreenScheduler": "#00d4aa",
                    "⚡ Naive (immediate)": "#e74c3c",
                },
                title="Carbon Intensity: GreenScheduler vs Naive",
                height=300,
            )
            st.plotly_chart(fig_cmp, use_container_width=True)

        # Score breakdown
        with st.expander("📋 Score breakdown"):
            bd_df = pd.DataFrame([
                {"Component": "Carbon", "Contribution": round(b.carbon_contribution, 4)},
                {"Component": "Water", "Contribution": round(b.water_contribution, 4)},
                {"Component": "Renewable", "Contribution": round(b.renewable_contribution, 4)},
                {"Component": "Deadline", "Contribution": round(b.deadline_contribution, 4)},
                {"Component": "Community", "Contribution": round(b.community_contribution, 4)},
            ])
            fig_bd = px.bar(bd_df, x="Component", y="Contribution", color="Contribution",
                            color_continuous_scale=["#00d4aa", "white", "#e74c3c"],
                            title="Score Component Breakdown (negative = good)", height=280)
            st.plotly_chart(fig_bd, use_container_width=True)

        # Top candidates table
        st.markdown("### 🏆 Top Candidate Windows")
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
        st.markdown("### 🗓️ Candidate Timeline")
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
                lambda r: "★ Best" if (r["Region"], r["Start"]) == best_key else "Other",
                axis=1,
            )
            fig_tl = px.scatter(
                tl_df, x="Start", y="Carbon", color="Region", symbol="Best",
                size=[8 if x == "★ Best" else 4 for x in tl_df["Best"]],
                title="Feasible Windows — Carbon Intensity Over Time",
                labels={"Carbon": "Carbon (gCO₂/kWh)", "Start": "Window Start (UTC)"},
                height=380,
            )
            st.plotly_chart(fig_tl, use_container_width=True)

    else:
        st.error(result.explanation())

st.markdown("---")

# ── Section 6: Live Job Queue ──────────────────────────────────────────────────
st.subheader("📋 Live Job Queue")

all_jobs = job_queue.list_all()
if not all_jobs:
    st.info("No jobs in queue. Submit a job via the API or the simulator above.")
else:
    job_rows = []
    for j in all_jobs:
        job_rows.append({
            "Job ID": j.request.job_id[:8] + "…",
            "Name": j.request.name or "—",
            "Status": j.status.value,
            "Priority": j.request.priority.value,
            "GPU-hrs": j.request.gpu_hours,
            "Region": j.assigned_region or "—",
            "CO₂ Saved (g)": j.carbon_saved_gco2 or 0,
            "CO₂ Emitted (g)": j.carbon_emitted_gco2 or 0,
            "Renewable %": f"{j.renewable_fraction:.1%}" if j.renewable_fraction else "—",
            "Scheduled Start": j.scheduled_start.strftime("%m-%d %H:%M") if j.scheduled_start else "—",
        })
    jdf = pd.DataFrame(job_rows)

    # Colour status
    def colour_status(val):
        colours = {
            "scheduled": "background-color: #1a3a2a",
            "running": "background-color: #1a2a3a",
            "completed": "background-color: #2a1a3a",
            "deferred": "background-color: #3a2a1a",
            "failed": "background-color: #3a1a1a",
        }
        return colours.get(val, "")

    st.dataframe(
        jdf.style.applymap(colour_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    # Quick lifecycle buttons
    st.caption("Transition a job through its lifecycle:")
    lc_col1, lc_col2, lc_col3 = st.columns(3)
    job_ids = [j.request.job_id for j in all_jobs]
    selected_jid = lc_col1.selectbox("Select job", job_ids,
                                      format_func=lambda x: x[:16])
    if lc_col2.button("▶ Mark Running"):
        j = job_queue.get(selected_jid)
        if j:
            try:
                j.mark_running()
                job_queue.update(j)
                st.success(f"Job {selected_jid[:8]} is now RUNNING")
                st.rerun()
            except Exception as e:
                st.error(str(e))
    if lc_col3.button("✅ Mark Completed"):
        j = job_queue.get(selected_jid)
        if j:
            try:
                j.mark_completed()
                job_queue.update(j)
                carbon_budget.commit(
                    selected_jid,
                    actual_gco2=j.carbon_emitted_gco2 or 0,
                    saved_gco2=j.carbon_saved_gco2 or 0,
                )
                st.success(f"Job {selected_jid[:8]} COMPLETED — carbon committed to budget")
                st.rerun()
            except Exception as e:
                st.error(str(e))

st.markdown("---")
st.caption(
    "GreenScheduler v2.0 · Jointly optimises carbon intensity · water stress · "
    "renewable availability · workload deadlines · community priority"
)
