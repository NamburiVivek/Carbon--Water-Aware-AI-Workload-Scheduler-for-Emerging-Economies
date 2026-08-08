"""
tests/test_scorer.py
Unit tests for the unified scoring function.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.loader import Settings, Weights, Constraints
from scheduler.scorer import Scorer, INFEASIBLE


def make_scorer(
    carbon=0.35, water=0.20, renewable=0.20, deadline=0.15, community=0.10,
    max_carbon=400.0, max_water_stress=0.8, min_renewable=0.0,
) -> Scorer:
    settings = Settings(
        weights=Weights(carbon=carbon, water=water, renewable=renewable,
                        deadline=deadline, community=community),
        constraints=Constraints(max_carbon_intensity=max_carbon,
                                max_water_stress=max_water_stress,
                                min_renewable_fraction=min_renewable),
    )
    return Scorer(settings, carbon_max_global=600.0)


NOW = datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc)
DEADLINE = NOW + timedelta(hours=12)


def test_feasible_green_window():
    scorer = make_scorer()
    result = scorer.score(
        region="us-west-2", window_start=NOW, window_end=NOW + timedelta(hours=2),
        carbon_intensity=80.0, water_stress=0.20, renewable_fraction=0.75,
        deadline=DEADLINE, job_duration_hours=2.0, community_score=0.4,
    )
    assert result.feasible
    assert result.total_score < 0.5
    assert result.carbon_contribution > 0
    assert result.renewable_contribution < 0


def test_deadline_infeasibility():
    scorer = make_scorer()
    tight = NOW + timedelta(hours=1)
    result = scorer.score(
        region="us-east-1", window_start=NOW, window_end=NOW + timedelta(hours=5),
        carbon_intensity=200.0, water_stress=0.3, renewable_fraction=0.4,
        deadline=tight, job_duration_hours=5.0, community_score=0.5,
    )
    assert not result.feasible
    assert "deadline" in result.infeasibility_reason


def test_greener_window_scores_better():
    scorer = make_scorer()
    common = dict(window_start=NOW, window_end=NOW + timedelta(hours=4),
                  water_stress=0.3, deadline=DEADLINE, job_duration_hours=4.0,
                  community_score=0.5)
    green = scorer.score(region="us-west-2", carbon_intensity=100.0,
                         renewable_fraction=0.80, **common)
    dirty = scorer.score(region="sg", carbon_intensity=380.0,
                         renewable_fraction=0.15, **common)
    assert green.feasible and dirty.feasible
    assert green.total_score < dirty.total_score


def test_no_deadline_zero_pressure():
    scorer = make_scorer()
    result = scorer.score(
        region="eu-west-1", window_start=NOW, window_end=NOW + timedelta(hours=2),
        carbon_intensity=200.0, water_stress=0.2, renewable_fraction=0.5,
        deadline=None, job_duration_hours=2.0, community_score=0.5,
    )
    assert result.feasible
    assert result.deadline_pressure == 0.0


def test_priority_urgency_high_scores_more_pressure():
    """Higher urgency factor should produce higher deadline pressure near the deadline."""
    scorer = make_scorer()
    near_deadline = DEADLINE - timedelta(hours=3)
    low_urgency = scorer.score(
        region="us-west-2", window_start=near_deadline,
        window_end=near_deadline + timedelta(hours=2),
        carbon_intensity=100.0, water_stress=0.2, renewable_fraction=0.7,
        deadline=DEADLINE, job_duration_hours=2.0, community_score=0.4,
        urgency_factor=0.5,
    )
    high_urgency = scorer.score(
        region="us-west-2", window_start=near_deadline,
        window_end=near_deadline + timedelta(hours=2),
        carbon_intensity=100.0, water_stress=0.2, renewable_fraction=0.7,
        deadline=DEADLINE, job_duration_hours=2.0, community_score=0.4,
        urgency_factor=4.0,
    )
    # Both feasible; high urgency should have higher total score (worse)
    assert low_urgency.feasible and high_urgency.feasible
    assert high_urgency.deadline_pressure >= low_urgency.deadline_pressure


def test_soft_warnings_field_exists():
    scorer = make_scorer()
    result = scorer.score(
        region="us-east-1", window_start=NOW, window_end=NOW + timedelta(hours=2),
        carbon_intensity=200.0, water_stress=0.2, renewable_fraction=0.5,
        deadline=DEADLINE, job_duration_hours=2.0, community_score=0.5,
    )
    assert hasattr(result, "soft_warnings")
    assert isinstance(result.soft_warnings, list)


def test_summary_output():
    scorer = make_scorer()
    result = scorer.score(
        region="us-west-2", window_start=NOW, window_end=NOW + timedelta(hours=2),
        carbon_intensity=120.0, water_stress=0.25, renewable_fraction=0.65,
        deadline=DEADLINE, job_duration_hours=2.0, community_score=0.4,
    )
    assert "us-west-2" in result.summary()
    assert "score=" in result.summary()
