"""
scheduler/scorer.py
Unified multi-objective scoring function for GreenScheduler.

Score(r, t) = w_c · NormCarbon(r,t)
            + w_w · NormWater(r,t)
            − w_r · RenewableFraction(r,t)
            + w_d · DeadlinePressure(t, deadline, urgency)
            − w_p · CommunityBenefit(r)

Lower score  → better candidate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from config.loader import Settings
from workloads.deadline import deadline_pressure

logger = logging.getLogger(__name__)

INFEASIBLE = float("inf")


@dataclass
class ScoreBreakdown:
    """Detailed scoring result for a single candidate window."""

    region: str
    window_start: datetime
    window_end: datetime

    # Raw environmental signals
    carbon_intensity: float = 0.0
    water_stress: float = 0.0
    renewable_fraction: float = 0.0
    deadline_pressure: float = 0.0
    community_score: float = 0.0

    # Weighted contributions
    carbon_contribution: float = 0.0
    water_contribution: float = 0.0
    renewable_contribution: float = 0.0
    deadline_contribution: float = 0.0
    community_contribution: float = 0.0

    total_score: float = 0.0
    feasible: bool = True
    infeasibility_reason: str = ""
    soft_warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.feasible:
            return (
                f"[INFEASIBLE] {self.region} @ {self.window_start.isoformat()}: "
                f"{self.infeasibility_reason}"
            )
        return (
            f"{self.region} @ {self.window_start.strftime('%Y-%m-%d %H:%M UTC')} | "
            f"score={self.total_score:.3f} | "
            f"carbon={self.carbon_intensity:.0f} gCO₂/kWh | "
            f"renewable={self.renewable_fraction:.1%} | "
            f"water={self.water_stress:.2f} | "
            f"deadline_pressure={self.deadline_pressure:.2f}"
        )


class Scorer:
    def __init__(self, settings: Settings, carbon_max_global: float = 600.0) -> None:
        self._w = settings.weights
        self._c = settings.constraints
        self._carbon_max = carbon_max_global

    def score(
        self,
        region: str,
        window_start: datetime,
        window_end: datetime,
        carbon_intensity: float,
        water_stress: float,
        renewable_fraction: float,
        deadline: Optional[datetime],
        job_duration_hours: float,
        community_score: float,
        urgency_factor: float = 2.0,
    ) -> ScoreBreakdown:
        result = ScoreBreakdown(
            region=region,
            window_start=window_start,
            window_end=window_end,
            carbon_intensity=carbon_intensity,
            water_stress=water_stress,
            renewable_fraction=renewable_fraction,
            community_score=community_score,
        )

        # Hard constraints are checked upstream (engine.py) before scoring.
        # Scorer only needs to handle deadline pressure here.
        dp = deadline_pressure(
            window_start=window_start,
            window_end=window_end,
            deadline=deadline,
            job_duration_hours=job_duration_hours,
            urgency_factor=urgency_factor,
        )
        if dp >= 1.0:
            result.feasible = False
            result.infeasibility_reason = "window would miss deadline"
            result.total_score = INFEASIBLE
            return result

        result.deadline_pressure = dp

        # Normalised contributions
        norm_carbon = min(carbon_intensity / max(self._carbon_max, 1.0), 1.0)
        norm_water = min(water_stress, 1.0)
        norm_renewable = renewable_fraction
        norm_deadline = dp
        norm_community = 1.0 - min(community_score, 1.0)

        result.carbon_contribution = self._w.carbon * norm_carbon
        result.water_contribution = self._w.water * norm_water
        result.renewable_contribution = -self._w.renewable * norm_renewable
        result.deadline_contribution = self._w.deadline * norm_deadline
        result.community_contribution = -self._w.community * norm_community

        result.total_score = (
            result.carbon_contribution
            + result.water_contribution
            + result.renewable_contribution
            + result.deadline_contribution
            + result.community_contribution
        )

        return result
