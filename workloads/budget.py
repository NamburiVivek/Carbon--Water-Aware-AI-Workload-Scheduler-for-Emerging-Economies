"""
workloads/budget.py
Carbon budget tracker.

Organisations can set a monthly gCO₂ budget.  The scheduler checks this
before accepting a job — if the budget is exhausted the job is rejected
with a clear explanation.

Budget is stored in-memory (resets on restart).  For persistence,
swap _store with a database-backed implementation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class BudgetPeriod:
    """Tracks carbon spend against a ceiling for a named period."""

    name: str                       # e.g. "2026-08"
    ceiling_gco2: float             # total budget in gCO₂
    spent_gco2: float = 0.0
    job_count: int = 0

    @property
    def remaining_gco2(self) -> float:
        return max(self.ceiling_gco2 - self.spent_gco2, 0.0)

    @property
    def utilisation_pct(self) -> float:
        if self.ceiling_gco2 <= 0:
            return 0.0
        return min(self.spent_gco2 / self.ceiling_gco2 * 100, 100.0)

    @property
    def is_exhausted(self) -> bool:
        return self.spent_gco2 >= self.ceiling_gco2


class CarbonBudgetTracker:
    """
    Thread-safe carbon budget manager.

    Usage
    -----
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=500_000)
    ok, msg = tracker.check_and_reserve("job-123", estimated_gco2=1200)
    if ok:
        # proceed with scheduling
        tracker.commit("job-123")
    """

    def __init__(self, monthly_ceiling_gco2: float = 0.0) -> None:
        """
        Parameters
        ----------
        monthly_ceiling_gco2 : float
            Monthly carbon budget in gCO₂.  0 = unlimited.
        """
        self._ceiling = monthly_ceiling_gco2
        self._lock = threading.RLock()
        self._periods: Dict[str, BudgetPeriod] = {}
        self._reservations: Dict[str, float] = {}   # job_id → reserved gCO₂
        self._total_saved: float = 0.0
        self._total_emitted: float = 0.0
        self._jobs_scheduled: int = 0

    # ── Current period ────────────────────────────────────────────────────

    def _period_key(self) -> str:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m")

    def _get_or_create_period(self) -> BudgetPeriod:
        key = self._period_key()
        if key not in self._periods:
            self._periods[key] = BudgetPeriod(
                name=key,
                ceiling_gco2=self._ceiling,
            )
        return self._periods[key]

    # ── Budget operations ─────────────────────────────────────────────────

    def check_and_reserve(
        self, job_id: str, estimated_gco2: float
    ) -> tuple[bool, str]:
        """
        Check if budget allows the job and place a soft reservation.
        Returns (allowed, reason_message).
        """
        with self._lock:
            if self._ceiling <= 0:
                return True, "No budget limit configured."

            period = self._get_or_create_period()
            # Include existing reservations
            pending = sum(self._reservations.values())
            projected_total = period.spent_gco2 + pending + estimated_gco2

            if projected_total > period.ceiling_gco2:
                return False, (
                    f"Carbon budget exhausted for {period.name}. "
                    f"Budget: {period.ceiling_gco2/1000:.1f} kgCO₂, "
                    f"Spent: {period.spent_gco2/1000:.1f} kgCO₂, "
                    f"Remaining: {period.remaining_gco2/1000:.1f} kgCO₂, "
                    f"Requested: {estimated_gco2/1000:.1f} kgCO₂."
                )

            self._reservations[job_id] = estimated_gco2
            return True, (
                f"Budget OK. Remaining after reservation: "
                f"{(period.remaining_gco2 - estimated_gco2)/1000:.1f} kgCO₂."
            )

    def commit(self, job_id: str, actual_gco2: float, saved_gco2: float = 0.0) -> None:
        """
        Commit actual emissions when a job completes.
        Releases the reservation and records final spend.
        """
        with self._lock:
            self._reservations.pop(job_id, None)
            period = self._get_or_create_period()
            period.spent_gco2 += actual_gco2
            period.job_count += 1
            self._total_emitted += actual_gco2
            self._total_saved += saved_gco2
            self._jobs_scheduled += 1

    def release(self, job_id: str) -> None:
        """Release a reservation without committing (e.g. job cancelled)."""
        with self._lock:
            self._reservations.pop(job_id, None)

    # ── Reporting ─────────────────────────────────────────────────────────

    def current_period_summary(self) -> dict:
        with self._lock:
            period = self._get_or_create_period()
            return {
                "period": period.name,
                "ceiling_gco2": period.ceiling_gco2,
                "spent_gco2": round(period.spent_gco2, 1),
                "remaining_gco2": round(period.remaining_gco2, 1),
                "utilisation_pct": round(period.utilisation_pct, 1),
                "is_exhausted": period.is_exhausted,
                "pending_reservations": round(sum(self._reservations.values()), 1),
            }

    def lifetime_summary(self) -> dict:
        with self._lock:
            return {
                "total_emitted_gco2": round(self._total_emitted, 1),
                "total_saved_gco2": round(self._total_saved, 1),
                "jobs_scheduled": self._jobs_scheduled,
                "avg_saving_per_job_gco2": (
                    round(self._total_saved / self._jobs_scheduled, 1)
                    if self._jobs_scheduled > 0 else 0.0
                ),
                "saving_rate_pct": (
                    round(
                        self._total_saved / (self._total_saved + self._total_emitted) * 100, 1
                    )
                    if (self._total_saved + self._total_emitted) > 0 else 0.0
                ),
            }


# Singleton — shared across the application
carbon_budget = CarbonBudgetTracker(monthly_ceiling_gco2=0.0)   # 0 = unlimited by default
