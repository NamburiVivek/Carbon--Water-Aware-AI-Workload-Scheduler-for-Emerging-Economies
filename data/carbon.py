"""
data/carbon.py
Fetches real-time and forecast carbon intensity data.

Supports:
  - Electricity Maps API  (https://api.electricitymap.org/v3)
  - WattTime API          (https://api.watttime.org/v3)
  - Fallback mock data    (used when no API key is configured)
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

class CarbonWindow:
    """Carbon intensity for a single time window."""

    __slots__ = ("region", "start", "end", "intensity_gco2_kwh", "source")

    def __init__(
        self,
        region: str,
        start: datetime,
        end: datetime,
        intensity_gco2_kwh: float,
        source: str = "mock",
    ) -> None:
        self.region = region
        self.start = start
        self.end = end
        self.intensity_gco2_kwh = intensity_gco2_kwh
        self.source = source

    def __repr__(self) -> str:
        return (
            f"CarbonWindow({self.region}, {self.start.isoformat()}, "
            f"{self.intensity_gco2_kwh:.1f} gCO₂/kWh)"
        )


# ── Fetchers ──────────────────────────────────────────────────────────────────

class ElectricityMapsFetcher:
    """
    Wraps the Electricity Maps /carbon-intensity/forecast endpoint.
    Docs: https://static.electricitymaps.com/api/docs/index.html
    """

    BASE_URL = "https://api.electricitymap.org/v3"

    def __init__(self, api_key: str, cache: MemoryCache, ttl: int = 900) -> None:
        self._key = api_key
        self._cache = cache
        self._ttl = ttl

    def fetch_forecast(self, grid_zone: str, hours: int = 48) -> List[CarbonWindow]:
        cache_key = f"carbon:em:{grid_zone}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        url = f"{self.BASE_URL}/carbon-intensity/forecast"
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
            logger.warning("ElectricityMaps fetch failed for %s: %s", grid_zone, exc)
            return _mock_forecast(grid_zone, hours)

        windows = []
        for entry in data.get("forecast", []):
            start = datetime.fromisoformat(entry["datetime"].replace("Z", "+00:00"))
            end = start + timedelta(hours=1)
            windows.append(
                CarbonWindow(
                    region=grid_zone,
                    start=start,
                    end=end,
                    intensity_gco2_kwh=entry["carbonIntensity"],
                    source="electricity_maps",
                )
            )

        self._cache.set(cache_key, windows, ttl=self._ttl)
        return windows


class WattTimeFetcher:
    """
    Wraps WattTime v3 /forecast endpoint.
    Docs: https://docs.watttime.org
    """

    BASE_URL = "https://api.watttime.org/v3"

    def __init__(self, api_key: str, cache: MemoryCache, ttl: int = 900) -> None:
        self._key = api_key
        self._cache = cache
        self._ttl = ttl
        self._token: Optional[str] = None

    def _login(self) -> str:
        if self._token:
            return self._token
        # WattTime uses username:password basic auth for token exchange
        # api_key is expected as "username:password"
        parts = self._key.split(":", 1)
        username, password = (parts[0], parts[1]) if len(parts) == 2 else (self._key, "")
        resp = httpx.get(
            f"{self.BASE_URL}/login",
            auth=(username, password),
            timeout=10.0,
        )
        resp.raise_for_status()
        self._token = resp.json()["token"]
        return self._token

    def fetch_forecast(self, ba: str, hours: int = 48) -> List[CarbonWindow]:
        cache_key = f"carbon:wt:{ba}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        try:
            token = self._login()
            resp = httpx.get(
                f"{self.BASE_URL}/forecast",
                params={"ba": ba, "extended_forecast": "true"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("WattTime fetch failed for %s: %s", ba, exc)
            return _mock_forecast(ba, hours)

        windows = []
        for entry in data.get("data", []):
            start = datetime.fromisoformat(entry["point_time"].replace("Z", "+00:00"))
            end = start + timedelta(minutes=5)
            # WattTime returns MOER (Marginal Operating Emissions Rate) in lbs/MWh
            moer_lbs_mwh = entry.get("value", 0)
            # Convert: 1 lb/MWh = 453.592 g/MWh = 0.4536 gCO₂/kWh
            intensity = moer_lbs_mwh * 0.4536
            windows.append(
                CarbonWindow(
                    region=ba,
                    start=start,
                    end=end,
                    intensity_gco2_kwh=intensity,
                    source="watttime",
                )
            )

        self._cache.set(cache_key, windows, ttl=self._ttl)
        return windows


# ── Mock / fallback ───────────────────────────────────────────────────────────

# Typical daily carbon intensity patterns (gCO₂/kWh) by region archetype.
_REGION_BASE_CARBON: Dict[str, float] = {
    "US-MIDA-PJM": 350.0,
    "US-NW-PACW": 120.0,
    "IE": 280.0,
    "SG": 480.0,
    "DEFAULT": 300.0,
}


def _mock_forecast(region: str, hours: int = 48) -> List[CarbonWindow]:
    """
    Generates a synthetic 48-hour carbon intensity forecast.
    Uses a sinusoidal daily pattern plus random noise to simulate
    overnight low-carbon valleys (e.g., high wind / low demand).
    """
    base = _REGION_BASE_CARBON.get(region, _REGION_BASE_CARBON["DEFAULT"])
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    windows = []
    rng = random.Random(region)  # deterministic per region for testability

    for h in range(hours):
        start = now + timedelta(hours=h)
        end = start + timedelta(hours=1)
        # Sinusoidal daily cycle: lowest at 03:00, highest at 14:00 local
        hour_of_day = (start.hour + rng.randint(-1, 1)) % 24
        cycle = math.sin(math.pi * (hour_of_day - 3) / 12)  # -1 to +1
        noise = rng.gauss(0, base * 0.05)
        intensity = max(50.0, base + cycle * base * 0.3 + noise)
        windows.append(
            CarbonWindow(
                region=region,
                start=start,
                end=end,
                intensity_gco2_kwh=round(intensity, 1),
                source="mock",
            )
        )
    return windows


# ── Public interface ──────────────────────────────────────────────────────────

class CarbonDataService:
    """
    Unified carbon data service.  Selects the best available source
    based on configured API keys, falling back gracefully to mock data.
    """

    def __init__(
        self,
        electricity_maps_key: str = "",
        watttime_key: str = "",
        cache: Optional[MemoryCache] = None,
        ttl: int = 900,
    ) -> None:
        self._cache = cache or MemoryCache(default_ttl=ttl)
        self._em = ElectricityMapsFetcher(electricity_maps_key, self._cache, ttl) if electricity_maps_key else None
        self._wt = WattTimeFetcher(watttime_key, self._cache, ttl) if watttime_key else None

    def get_forecast(
        self,
        grid_zone: str,
        hours: int = 48,
        watttime_ba: Optional[str] = None,
    ) -> List[CarbonWindow]:
        """Return a list of CarbonWindow objects covering the next `hours` hours."""
        if self._em:
            return self._em.fetch_forecast(grid_zone, hours)
        if self._wt and watttime_ba:
            return self._wt.fetch_forecast(watttime_ba, hours)
        logger.debug("No carbon API configured; using mock data for %s", grid_zone)
        return _mock_forecast(grid_zone, hours)

    def get_intensity_at(
        self,
        grid_zone: str,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        """Return the average carbon intensity (gCO₂/kWh) for a specific window."""
        forecast = self.get_forecast(grid_zone)
        matching = [
            w.intensity_gco2_kwh
            for w in forecast
            if w.start < window_end and w.end > window_start
        ]
        if not matching:
            return _REGION_BASE_CARBON.get(grid_zone, _REGION_BASE_CARBON["DEFAULT"])
        return sum(matching) / len(matching)
