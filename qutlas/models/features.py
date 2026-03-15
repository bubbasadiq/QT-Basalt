"""
Feature Engineering

Transforms windows of synchronised sensor readings into
feature vectors suitable for property prediction models.

Raw sensor data is noisy and high-frequency. Models need
stable, informative representations. This module computes:

  - Statistical summaries over sliding windows
    (mean, std, min, max, trend)
  - Derived process variables
    (viscosity proxy, draw stability index, thermal uniformity)
  - Lag features
    (process state N timesteps ago)

All features are documented with their physical interpretation.
Feature names are stable — changing them breaks trained models.

Feature schema version: 1.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from qutlas.data_pipeline.sync import SyncedReading


# ── Feature vector definition ──────────────────────────────────────────────

# Ordered list of feature names. Order must never change between versions.
# Adding features requires a new schema version and model retraining.
FEATURE_NAMES_V1: list[str] = [
    # Window statistics — furnace temperature
    "temp_mean",
    "temp_std",
    "temp_min",
    "temp_max",
    "temp_trend",          # linear slope over window (°C/timestep)

    # Window statistics — fiber diameter
    "diam_mean",
    "diam_std",
    "diam_min",
    "diam_max",
    "diam_trend",

    # Window statistics — draw speed
    "speed_mean",
    "speed_std",
    "speed_trend",

    # Window statistics — melt viscosity
    "visc_mean",
    "visc_std",
    "visc_trend",

    # Derived variables
    "draw_stability_index",   # 1 - (diam_std / diam_mean), higher = more stable
    "thermal_uniformity",     # 1 - (temp_std / temp_mean), higher = more uniform
    "viscosity_proxy",        # draw_tension / draw_speed, correlates with viscosity
    "process_window_score",   # composite: how well params sit in optimal window

    # Current snapshot (latest values)
    "current_temp",
    "current_diam",
    "current_speed",
    "current_visc",
    "current_tension",
    "current_airflow",
]

FEATURE_DIM = len(FEATURE_NAMES_V1)


@dataclass
class FeatureVector:
    """
    A computed feature vector ready for model inference.

    Carries the feature values alongside metadata for traceability.
    """
    values:          list[float]
    feature_names:   list[str]
    window_size:     int
    schema_version:  str = "1.0"
    complete:        bool = True   # False if window had missing values
    missing_count:   int = 0

    def __len__(self) -> int:
        return len(self.values)

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.feature_names, self.values))

    def validate(self) -> list[str]:
        """Return list of issues. Empty means valid."""
        issues = []
        if len(self.values) != FEATURE_DIM:
            issues.append(
                f"Expected {FEATURE_DIM} features, got {len(self.values)}"
            )
        if any(math.isnan(v) or math.isinf(v) for v in self.values):
            issues.append("Feature vector contains NaN or Inf values")
        return issues


class FeatureEngineer:
    """
    Transforms a window of SyncedReadings into a FeatureVector.

    Usage:
        engineer = FeatureEngineer(window_size=100)
        window   = pipeline.synced_window(100)
        fv       = engineer.compute(window)
        # fv.values → list of 26 floats ready for the model
    """

    def __init__(self, window_size: int = 100) -> None:
        self.window_size = window_size

    def compute(self, window: list[SyncedReading]) -> FeatureVector:
        """
        Compute a feature vector from a window of synced readings.

        If the window is shorter than window_size, the available
        readings are used and complete=False is set.

        Args:
            window: list of SyncedReadings, oldest first

        Returns:
            FeatureVector with FEATURE_DIM values
        """
        if not window:
            return self._zero_vector()

        missing_count = sum(1 for r in window if not r.complete)

        # Extract time series for each sensor
        temps   = self._extract(window, "furnace_temp_c")
        diams   = self._extract(window, "fiber_diameter_um")
        speeds  = self._extract(window, "draw_speed_ms")
        viscs   = self._extract(window, "melt_viscosity_cp")
        tensions= self._extract(window, "draw_tension_n")
        airflows= self._extract(window, "airflow_rate_lpm")

        # Latest reading for current-state features
        latest = window[-1]

        # Compute statistics
        temp_stats  = _stats(temps)
        diam_stats  = _stats(diams)
        speed_stats = _stats(speeds)
        visc_stats  = _stats(viscs)

        # Derived features
        draw_stability = (
            1.0 - (diam_stats["std"] / max(diam_stats["mean"], 1e-6))
            if diam_stats["mean"] > 0 else 0.0
        )
        thermal_uniformity = (
            1.0 - (temp_stats["std"] / max(temp_stats["mean"], 1e-6))
            if temp_stats["mean"] > 0 else 0.0
        )
        viscosity_proxy = (
            _safe_mean(tensions) / max(_safe_mean(speeds), 1e-6)
            if tensions and speeds else 0.0
        )
        process_window_score = self._process_window_score(
            temp=temp_stats["mean"],
            diam=diam_stats["mean"],
            speed=speed_stats["mean"],
        )

        values = [
            # Temperature window stats
            temp_stats["mean"],
            temp_stats["std"],
            temp_stats["min"],
            temp_stats["max"],
            temp_stats["trend"],

            # Diameter window stats
            diam_stats["mean"],
            diam_stats["std"],
            diam_stats["min"],
            diam_stats["max"],
            diam_stats["trend"],

            # Speed window stats
            speed_stats["mean"],
            speed_stats["std"],
            speed_stats["trend"],

            # Viscosity window stats
            visc_stats["mean"],
            visc_stats["std"],
            visc_stats["trend"],

            # Derived
            draw_stability,
            thermal_uniformity,
            viscosity_proxy,
            process_window_score,

            # Current snapshot
            latest.furnace_temp_c      or 0.0,
            latest.fiber_diameter_um   or 0.0,
            latest.draw_speed_ms       or 0.0,
            latest.melt_viscosity_cp   or 0.0,
            latest.draw_tension_n      or 0.0,
            latest.airflow_rate_lpm    or 0.0,
        ]

        return FeatureVector(
            values        = values,
            feature_names = FEATURE_NAMES_V1,
            window_size   = len(window),
            complete      = missing_count == 0,
            missing_count = missing_count,
        )

    def _zero_vector(self) -> FeatureVector:
        """Return a zero-filled feature vector for edge cases."""
        return FeatureVector(
            values        = [0.0] * FEATURE_DIM,
            feature_names = FEATURE_NAMES_V1,
            window_size   = 0,
            complete      = False,
            missing_count = self.window_size,
        )

    @staticmethod
    def _extract(
        window: list[SyncedReading],
        field:  str,
    ) -> list[float]:
        """Extract a list of non-None float values for a sensor field."""
        return [
            v for r in window
            if (v := getattr(r, field, None)) is not None
        ]

    @staticmethod
    def _process_window_score(
        temp:  float,
        diam:  float,
        speed: float,
    ) -> float:
        """
        Score how well the current process parameters sit within the
        optimal operating window for basalt fiber drawing.

        Returns 0.0 (worst) to 1.0 (optimal).

        Optimal conditions (from draw window analysis):
          Temperature: 1460–1520°C
          Diameter:    9–16 µm
          Speed:       10–18 m/s
        """
        def in_range(v: float, lo: float, hi: float, soft: float = 0.1) -> float:
            """Soft-clipped range score: 1.0 inside, drops toward edges."""
            centre = (lo + hi) / 2.0
            half   = (hi - lo) / 2.0
            dist   = abs(v - centre) / max(half, 1e-6)
            return max(0.0, 1.0 - max(0.0, dist - 1.0) / soft)

        t_score = in_range(temp,  1460.0, 1520.0, soft=0.2)
        d_score = in_range(diam,  9.0,    16.0,   soft=0.5)
        s_score = in_range(speed, 10.0,   18.0,   soft=0.3)
        return (t_score + d_score + s_score) / 3.0


# ── Statistical helpers ────────────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stats(values: list[float]) -> dict[str, float]:
    """
    Compute mean, std, min, max, and linear trend for a list of values.
    Returns zeros for empty input.
    """
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "trend": 0.0}

    n    = len(values)
    mean = sum(values) / n
    var  = sum((v - mean) ** 2 for v in values) / n
    std  = math.sqrt(var)
    mn   = min(values)
    mx   = max(values)

    # Linear trend: slope of least-squares fit
    if n >= 2:
        xs    = list(range(n))
        x_bar = (n - 1) / 2.0
        num   = sum((xs[i] - x_bar) * (values[i] - mean) for i in range(n))
        den   = sum((xs[i] - x_bar) ** 2 for i in range(n))
        trend = num / den if den > 0 else 0.0
    else:
        trend = 0.0

    return {"mean": mean, "std": std, "min": mn, "max": mx, "trend": trend}
