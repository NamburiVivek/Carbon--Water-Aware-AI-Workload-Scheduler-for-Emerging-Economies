"""
tests/test_budget.py
Unit tests for the carbon budget tracker.
"""

from __future__ import annotations

import pytest

from workloads.budget import CarbonBudgetTracker


def test_unlimited_budget_always_allows():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=0)
    ok, msg = tracker.check_and_reserve("job-1", 999999)
    assert ok


def test_budget_allows_within_ceiling():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=10000)
    ok, msg = tracker.check_and_reserve("job-1", 5000)
    assert ok


def test_budget_blocks_over_ceiling():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=1000)
    ok, msg = tracker.check_and_reserve("job-1", 2000)
    assert not ok
    assert "exhausted" in msg.lower()


def test_reservations_stack():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=1000)
    ok1, _ = tracker.check_and_reserve("job-1", 600)
    ok2, _ = tracker.check_and_reserve("job-2", 600)  # total 1200 > 1000
    assert ok1
    assert not ok2


def test_commit_records_spend():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=5000)
    tracker.check_and_reserve("job-1", 1000)
    tracker.commit("job-1", actual_gco2=900, saved_gco2=200)
    summary = tracker.lifetime_summary()
    assert summary["total_emitted_gco2"] == 900
    assert summary["total_saved_gco2"] == 200
    assert summary["jobs_scheduled"] == 1


def test_release_frees_reservation():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=1000)
    tracker.check_and_reserve("job-1", 800)
    tracker.release("job-1")
    ok, _ = tracker.check_and_reserve("job-2", 800)
    assert ok


def test_period_summary_fields():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=5000)
    summary = tracker.current_period_summary()
    assert "period" in summary
    assert "ceiling_gco2" in summary
    assert "remaining_gco2" in summary
    assert "utilisation_pct" in summary
    assert "is_exhausted" in summary


def test_saving_rate_calculation():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=50000)
    tracker.check_and_reserve("j1", 1000)
    tracker.commit("j1", actual_gco2=700, saved_gco2=300)
    summary = tracker.lifetime_summary()
    # saved / (saved + emitted) = 300 / 1000 = 30%
    assert abs(summary["saving_rate_pct"] - 30.0) < 0.1


def test_exhausted_flag():
    tracker = CarbonBudgetTracker(monthly_ceiling_gco2=500)
    tracker.check_and_reserve("j1", 400)
    tracker.commit("j1", actual_gco2=500)
    period = tracker.current_period_summary()
    assert period["is_exhausted"]
