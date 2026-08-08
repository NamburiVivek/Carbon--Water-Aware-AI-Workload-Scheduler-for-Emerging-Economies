"""
data/renewable.py
Renewable energy availability forecasts.

Provides the fraction of electricity from renewable sources (solar, wind,
hydro, geothermal) for a given grid zone and time window.

Sources:
  - Electricity Maps /power-breakdown/forecast (preferred)
  - Synthetic model combining solar + wind curves (fallback)
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

from data.cache import MemoryCache

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

class RenewableWindow:
    """Renewable fraction for a single time window."""

    __slots__ = ("region", "start", "end", "renewable_fraction", "breakdown", "source")

    def __init__(
        self,
        region: str,
        start: datetime,
        end: datetime,
        renewable_fraction: float,
        breakdown: Optional[Dict[str, float]] = None,
        source: str = "mock",
    ) -> None:
        self.region = region
        self.start = start
        self.end = end
        self.renewable_fraction = max(0.0, min(1.0, renewable_fraction))
        self.breakdown = breakdown or {}  # e.g. {"wind": 0.4, "solar": 0.2, ...}
        self.source = source

    def __repr__(self) -> str:
        return (
            f"RenewableWindow({self.region}, {self.start.isoformat()}, "
            f"{self.renewable_fraction:.1%})"
        )


# ── Electricity Maps fetcher ──────────────────────────────────────────────────

RENEWABLE_SOURCES = {"wind", "solar", "hydro", "geothermal", "biomass", "nuclear"}


class ElectricityMapsRenewableFetcher:
    BASE_URL = "https://api.electricitymap.org/v3"

    def __init__(self, api_key: str, cache: MemoryCache, ttl: int = 900) -> None:
        self._key = api_key
        self._cache = cache
        self._ttl = ttl

    def fetch_forecast(self, grid_zone: str, hours: int = 48) -> List[RenewableWindow]:
        cache_key = f"renewable:em:{grid_zone}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        url = f"{self.BASE_URL}/power-breakdown/forecast"
        try:
            resp = httpx.get(
                url,
                params={"zone": grid_zone},
                headers={"auth-token": self._key},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("ElectricityMaps renewable fetch failed for %s: %s", grid_zone, exc)
            return _mock_renewable_forecast(grid_zone, hours)

        windows = []
        for entry in data.get("forecast", []):
            start = datetime.fromisoformat(entry["datetime"].replace("Z", "+00:00"))
            end = start + timedelta(hours=1)
            breakdown_mw = entry.get("powerProductionBreakdown", {})
            total = sum(v for v in breakdown_mw.values() if v and v > 0)
            renewable_mw = sum(
                v for k, v in breakdown_mw.items()
                if k.lower() in RENEWABLE_SOURCES and v and v > 0
            )
            fraction = renewable_mw / total if total > 0 else 0.0
            breakdown_frac = {
                k: (v / total if total > 0 else 0.0)
                for k, v in breakdown_mw.items()
                if v and v > 0
            }
            windows.append(
                RenewableWindow(
                    region=grid_zone,
                    start=start,
                    end=end,
                    renewable_fraction=fraction,
                    breakdown=breakdown_frac,
                    source="electricity_maps",
                )
            )

        self._cache.set(cache_key, windows, ttl=self._ttl)
        return windows


# ── Synthetic fallback model ──────────────────────────────────────────────────

# Typical base renewable fraction by region archetype
_REGION_BASE_RENEWABLE: Dict[str, float] = {
    "US-MIDA-PJM": 0.22,
    "US-NW-PACW": 0.65,   # heavy hydro
    "IE": 0.45,            # strong wind
    "SG": 0.05,
    "DEFAULT": 0.30,
}


def _mock_renewable_forecast(region: str, hours: int = 48) -> List[RenewableWindow]:
    """
    Generates synthetic renewable availability using solar + wind curves.

    Solar: Gaussian peak centred at 12:00 UTC (roughly midday).
    Wind:  Sinusoidal with peak around 02:00–06:00 (typical overnight pattern).
    """
    base = _REGION_BASE_RENEWABLE.get(region, _REGION_BASE_RENEWABLE["DEFAULT"])
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rng = random.Random(region + "_renewable")
    windows = []

    for h in range(hours):
        start = now + timedelta(hours=h)
        end = start + timedelta(hours=1)
        hour = start.hour

        # Solar contribution: Gaussian peak at noon
        solar = max(0.0, 0.3 * math.exp(-((hour - 12) ** 2) / 18.0))

        # Wind contribution: sinusoidal, peaks overnight
        wind = 0.15 * (1 + math.cos(math.pi * hour / 12))

        # Noise
        noise = rng.gauss(0, 0.03)

        fraction = min(max(base * 0.5 + solar + wind + noise, 0.0), 1.0)
        windows.append(
            RenewableWindow(
                region=region,
                start=start,
                end=end,
                renewable_fraction=round(fraction, 3),
                breakdown={"solar": round(solar, 3), "wind": round(wind, 3)},
                source="mock",
            )
        )
    return windows


# ── Public service ────────────────────────────────────────────────────────────

class RenewableDataService:
    """Unified renewable data service with graceful fallback."""

    def __init__(
        self,
        electricity_maps_key: str = "",
        cache: Optional[MemoryCache] = None,
        ttl: int = 900,
    ) -> None:
        self._cache = cache or MemoryCache(default_ttl=ttl)
        self._em = (
            ElectricityMapsRenewableFetcher(electricity_maps_key, self._cache, ttl)
            if electricity_maps_key
            else None
        )

    def get_forecast(self, grid_zone: str, hours: int = 48) -> List[RenewableWindow]:
        if self._em:
            return self._em.fetch_forecast(grid_zone, hours)
        logger.debug("No renewable API configured; using mock data for %s", grid_zone)
        return _mock_renewable_forecast(grid_zone, hours)

    def get_fraction_at(
        self,
        grid_zone: str,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        """Return average renewable fraction for a specific window."""
        forecast = self.get_forecast(grid_zone)
        matching = [
            w.renewable_fraction
            for w in forecast
            if w.start < window_end and w.end > window_start
        ]
        if not matching:
            return _REGION_BASE_RENEWABLE.get(region := grid_zone, _REGION_BASE_RENEWABLE["DEFAULT"])
        return sum(matching) / len(matching)
