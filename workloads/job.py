"""
workloads/job.py
Pydantic models for AI workload jobs submitted to GreenScheduler.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Priority(str, Enum):
    CRITICAL = "critical"   # must run ASAP; deadline pressure maxed
    HIGH = "high"
    STANDARD = "standard"   # default
    LOW = "low"             # defer aggressively for greener windows
    BATCH = "batch"         # best-effort; no hard deadline


# Priority → urgency multiplier applied to deadline pressure
PRIORITY_URGENCY: Dict[str, float] = {
    "critical": 4.0,
    "high": 2.5,
    "standard": 2.0,
    "low": 0.8,
    "batch": 0.3,
}


class JobStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"


class JobRequest(BaseModel):
    """Payload accepted by POST /api/v1/jobs."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = ""
    gpu_hours: float = Field(..., gt=0, description="Estimated GPU-hours required")
    num_gpus: int = Field(1, ge=1, description="Number of GPUs; wall-clock = gpu_hours / num_gpus")
    gpu_tdp_watts: float = Field(300.0, gt=0, description="TDP per GPU in watts (default: A100 300W)")
    cpu_hours: float = Field(0.0, ge=0)
    memory_gb: float = Field(0.0, ge=0)
    deadline: Optional[datetime] = None
    priority: Priority = Priority.STANDARD
    regions: List[str] = Field(
        default_factory=list,
        description="Allowed regions; empty = any region",
    )
    tags: dict = Field(default_factory=dict)
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def wall_clock_hours(self) -> float:
        """Actual execution duration: gpu_hours / num_gpus."""
        return self.gpu_hours / max(self.num_gpus, 1)

    @property
    def total_power_kw(self) -> float:
        """Total GPU power draw in kW."""
        return (self.num_gpus * self.gpu_tdp_watts) / 1000.0

    @field_validator("deadline", mode="before")
    @classmethod
    def ensure_timezone(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("deadline", mode="after")
    @classmethod
    def deadline_in_future(cls, v):
        if v is None:
            return v
        now = datetime.now(timezone.utc)
        if v <= now:
            raise ValueError("deadline must be in the future")
        return v


class ScheduledJob(BaseModel):
    """A job enriched with the scheduler's decision."""

    request: JobRequest
    status: JobStatus = JobStatus.PENDING

    # Scheduler output
    assigned_region: Optional[str] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    score: Optional[float] = None
    explanation: str = ""

    # Environmental impact
    carbon_saved_gco2: Optional[float] = None      # gCO₂ saved vs naive scheduling
    carbon_emitted_gco2: Optional[float] = None    # actual gCO₂ emitted
    naive_carbon_gco2: Optional[float] = None      # what naive scheduling would have emitted
    water_saved_liters: Optional[float] = None
    renewable_fraction: Optional[float] = None

    # Lifecycle timestamps
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def mark_scheduled(
        self,
        region: str,
        start: datetime,
        end: datetime,
        score: float,
        explanation: str = "",
        carbon_emitted: float = 0.0,
        naive_carbon: float = 0.0,
        renewable_fraction: float = 0.0,
    ) -> None:
        self.assigned_region = region
        self.scheduled_start = start
        self.scheduled_end = end
        self.score = score
        self.explanation = explanation
        self.carbon_emitted_gco2 = round(carbon_emitted, 1)
        self.naive_carbon_gco2 = round(naive_carbon, 1)
        self.carbon_saved_gco2 = round(max(naive_carbon - carbon_emitted, 0.0), 1)
        self.renewable_fraction = renewable_fraction
        self.status = JobStatus.SCHEDULED
        self.updated_at = datetime.now(timezone.utc)

    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_completed(self) -> None:
        self.status = JobStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
