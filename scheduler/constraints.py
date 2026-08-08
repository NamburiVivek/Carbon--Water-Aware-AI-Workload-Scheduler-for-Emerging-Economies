"""
scheduler/constraints.py
Hard and soft constraint evaluation for GreenScheduler.

Hard constraints disqualify a candidate outright.
Soft constraints produce a penalty that feeds into the score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class ConstraintViolation:
    name: str
    message: str
    hard: bool = True  # True = disqualify; False = soft penalty


@dataclass
class ConstraintResult:
    violations: List[ConstraintViolation] = field(default_factory=list)

    @property
    def is_feasible(self) -> bool:
        return not any(v.hard for v in self.violations)

    @property
    def hard_violations(self) -> List[ConstraintViolation]:
        return [v for v in self.violations if v.hard]

    @property
    def soft_violations(self) -> List[ConstraintViolation]:
        return [v for v in self.violations if not v.hard]


def check_hard_constraints(
    carbon_intensity: float,
    water_stress: float,
    renewable_fraction: float,
    deadline: Optional[datetime],
    window_start: datetime,
    job_duration_hours: float,
    max_carbon: float = 400.0,
    max_water_stress: float = 0.8,
    min_renewable: float = 0.0,
    drought_alert: bool = False,
) -> ConstraintResult:
    """
    Evaluate all hard constraints for a candidate window.
    Returns a ConstraintResult; check .is_feasible before scheduling.
    """
    result = ConstraintResult()

    # 1. Carbon intensity ceiling
    if carbon_intensity > max_carbon:
        result.violations.append(
            ConstraintViolation(
                name="carbon_ceiling",
                message=(
                    f"Carbon intensity {carbon_intensity:.0f} gCO₂/kWh exceeds "
                    f"hard cap of {max_carbon:.0f} gCO₂/kWh"
                ),
            )
        )

    # 2. Water stress ceiling
    if water_stress > max_water_stress:
        result.violations.append(
            ConstraintViolation(
                name="water_stress_ceiling",
                message=(
                    f"Water stress {water_stress:.2f} exceeds hard limit of "
                    f"{max_water_stress:.2f}"
                ),
            )
        )

    # 3. Active drought alert overrides
    if drought_alert and water_stress > 0.6:
        result.violations.append(
            ConstraintViolation(
                name="drought_alert",
                message=(
                    "Active drought alert: workload execution in this region is "
                    "suspended until stress subsides below 0.6"
                ),
            )
        )

    # 4. Minimum renewable fraction
    if renewable_fraction < min_renewable:
        result.violations.append(
            ConstraintViolation(
                name="min_renewable",
                message=(
                    f"Renewable fraction {renewable_fraction:.1%} is below "
                    f"configured minimum {min_renewable:.1%}"
                ),
            )
        )

    # 5. Deadline feasibility
    if deadline is not None:
        from datetime import timedelta
        finish = window_start + timedelta(hours=job_duration_hours)
        if finish > deadline:
            result.violations.append(
                ConstraintViolation(
                    name="deadline_miss",
                    message=(
                        f"Job would finish at {finish.isoformat()} "
                        f"which is after deadline {deadline.isoformat()}"
                    ),
                )
            )

    return result


def check_soft_constraints(
    carbon_intensity: float,
    renewable_fraction: float,
    water_stress: float,
    preferred_renewable_min: float = 0.60,
    preferred_water_max: float = 0.50,
    preferred_carbon_max: float = 250.0,
) -> List[ConstraintViolation]:
    """
    Evaluate soft constraints and return advisory violations.
    These do not disqualify a window but are logged and fed into reporting.
    """
    violations = []

    if renewable_fraction < preferred_renewable_min:
        violations.append(
            ConstraintViolation(
                name="low_renewable_soft",
                message=(
                    f"Renewable fraction {renewable_fraction:.1%} is below "
                    f"preferred {preferred_renewable_min:.1%}"
                ),
                hard=False,
            )
        )

    if water_stress > preferred_water_max:
        violations.append(
            ConstraintViolation(
                name="high_water_soft",
                message=(
                    f"Water stress {water_stress:.2f} is above preferred "
                    f"maximum {preferred_water_max:.2f}"
                ),
                hard=False,
            )
        )

    if carbon_intensity > preferred_carbon_max:
        violations.append(
            ConstraintViolation(
                name="high_carbon_soft",
                message=(
                    f"Carbon intensity {carbon_intensity:.0f} gCO₂/kWh exceeds "
                    f"preferred maximum {preferred_carbon_max:.0f} gCO₂/kWh"
                ),
                hard=False,
            )
        )

    return violations
