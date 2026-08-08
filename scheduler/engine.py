"""
scheduler/engine.py
Core scheduling engine for GreenScheduler.

Improvements over v1:
  - gpu_hours / num_gpus correctly separates GPU-hours from wall-clock duration
  - Carbon math uses actual GPU power draw (TDP × wall-clock hours) for gCO₂
  - Naive baseline (immediate worst-region execution) drives the "what-if" comparison
  - Priority urgency multiplier wired into deadline pressure
  - constraints.py drought alerts now gate scheduling
  - Soft constraint violations surfaced in ScoreBreakdown
  - Carbon budget check integrated
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config.loader import Settings, get_settings
from data.carbon import CarbonDataService
from data.renewable import RenewableDataService
from data.water import WaterDataService
from scheduler.constraints import check_hard_constraints, check_soft_constraints
from scheduler.scorer import INFEASIBLE, ScoreBreakdown, Scorer
from workloads.deadline import deadline_pressure
from workloads.job import PRIORITY_URGENCY, JobRequest, ScheduledJob

logger = logging.getLogger(__name__)


@dataclass
class NaiveBaseline:
    """What the job would have emitted with naive (immediate, any-region) scheduling."""
    region: str
    carbon_intensity: float
    carbon_gco2: float
    renewable_fraction: float
    water_stress: float


@dataclass
class SchedulingResult:
    """Output of a single scheduling run."""

    job_id: str
    best: Optional[ScoreBreakdown]
    all_candidates: List[ScoreBreakdown]
    naive_baseline: Optional[NaiveBaseline] = None
    carbon_saved_pct: float = 0.0
    water_saved_pct: float = 0.0
    carbon_saved_gco2: float = 0.0
    carbon_emitted_gco2: float = 0.0
    naive_carbon_gco2: float = 0.0

    @property
    def is_feasible(self) -> bool:
        return self.best is not None and self.best.feasible

    def explanation(self) -> str:
        if not self.is_feasible:
            return (
                f"No feasible window found for job {self.job_id} within the "
                f"scheduling horizon. Consider relaxing constraints or extending the deadline."
            )
        b = self.best
        naive_note = ""
        if self.naive_baseline:
            naive_note = (
                f" Naive scheduling would have emitted "
                f"{self.naive_carbon_gco2/1000:.2f} kgCO₂ "
                f"({self.naive_baseline.region}, {self.naive_baseline.carbon_intensity:.0f} gCO₂/kWh). "
                f"GreenScheduler saves {self.carbon_saved_gco2/1000:.2f} kgCO₂ "
                f"({self.carbon_saved_pct:.1f}%)."
            )
        return (
            f"Scheduled in {b.region} starting {b.window_start.strftime('%Y-%m-%d %H:%M UTC')}. "
            f"Carbon: {b.carbon_intensity:.0f} gCO₂/kWh | "
            f"Renewable: {b.renewable_fraction:.1%} | "
            f"Water stress: {b.water_stress:.2f} | "
            f"Emits: {self.carbon_emitted_gco2/1000:.2f} kgCO₂."
            + naive_note
        )


class SchedulingEngine:
    """
    Multi-objective scheduling engine.

    Usage
    -----
    engine = SchedulingEngine.from_settings()
    result = engine.schedule(job_request)
    """

    def __init__(
        self,
        settings: Settings,
        carbon_service: CarbonDataService,
        renewable_service: RenewableDataService,
        water_service: WaterDataService,
    ) -> None:
        self._settings = settings
        self._carbon = carbon_service
        self._renewable = renewable_service
        self._water = water_service
        self._scorer = Scorer(settings)

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "SchedulingEngine":
        s = settings or get_settings()
        from data.cache import build_cache
        cache = build_cache(
            backend=s.cache.backend,
            redis_url=s.cache.redis_url,
            ttl=s.cache.ttl_seconds,
        )
        return cls(
            settings=s,
            carbon_service=CarbonDataService(
                electricity_maps_key=s.api_keys.electricity_maps,
                watttime_key=s.api_keys.watttime,
                cache=cache,
                ttl=s.cache.ttl_seconds,
            ),
            renewable_service=RenewableDataService(
                electricity_maps_key=s.api_keys.electricity_maps,
                cache=cache,
                ttl=s.cache.ttl_seconds,
            ),
            water_service=WaterDataService(
                aqueduct_key=s.api_keys.wri_aqueduct,
                cache=cache,
            ),
        )

    # ── Main entry point ───────────────────────────────────────────────────

    def schedule(self, request: JobRequest) -> SchedulingResult:
        """Find the optimal (region, window) pair for the given job."""
        sched = self._settings.scheduling
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=sched.lookahead_hours)
        step = timedelta(minutes=sched.window_resolution_minutes)

        # Wall-clock duration corrected for parallelism
        wall_hours = request.wall_clock_hours
        power_kw = request.total_power_kw
        urgency = PRIORITY_URGENCY.get(request.priority.value, 2.0)

        candidate_regions = (
            [r for r in request.regions if r in self._settings.regions]
            if request.regions
            else list(self._settings.regions.keys())
        )
        if not candidate_regions:
            logger.warning("No matching regions for job %s", request.job_id)
            return SchedulingResult(job_id=request.job_id, best=None, all_candidates=[])

        deadline = request.deadline
        if deadline is None:
            deadline = now + timedelta(hours=sched.default_deadline_hours)

        logger.info(
            "Scheduling job=%s | wall_hours=%.1f | power=%.1fkW | regions=%s | priority=%s",
            request.job_id, wall_hours, power_kw, candidate_regions, request.priority.value,
        )

        all_candidates: List[ScoreBreakdown] = []

        # Pre-fetch all environmental data
        env_cache: Dict[str, dict] = {}
        for region_name in candidate_regions:
            cfg = self._settings.regions[region_name]
            env_cache[region_name] = {
                "carbon_map": {
                    w.start: w.intensity_gco2_kwh
                    for w in self._carbon.get_forecast(cfg.grid_zone)
                },
                "renewable_map": {
                    w.start: w.renewable_fraction
                    for w in self._renewable.get_forecast(cfg.grid_zone)
                },
                "water": self._water.get_stress(cfg.water_basin),
                "community_score": cfg.community_score,
            }

        # ── Naive baseline: immediate execution, highest-carbon feasible region ──
        naive_baseline = self._compute_naive_baseline(
            candidate_regions, env_cache, now, wall_hours, power_kw
        )

        # ── Enumerate candidate windows ────────────────────────────────────────
        for region_name in candidate_regions:
            cfg = self._settings.regions[region_name]
            e = env_cache[region_name]
            water = e["water"]

            t = now
            while t + timedelta(hours=wall_hours) <= horizon:
                window_end = t + timedelta(hours=wall_hours)
                carbon = self._average_over_window(e["carbon_map"], t, window_end, 300.0)
                renewable = self._average_over_window(e["renewable_map"], t, window_end, 0.3)
                stress = water.stress_index

                # ── Hard constraint gate (including drought alert) ──────────────
                hard_check = check_hard_constraints(
                    carbon_intensity=carbon,
                    water_stress=stress,
                    renewable_fraction=renewable,
                    deadline=deadline,
                    window_start=t,
                    job_duration_hours=wall_hours,
                    max_carbon=self._settings.constraints.max_carbon_intensity,
                    max_water_stress=self._settings.constraints.max_water_stress,
                    min_renewable=self._settings.constraints.min_renewable_fraction,
                    drought_alert=water.drought_alert,
                )

                if not hard_check.is_feasible:
                    # Still record as infeasible candidate for reporting
                    sb = ScoreBreakdown(
                        region=region_name,
                        window_start=t,
                        window_end=window_end,
                        carbon_intensity=carbon,
                        water_stress=stress,
                        renewable_fraction=renewable,
                        feasible=False,
                        infeasibility_reason=hard_check.hard_violations[0].message,
                        total_score=INFEASIBLE,
                    )
                    all_candidates.append(sb)
                    t += step
                    continue

                # ── Compute score with priority-adjusted urgency ───────────────
                dp = deadline_pressure(
                    window_start=t,
                    window_end=window_end,
                    deadline=deadline,
                    job_duration_hours=wall_hours,
                    urgency_factor=urgency,
                )
                if dp >= 1.0:
                    t += step
                    continue

                score = self._scorer.score(
                    region=region_name,
                    window_start=t,
                    window_end=window_end,
                    carbon_intensity=carbon,
                    water_stress=stress,
                    renewable_fraction=renewable,
                    deadline=deadline,
                    job_duration_hours=wall_hours,
                    community_score=e["community_score"],
                    urgency_factor=urgency,
                )

                # Attach soft constraint warnings
                score.soft_warnings = [
                    v.message for v in check_soft_constraints(
                        carbon_intensity=carbon,
                        renewable_fraction=renewable,
                        water_stress=stress,
                    )
                ]

                all_candidates.append(score)
                t += step

        if not all_candidates:
            return SchedulingResult(job_id=request.job_id, best=None, all_candidates=[])

        all_candidates.sort(key=lambda s: (not s.feasible, s.total_score))
        best = all_candidates[0] if all_candidates[0].feasible else None

        # ── Compute environmental savings ──────────────────────────────────────
        feasible = [c for c in all_candidates if c.feasible]
        carbon_saved_pct = water_saved_pct = 0.0
        carbon_emitted = naive_carbon = carbon_saved_gco2 = 0.0

        if best:
            # Actual emissions: power (kW) × wall-clock hours × carbon intensity (gCO₂/kWh)
            carbon_emitted = power_kw * wall_hours * best.carbon_intensity

            if naive_baseline:
                naive_carbon = power_kw * wall_hours * naive_baseline.carbon_intensity
                carbon_saved_gco2 = max(naive_carbon - carbon_emitted, 0.0)
                if naive_carbon > 0:
                    carbon_saved_pct = carbon_saved_gco2 / naive_carbon * 100

            if len(feasible) > 1:
                worst_water = max(c.water_stress for c in feasible)
                if worst_water > 0:
                    water_saved_pct = (worst_water - best.water_stress) / worst_water * 100

        result = SchedulingResult(
            job_id=request.job_id,
            best=best,
            all_candidates=all_candidates,
            naive_baseline=naive_baseline,
            carbon_saved_pct=round(carbon_saved_pct, 1),
            water_saved_pct=round(water_saved_pct, 1),
            carbon_saved_gco2=round(carbon_saved_gco2, 1),
            carbon_emitted_gco2=round(carbon_emitted, 1),
            naive_carbon_gco2=round(naive_carbon, 1),
        )
        logger.info("Result: %s", result.explanation())
        return result

    # ── Naive baseline ──────────────────────────────────────────────────────

    def _compute_naive_baseline(
        self,
        regions: List[str],
        env_cache: dict,
        now: datetime,
        wall_hours: float,
        power_kw: float,
    ) -> Optional[NaiveBaseline]:
        """
        Naive baseline = immediate execution in the highest-carbon available region.
        Represents what would happen without green scheduling.
        """
        worst = None
        worst_carbon = -1.0

        for region_name in regions:
            e = env_cache[region_name]
            window_end = now + timedelta(hours=wall_hours)
            carbon = self._average_over_window(e["carbon_map"], now, window_end, 300.0)
            renewable = self._average_over_window(e["renewable_map"], now, window_end, 0.3)

            if carbon > worst_carbon:
                worst_carbon = carbon
                worst = NaiveBaseline(
                    region=region_name,
                    carbon_intensity=carbon,
                    carbon_gco2=power_kw * wall_hours * carbon,
                    renewable_fraction=renewable,
                    water_stress=e["water"].stress_index,
                )
        return worst

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _average_over_window(
        time_map: dict, start: datetime, end: datetime, default: float
    ) -> float:
        values = [v for ts, v in time_map.items() if start <= ts < end]
        return sum(values) / len(values) if values else default
