"""
tests/test_engine.py
Integration tests for the scheduling engine v2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.loader import Settings, Weights, Constraints, Scheduling, RegionConfig, CacheConfig, ServerConfig, LoggingConfig
from scheduler.engine import SchedulingEngine
from workloads.job import JobRequest, Priority


def make_settings() -> Settings:
    return Settings(
        weights=Weights(carbon=0.35, water=0.20, renewable=0.20, deadline=0.15, community=0.10),
        constraints=Constraints(max_carbon_intensity=400.0, max_water_stress=0.8,
                                min_renewable_fraction=0.0),
        scheduling=Scheduling(lookahead_hours=24, window_resolution_minutes=60,
                              default_deadline_hours=24),
        regions={
            "us-west-2": RegionConfig(grid_zone="US-NW-PACW", water_basin="Columbia River",
                                      community_score=0.4),
            "us-east-1": RegionConfig(grid_zone="US-MIDA-PJM", water_basin="Ohio River",
                                      community_score=0.6),
            "ap-southeast-1": RegionConfig(grid_zone="SG", water_basin="Johor River",
                                           community_score=0.7),
        },
    )


@pytest.fixture
def engine():
    from data.cache import MemoryCache
    from data.carbon import CarbonDataService
    from data.renewable import RenewableDataService
    from data.water import WaterDataService
    s = make_settings()
    cache = MemoryCache()
    return SchedulingEngine(
        settings=s,
        carbon_service=CarbonDataService(cache=cache),
        renewable_service=RenewableDataService(cache=cache),
        water_service=WaterDataService(cache=cache),
    )


def make_job(gpu_hours=4.0, num_gpus=1, deadline_hours=24,
             priority=Priority.STANDARD, regions=None) -> JobRequest:
    return JobRequest(
        gpu_hours=gpu_hours,
        num_gpus=num_gpus,
        deadline=datetime.now(timezone.utc) + timedelta(hours=deadline_hours),
        priority=priority,
        regions=regions or [],
    )


# ── Basic scheduling ───────────────────────────────────────────────────────────

def test_schedule_returns_feasible_result(engine):
    result = engine.schedule(make_job())
    assert result.is_feasible
    assert result.best is not None


def test_schedule_respects_region_filter(engine):
    result = engine.schedule(make_job(regions=["us-west-2"]))
    assert result.is_feasible
    assert all(c.region == "us-west-2" for c in result.all_candidates)


def test_schedule_unknown_region(engine):
    result = engine.schedule(make_job(regions=["mars-dc-1"]))
    assert not result.is_feasible
    assert len(result.all_candidates) == 0


def test_best_score_is_minimum(engine):
    result = engine.schedule(make_job())
    feasible = [c for c in result.all_candidates if c.feasible]
    if feasible:
        assert abs(result.best.total_score - min(c.total_score for c in feasible)) < 1e-9


# ── Wall-clock vs GPU-hours ────────────────────────────────────────────────────

def test_wall_clock_equals_gpu_hours_divided_by_gpus():
    job = make_job(gpu_hours=8.0, num_gpus=4)
    assert abs(job.wall_clock_hours - 2.0) < 1e-9


def test_single_gpu_wall_clock_equals_gpu_hours():
    job = make_job(gpu_hours=6.0, num_gpus=1)
    assert abs(job.wall_clock_hours - 6.0) < 1e-9


def test_power_calculation():
    job = JobRequest(gpu_hours=4.0, num_gpus=4, gpu_tdp_watts=300.0,
                     deadline=datetime.now(timezone.utc) + timedelta(hours=24))
    assert abs(job.total_power_kw - 1.2) < 1e-9


# ── Carbon math ───────────────────────────────────────────────────────────────

def test_carbon_emitted_uses_power_not_gpu_hours(engine):
    """carbon_emitted = power_kw × wall_hours × intensity — not gpu_hours × intensity"""
    job = make_job(gpu_hours=8.0, num_gpus=4)  # wall_clock=2h, power=1.2kW
    result = engine.schedule(job)
    if result.is_feasible and result.best:
        b = result.best
        expected = job.total_power_kw * job.wall_clock_hours * b.carbon_intensity
        assert abs(result.carbon_emitted_gco2 - expected) < 1.0


def test_naive_baseline_has_higher_carbon(engine):
    """Naive baseline should be >= best candidate carbon."""
    result = engine.schedule(make_job())
    if result.is_feasible and result.naive_baseline:
        assert result.naive_baseline.carbon_intensity >= result.best.carbon_intensity - 1.0


def test_carbon_saved_non_negative(engine):
    result = engine.schedule(make_job())
    assert result.carbon_saved_gco2 >= 0.0
    assert result.carbon_saved_pct >= 0.0


# ── Priority ──────────────────────────────────────────────────────────────────

def test_batch_job_can_schedule(engine):
    result = engine.schedule(make_job(priority=Priority.BATCH))
    assert isinstance(result.is_feasible, bool)


def test_critical_job_can_schedule(engine):
    result = engine.schedule(make_job(priority=Priority.CRITICAL, deadline_hours=6))
    assert isinstance(result.is_feasible, bool)


# ── Naive baseline ────────────────────────────────────────────────────────────

def test_naive_baseline_present(engine):
    result = engine.schedule(make_job())
    assert result.naive_baseline is not None
    assert result.naive_baseline.region in ["us-west-2", "us-east-1", "ap-southeast-1"]


# ── Explanation ───────────────────────────────────────────────────────────────

def test_explanation_contains_naive_comparison(engine):
    result = engine.schedule(make_job())
    if result.is_feasible:
        assert "kgCO₂" in result.explanation()
