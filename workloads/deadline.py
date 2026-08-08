"""
workloads/deadline.py
Computes deadline pressure — a 0–1 score that rises as the window
approaches the job's deadline.  Jobs without deadlines return 0.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional


def deadline_pressure(
    window_start: datetime,
    window_end: datetime,
    deadline: Optional[datetime],
    job_duration_hours: float,
    urgency_factor: float = 2.0,
) -> float:
    """
    Returns a [0, 1] pressure score.

    Parameters
    ----------
    window_start : datetime
        Start of the candidate execution window (UTC).
    window_end : datetime
        End of the candidate execution window (UTC).
    deadline : datetime | None
        Hard deadline for the job.  None → no pressure (returns 0).
    job_duration_hours : float
        Estimated runtime of the job in hours.
    urgency_factor : float
        Controls how steeply pressure rises.  Higher = more aggressive deferral.

    Notes
    -----
    Pressure is computed as:

        slack = deadline - (window_start + job_duration)  [hours]
        pressure = 1 - sigmoid(urgency_factor * slack / total_window)

    When slack ≤ 0 (window would miss the deadline) we return 1.0 (infeasible).
    """
    if deadline is None:
        return 0.0

    now = datetime.now(timezone.utc)
    job_delta = timedelta(hours=job_duration_hours)
    finish_time = window_start + job_delta

    # Hard infeasibility
    if finish_time > deadline:
        return 1.0

    total_slack_hours = max((deadline - now).total_seconds() / 3600.0, 1e-6)
    remaining_slack_hours = max((deadline - finish_time).total_seconds() / 3600.0, 0.0)

    # Normalised slack: 1 = plenty of time, 0 = about to miss
    normalised_slack = remaining_slack_hours / total_slack_hours

    # Sigmoid-based pressure: rises sharply when normalised_slack < 0.3
    pressure = 1.0 / (1.0 + math.exp(urgency_factor * (normalised_slack - 0.5) * 6))
    return float(min(max(pressure, 0.0), 1.0))


def earliest_feasible_window(
    now: datetime,
    deadline: Optional[datetime],
    job_duration_hours: float,
    default_deadline_hours: int = 24,
) -> datetime:
    """
    Returns the latest start time that still satisfies the deadline.
    Used as the boundary for the scheduling search space.
    """
    if deadline is None:
        deadline = now + timedelta(hours=default_deadline_hours)
    latest_start = deadline - timedelta(hours=job_duration_hours)
    return max(now, latest_start)
