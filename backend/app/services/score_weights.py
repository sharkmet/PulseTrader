"""
Scoring weight loader — reads config/score_weights.toml and exposes typed weights.

Falls back to hardcoded defaults if the TOML file is missing or invalid.
Uses Python 3.11's stdlib tomllib (zero extra dependencies).
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_TOML_PATH = Path(__file__).parent.parent.parent.parent / "config" / "score_weights.toml"

_DEFAULTS: dict = {
    "meta": {"version": "2.0.0", "description": "default"},
    "weights": {
        "trend": 0.25,
        "momentum": 0.20,
        "volatility": 0.10,
        "volume": 0.10,
        "sentiment": 0.15,
        "multi_timeframe": 0.10,
        "ai": 0.10,
    },
    "sub_weights": {
        "trend": {"ma_alignment": 0.40, "macd": 0.35, "ichimoku": 0.25},
        "momentum": {"rsi": 0.35, "stochastic": 0.25, "williams_r": 0.15, "price_return": 0.25},
        "volatility": {"bb_percent_b": 0.60, "keltner_pos": 0.40},
        "volume": {"volume_ratio": 0.50, "mfi": 0.50},
    },
}


@dataclass(frozen=True)
class ComponentWeights:
    """Top-level component weights."""
    trend: float
    momentum: float
    volatility: float
    volume: float
    sentiment: float
    multi_timeframe: float
    ai: float

    def as_dict(self) -> dict[str, float]:
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "volume": self.volume,
            "sentiment": self.sentiment,
            "multi_timeframe": self.multi_timeframe,
            "ai": self.ai,
        }

    def total(self) -> float:
        return sum(self.as_dict().values())


@dataclass(frozen=True)
class ScoringConfig:
    """Full scoring configuration loaded from TOML."""
    version: str
    components: ComponentWeights
    sub_weights: dict[str, dict[str, float]] = field(default_factory=dict)

    def sub(self, component: str) -> dict[str, float]:
        return self.sub_weights.get(component, {})


def _validate_sums_to_one(d: dict[str, float], context: str) -> None:
    total = sum(d.values())
    if not math.isclose(total, 1.0, abs_tol=0.001):
        raise ValueError(f"Weights in [{context}] sum to {total:.4f}, must be 1.0")


def _load_toml() -> dict:
    try:
        if _TOML_PATH.exists():
            with _TOML_PATH.open("rb") as f:
                return tomllib.load(f)
    except Exception:
        pass
    return _DEFAULTS


@lru_cache(maxsize=1)
def get_scoring_config() -> ScoringConfig:
    """Load and validate scoring config. Cached for the process lifetime."""
    raw = _load_toml()

    w = raw.get("weights", _DEFAULTS["weights"])
    _validate_sums_to_one(w, "weights")

    sub = raw.get("sub_weights", _DEFAULTS["sub_weights"])
    for key, sw in sub.items():
        _validate_sums_to_one(sw, f"sub_weights.{key}")

    return ScoringConfig(
        version=raw.get("meta", {}).get("version", "2.0.0"),
        components=ComponentWeights(
            trend=w["trend"],
            momentum=w["momentum"],
            volatility=w["volatility"],
            volume=w["volume"],
            sentiment=w["sentiment"],
            multi_timeframe=w["multi_timeframe"],
            ai=w["ai"],
        ),
        sub_weights=sub,
    )
