"""
data/water.py
Water stress data service.

Sources:
  - WRI Aqueduct API (optional)
  - Bundled static baseline stress indices by basin
  - Seasonal adjustment model for drought periods
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx

from data.cache import MemoryCache

logger = logging.getLogger(__name__)


# ── Baseline water stress indices (WRI Aqueduct 4.0 data, 2023) ─────────────
# Scale: 0.0 (low stress) → 1.0 (extremely high stress)
# Source: https://www.wri.org/data/aqueduct-water-risk-atlas
_BASIN_STRESS: Dict[str, float] = {
    "Ohio River": 0.25,
    "Columbia River": 0.35,
    "Shannon": 0.15,
    "Johor River": 0.55,
    "Colorado River": 0.90,
    "Indus": 0.95,
    "Yellow River": 0.85,
    "Rhine": 0.40,
    "DEFAULT": 0.50,
}


class WaterStressWindow:
    """Water stress reading for a basin at a point in time."""

    __slots__ = ("basin", "timestamp", "stress_index", "drought_alert", "source")

    def __init__(
        self,
        basin: str,
        timestamp: datetime,
        stress_index: float,
        drought_alert: bool = False,
        source: str = "static",
    ) -> None:
        self.basin = basin
        self.timestamp = timestamp
        self.stress_index = stress_index      # 0–1
        self.drought_alert = drought_alert
        self.source = source

    def __repr__(self) -> str:
        alert = " ⚠️ DROUGHT" if self.drought_alert else ""
        return f"WaterStress({self.basin}, {self.stress_index:.2f}{alert})"


class WaterDataService:
    """
    Provides current and forecast water stress for a named basin.

    When no live API is configured, applies a seasonal correction to
    the WRI Aqueduct static baseline to simulate summer/drought peaks.
    """

    def __init__(
        self,
        aqueduct_key: str = "",
        cache: Optional[MemoryCache] = None,
        ttl: int = 3600,
        drought_threshold: float = 0.7,
    ) -> None:
        self._key = aqueduct_key
        self._cache = cache or MemoryCache(default_ttl=ttl)
        self._ttl = ttl
        self._drought_threshold = drought_threshold

    # ── Public API ──────────────────────────────────────────────────────────

    def get_stress(self, basin: str) -> WaterStressWindow:
        """Return the current water stress for a basin."""
        cache_key = f"water:{basin}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        if self._key:
            result = self._fetch_live(basin)
        else:
            result = self._compute_seasonal(basin)

        self._cache.set(cache_key, result, ttl=self._ttl)
        return result

    def get_stress_index(self, basin: str) -> float:
        """Convenience: return just the numeric stress index."""
        return self.get_stress(basin).stress_index

    def is_drought_alert(self, basin: str) -> bool:
        """Return True if the basin is in a drought-alert state."""
        return self.get_stress(basin).drought_alert

    # ── Live fetch (WRI Aqueduct) ────────────────────────────────────────────

    def _fetch_live(self, basin: str) -> WaterStressWindow:
        """
        Placeholder for WRI Aqueduct API integration.
        The Aqueduct API is subscription-based; this shows the integration
        pattern.  Falls back to seasonal model on any error.
        """
        url = "https://www.wri.org/api/aqueduct/v1/water-stress"
        try:
            resp = httpx.get(
                url,
                params={"basin": basin},
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            stress = float(data["stress_index"])
            alert = stress >= self._drought_threshold
            return WaterStressWindow(
                basin=basin,
                timestamp=datetime.now(timezone.utc),
                stress_index=min(stress, 1.0),
                drought_alert=alert,
                source="wri_aqueduct",
            )
        except Exception as exc:
            logger.warning("WRI Aqueduct fetch failed for %s: %s", basin, exc)
            return self._compute_seasonal(basin)

    # ── Seasonal model ────────────────────────────────────────────────────────

    def _compute_seasonal(self, basin: str) -> WaterStressWindow:
        """
        Applies a sinusoidal seasonal correction to the static baseline.
        Peak stress occurs in July–August (northern hemisphere summer).
        """
        baseline = _BASIN_STRESS.get(basin, _BASIN_STRESS["DEFAULT"])
        now = datetime.now(timezone.utc)

        # Day-of-year normalised to [0, 2π], peak at day ~200 (July 19)
        doy = now.timetuple().tm_yday
        seasonal_factor = 0.15 * math.sin(2 * math.pi * (doy - 80) / 365)

        # Small stochastic noise to simulate sensor variance
        noise = random.gauss(0, 0.02)

        stress = min(max(baseline + seasonal_factor + noise, 0.0), 1.0)
        alert = stress >= self._drought_threshold

        return WaterStressWindow(
            basin=basin,
            timestamp=now,
            stress_index=round(stress, 3),
            drought_alert=alert,
            source="seasonal_model",
        )
