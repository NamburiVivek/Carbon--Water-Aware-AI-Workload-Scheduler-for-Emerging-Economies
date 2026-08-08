"""
config/loader.py
Loads and validates GreenScheduler configuration from YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Sub-models ───────────────────────────────────────────────────────────────

class ApiKeys(BaseModel):
    electricity_maps: str = ""
    watttime: str = ""
    wri_aqueduct: str = ""


class Weights(BaseModel):
    carbon: float = 0.35
    water: float = 0.20
    renewable: float = 0.20
    deadline: float = 0.15
    community: float = 0.10

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "Weights":
        total = self.carbon + self.water + self.renewable + self.deadline + self.community
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total:.3f}")
        return self


class Constraints(BaseModel):
    max_carbon_intensity: float = 400.0   # gCO₂eq/kWh
    max_water_stress: float = 0.8         # 0–1
    min_renewable_fraction: float = 0.0   # 0–1


class Scheduling(BaseModel):
    lookahead_hours: int = 48
    window_resolution_minutes: int = 30
    default_deadline_hours: int = 24


class RegionConfig(BaseModel):
    grid_zone: str
    water_basin: str
    community_score: float = Field(0.5, ge=0.0, le=1.0)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False


class CacheConfig(BaseModel):
    backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    ttl_seconds: int = 900


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class Settings(BaseModel):
    api_keys: ApiKeys = ApiKeys()
    weights: Weights = Weights()
    constraints: Constraints = Constraints()
    scheduling: Scheduling = Scheduling()
    regions: Dict[str, RegionConfig] = {}
    server: ServerConfig = ServerConfig()
    cache: CacheConfig = CacheConfig()
    logging: LoggingConfig = LoggingConfig()


# ── Loader ────────────────────────────────────────────────────────────────────

def _find_config_file() -> Path:
    """Search for settings.yaml relative to the project root."""
    candidates = [
        Path(os.environ.get("GREENSCHED_CONFIG", "")),
        Path(__file__).parent / "settings.yaml",
        Path(__file__).parent / "settings.example.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "No settings.yaml found. Copy config/settings.example.yaml to "
        "config/settings.yaml and fill in your values."
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached, validated Settings instance."""
    config_path = _find_config_file()
    raw: Dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
    return Settings(**raw)
