"""
api/routes.py
REST API endpoints for GreenScheduler.

Endpoints
---------
POST   /api/v1/jobs                       Submit a new workload
GET    /api/v1/jobs                        List all jobs
GET    /api/v1/jobs/{job_id}               Get a specific job
DELETE /api/v1/jobs/{job_id}               Cancel a job
POST   /api/v1/jobs/{job_id}/start         Mark job as running
POST   /api/v1/jobs/{job_id}/complete      Mark job as completed
GET    /api/v1/schedule/{job_id}           Get scheduling recommendation
GET    /api/v1/environment/{region}        Current environmental signals
GET    /api/v1/environment/compare         Compare all regions side-by-side
GET    /api/v1/impact                      Cumulative environmental impact
GET    /api/v1/budget                      Carbon budget status
POST   /api/v1/budget                      Set monthly carbon budget
GET    /api/v1/health                      Health check
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config.loader import get_settings
from scheduler.engine import SchedulingEngine, SchedulingResult
from workloads.budget import carbon_budget
from workloads.job import JobRequest, JobStatus, Priority, ScheduledJob
from workloads.queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Response models ───────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    job_id: str
    name: str
    status: str
    priority: str
    gpu_hours: float
    num_gpus: int
    wall_clock_hours: float
    deadline: Optional[datetime]
    assigned_region: Optional[str]
    scheduled_start: Optional[datetime]
    scheduled_end: Optional[datetime]
    score: Optional[float]
    explanation: str
    carbon_emitted_gco2: Optional[float]
    naive_carbon_gco2: Optional[float]
    carbon_saved_gco2: Optional[float]
    renewable_fraction: Optional[float]
    submitted_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    @classmethod
    def from_scheduled_job(cls, j: ScheduledJob) -> "JobResponse":
        return cls(
            job_id=j.request.job_id,
            name=j.request.name,
            status=j.status.value,
            priority=j.request.priority.value,
            gpu_hours=j.request.gpu_hours,
            num_gpus=j.request.num_gpus,
            wall_clock_hours=j.request.wall_clock_hours,
            deadline=j.request.deadline,
            assigned_region=j.assigned_region,
            scheduled_start=j.scheduled_start,
            scheduled_end=j.scheduled_end,
            score=j.score,
            explanation=j.explanation,
            carbon_emitted_gco2=j.carbon_emitted_gco2,
            naive_carbon_gco2=j.naive_carbon_gco2,
            carbon_saved_gco2=j.carbon_saved_gco2,
            renewable_fraction=j.renewable_fraction,
            submitted_at=j.request.submitted_at,
            started_at=j.started_at,
            completed_at=j.completed_at,
        )


class ScheduleResponse(BaseModel):
    job_id: str
    feasible: bool
    region: Optional[str]
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    score: Optional[float]
    carbon_intensity: Optional[float]
    renewable_fraction: Optional[float]
    water_stress: Optional[float]
    carbon_emitted_gco2: float
    naive_carbon_gco2: float
    carbon_saved_gco2: float
    carbon_saved_pct: float
    water_saved_pct: float
    explanation: str
    top_alternatives: List[Dict[str, Any]]
    soft_warnings: List[str]


class EnvironmentResponse(BaseModel):
    region: str
    grid_zone: str
    timestamp: datetime
    carbon_intensity_gco2_kwh: float
    renewable_fraction: float
    water_stress: float
    drought_alert: bool
    community_score: float


class ImpactResponse(BaseModel):
    total_emitted_gco2: float
    total_saved_gco2: float
    total_emitted_kgco2: float
    total_saved_kgco2: float
    jobs_scheduled: int
    avg_saving_per_job_gco2: float
    saving_rate_pct: float
    equivalent_trees_planted: float     # 1 tree ≈ 21 kgCO₂/year absorbed
    equivalent_car_km_avoided: float    # avg car ≈ 120 gCO₂/km


class BudgetStatusResponse(BaseModel):
    period: str
    ceiling_gco2: float
    ceiling_kgco2: float
    spent_gco2: float
    spent_kgco2: float
    remaining_gco2: float
    remaining_kgco2: float
    utilisation_pct: float
    is_exhausted: bool
    pending_reservations_gco2: float


class SetBudgetRequest(BaseModel):
    monthly_ceiling_kgco2: float = Field(..., gt=0, description="Monthly budget in kgCO₂")


class HealthResponse(BaseModel):
    status: str
    version: str
    jobs_in_queue: int
    budget_utilisation_pct: float


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_engine(request: Request) -> SchedulingEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        engine = SchedulingEngine.from_settings()
        request.app.state.engine = engine
    return engine


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    summary = carbon_budget.current_period_summary()
    return HealthResponse(
        status="ok",
        version="2.0.0",
        jobs_in_queue=len(job_queue),
        budget_utilisation_pct=summary["utilisation_pct"],
    )


# ── Jobs CRUD + Lifecycle ──────────────────────────────────────────────────────

@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def submit_job(payload: JobRequest, request: Request):
    """Submit a new AI workload for scheduling."""
    engine = _get_engine(request)
    result: SchedulingResult = engine.schedule(payload)

    scheduled = ScheduledJob(request=payload)

    if result.is_feasible:
        b = result.best

        # Carbon budget check
        allowed, budget_msg = carbon_budget.check_and_reserve(
            payload.job_id, result.carbon_emitted_gco2
        )
        if not allowed:
            raise HTTPException(status_code=429, detail=budget_msg)

        scheduled.mark_scheduled(
            region=b.region,
            start=b.window_start,
            end=b.window_end,
            score=b.total_score,
            explanation=result.explanation(),
            carbon_emitted=result.carbon_emitted_gco2,
            naive_carbon=result.naive_carbon_gco2,
            renewable_fraction=b.renewable_fraction,
        )
    else:
        scheduled.status = JobStatus.DEFERRED
        scheduled.explanation = result.explanation()

    job_queue.enqueue(scheduled)
    logger.info("Job %s → %s", payload.job_id, scheduled.status.value)
    return JobResponse.from_scheduled_job(scheduled)


@router.get("/jobs", response_model=List[JobResponse])
async def list_jobs(status_filter: Optional[str] = None):
    """List all jobs, optionally filtered by status."""
    jobs = job_queue.list_all()
    if status_filter:
        jobs = [j for j in jobs if j.status.value == status_filter]
    return [JobResponse.from_scheduled_job(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobResponse.from_scheduled_job(job)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: str):
    removed = job_queue.remove(job_id)
    if removed is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    carbon_budget.release(job_id)


@router.post("/jobs/{job_id}/start", response_model=JobResponse)
async def start_job(job_id: str):
    """Mark a scheduled job as running."""
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != JobStatus.SCHEDULED:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in SCHEDULED state to start. Current: {job.status.value}",
        )
    job.mark_running()
    job_queue.update(job)
    return JobResponse.from_scheduled_job(job)


@router.post("/jobs/{job_id}/complete", response_model=JobResponse)
async def complete_job(job_id: str):
    """Mark a running job as completed and commit its carbon spend."""
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != JobStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in RUNNING state to complete. Current: {job.status.value}",
        )
    job.mark_completed()
    job_queue.update(job)
    # Commit actual carbon spend to budget tracker
    carbon_budget.commit(
        job_id=job_id,
        actual_gco2=job.carbon_emitted_gco2 or 0.0,
        saved_gco2=job.carbon_saved_gco2 or 0.0,
    )
    return JobResponse.from_scheduled_job(job)


# ── Schedule ───────────────────────────────────────────────────────────────────

@router.get("/schedule/{job_id}", response_model=ScheduleResponse)
async def get_schedule(job_id: str, request: Request):
    """Re-run scheduling optimisation and return full recommendation."""
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    engine = _get_engine(request)
    result = engine.schedule(job.request)

    feasible_candidates = [c for c in result.all_candidates if c.feasible]
    top_alternatives = []
    for candidate in feasible_candidates[1:6]:
        top_alternatives.append({
            "region": candidate.region,
            "window_start": candidate.window_start.isoformat(),
            "score": round(candidate.total_score, 4),
            "carbon_intensity": round(candidate.carbon_intensity, 1),
            "renewable_fraction": round(candidate.renewable_fraction, 3),
            "water_stress": round(candidate.water_stress, 3),
        })

    b = result.best
    soft_warnings = b.soft_warnings if b else []

    return ScheduleResponse(
        job_id=job_id,
        feasible=result.is_feasible,
        region=b.region if b else None,
        window_start=b.window_start if b else None,
        window_end=b.window_end if b else None,
        score=round(b.total_score, 4) if b else None,
        carbon_intensity=round(b.carbon_intensity, 1) if b else None,
        renewable_fraction=round(b.renewable_fraction, 3) if b else None,
        water_stress=round(b.water_stress, 3) if b else None,
        carbon_emitted_gco2=round(result.carbon_emitted_gco2, 1),
        naive_carbon_gco2=round(result.naive_carbon_gco2, 1),
        carbon_saved_gco2=round(result.carbon_saved_gco2, 1),
        carbon_saved_pct=round(result.carbon_saved_pct, 1),
        water_saved_pct=round(result.water_saved_pct, 1),
        explanation=result.explanation(),
        top_alternatives=top_alternatives,
        soft_warnings=soft_warnings,
    )


# ── Environment ────────────────────────────────────────────────────────────────

@router.get("/environment/compare")
async def compare_regions(request: Request):
    """Return current environmental signals for all configured regions side-by-side."""
    settings = get_settings()
    engine = _get_engine(request)
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=1)

    comparison = []
    for region_name, cfg in settings.regions.items():
        carbon = engine._carbon.get_intensity_at(cfg.grid_zone, now, window_end)
        renewable = engine._renewable.get_fraction_at(cfg.grid_zone, now, window_end)
        water = engine._water.get_stress(cfg.water_basin)
        comparison.append({
            "region": region_name,
            "grid_zone": cfg.grid_zone,
            "carbon_intensity_gco2_kwh": round(carbon, 1),
            "renewable_fraction": round(renewable, 3),
            "water_stress": round(water.stress_index, 3),
            "drought_alert": water.drought_alert,
            "community_score": cfg.community_score,
            "green_score": round(
                (1 - carbon / 600) * 0.5 + renewable * 0.3 + (1 - water.stress_index) * 0.2,
                3,
            ),
        })

    comparison.sort(key=lambda x: x["green_score"], reverse=True)
    return {"timestamp": now.isoformat(), "regions": comparison}


@router.get("/environment/{region}", response_model=EnvironmentResponse)
async def get_environment(region: str, request: Request):
    settings = get_settings()
    region_cfg = settings.regions.get(region)
    if region_cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Region '{region}' not found. Available: {list(settings.regions.keys())}",
        )

    engine = _get_engine(request)
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=1)

    carbon = engine._carbon.get_intensity_at(region_cfg.grid_zone, now, window_end)
    renewable = engine._renewable.get_fraction_at(region_cfg.grid_zone, now, window_end)
    water = engine._water.get_stress(region_cfg.water_basin)

    return EnvironmentResponse(
        region=region,
        grid_zone=region_cfg.grid_zone,
        timestamp=now,
        carbon_intensity_gco2_kwh=round(carbon, 1),
        renewable_fraction=round(renewable, 3),
        water_stress=round(water.stress_index, 3),
        drought_alert=water.drought_alert,
        community_score=region_cfg.community_score,
    )


# ── Impact ─────────────────────────────────────────────────────────────────────

@router.get("/impact", response_model=ImpactResponse)
async def get_impact():
    """Cumulative environmental impact of all scheduled jobs."""
    summary = carbon_budget.lifetime_summary()
    total_saved = summary["total_saved_gco2"]
    total_emitted = summary["total_emitted_gco2"]

    return ImpactResponse(
        total_emitted_gco2=total_emitted,
        total_saved_gco2=total_saved,
        total_emitted_kgco2=round(total_emitted / 1000, 3),
        total_saved_kgco2=round(total_saved / 1000, 3),
        jobs_scheduled=summary["jobs_scheduled"],
        avg_saving_per_job_gco2=summary["avg_saving_per_job_gco2"],
        saving_rate_pct=summary["saving_rate_pct"],
        # 1 mature tree absorbs ~21 kgCO₂/year → per-second: 21000 / (365*24*3600)
        equivalent_trees_planted=round(total_saved / 21000, 2),
        # Average car emits ~120 gCO₂/km
        equivalent_car_km_avoided=round(total_saved / 120, 1),
    )


# ── Budget ─────────────────────────────────────────────────────────────────────

@router.get("/budget", response_model=BudgetStatusResponse)
async def get_budget():
    """Current carbon budget status."""
    s = carbon_budget.current_period_summary()
    return BudgetStatusResponse(
        period=s["period"],
        ceiling_gco2=s["ceiling_gco2"],
        ceiling_kgco2=round(s["ceiling_gco2"] / 1000, 2),
        spent_gco2=s["spent_gco2"],
        spent_kgco2=round(s["spent_gco2"] / 1000, 2),
        remaining_gco2=s["remaining_gco2"],
        remaining_kgco2=round(s["remaining_gco2"] / 1000, 2),
        utilisation_pct=s["utilisation_pct"],
        is_exhausted=s["is_exhausted"],
        pending_reservations_gco2=s["pending_reservations"],
    )


@router.post("/budget", status_code=status.HTTP_200_OK)
async def set_budget(payload: SetBudgetRequest):
    """Set or update the monthly carbon budget."""
    carbon_budget._ceiling = payload.monthly_ceiling_kgco2 * 1000
    return {
        "message": f"Monthly carbon budget set to {payload.monthly_ceiling_kgco2} kgCO₂",
        "ceiling_gco2": carbon_budget._ceiling,
    }
