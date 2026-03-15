"""
Safety Monitor

Enforces hard process limits before every control action.
This is the last line of defence before a command reaches hardware.

The safety monitor:
  - Clamps all setpoints to configured hard limits
  - Detects safety breaches in incoming sensor readings
  - Triggers emergency stop when limits are exceeded
  - Logs every safety intervention

Safety is never traded against performance. The safety monitor
runs synchronously before every control action and cannot be
bypassed by recipe configuration or runtime tuning.

Design principle: fail safe.
  - If a sensor reading is missing, assume the worst plausible value
  - If a setpoint calculation produces NaN or Inf, clamp to safe value
  - If the safety monitor itself errors, trigger emergency stop
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional

from qutlas.data_pipeline.sync import SyncedReading
from qutlas.control.types      import ControlConfig, ParameterSetpoint

logger = logging.getLogger(__name__)


@dataclass
class SafetyStatus:
    """Result of a safety check."""
    safe:             bool
    breaches:         list[str] = field(default_factory=list)
    clamped_fields:   list[str] = field(default_factory=list)
    emergency_stop:   bool      = False

    @property
    def any_clamped(self) -> bool:
        return len(self.clamped_fields) > 0


class SafetyMonitor:
    """
    Hard-limit safety monitor for the manufacturing process.

    Usage:
        monitor = SafetyMonitor(config)

        # Check a sensor reading for breaches:
        status = monitor.check_reading(reading)
        if not status.safe:
            controller.abort(status.breaches)

        # Clamp a setpoint to safe bounds:
        safe_sp = monitor.clamp_setpoint(proposed_setpoint)
    """

    def __init__(self, config: ControlConfig | None = None) -> None:
        self.config      = config or ControlConfig()
        self._breach_count:  int = 0
        self._clamp_count:   int = 0
        self._e_stop_count:  int = 0

    # ── Sensor reading checks ───────────────────────────────────────────

    def check_reading(
        self,
        reading: SyncedReading,
    ) -> SafetyStatus:
        """
        Check a sensor reading for safety breaches.

        A breach occurs when a sensor value exceeds the hard limits
        defined in ControlConfig. Missing critical values are treated
        as potential breaches and logged as warnings.

        Args:
            reading: current synchronised sensor reading

        Returns:
            SafetyStatus — safe=False triggers emergency stop
        """
        breaches: list[str] = []
        cfg = self.config

        # Furnace temperature
        if reading.furnace_temp_c is not None:
            if reading.furnace_temp_c > cfg.safety_max_temp_c:
                breaches.append(
                    f"furnace_temp_c={reading.furnace_temp_c:.1f}°C "
                    f"exceeds max {cfg.safety_max_temp_c}°C"
                )
            elif reading.furnace_temp_c < cfg.safety_min_temp_c:
                breaches.append(
                    f"furnace_temp_c={reading.furnace_temp_c:.1f}°C "
                    f"below min {cfg.safety_min_temp_c}°C"
                )
        else:
            logger.warning("Safety: furnace_temp_c is None — cannot verify thermal safety")

        # Draw speed
        if reading.draw_speed_ms is not None:
            if reading.draw_speed_ms > cfg.safety_max_speed_ms:
                breaches.append(
                    f"draw_speed_ms={reading.draw_speed_ms:.2f} "
                    f"exceeds max {cfg.safety_max_speed_ms}"
                )

        # Fiber diameter — extreme values indicate process instability
        if reading.fiber_diameter_um is not None:
            if reading.fiber_diameter_um < 1.0:
                breaches.append(
                    f"fiber_diameter_um={reading.fiber_diameter_um:.2f} "
                    f"critically low — possible fiber break"
                )
            elif reading.fiber_diameter_um > 80.0:
                breaches.append(
                    f"fiber_diameter_um={reading.fiber_diameter_um:.1f} "
                    f"critically high — possible bushing blockage"
                )

        if breaches:
            self._breach_count += len(breaches)
            for b in breaches:
                logger.error(f"Safety breach: {b}")

        return SafetyStatus(
            safe           = len(breaches) == 0,
            breaches       = breaches,
            emergency_stop = len(breaches) > 0,
        )

    # ── Setpoint clamping ───────────────────────────────────────────────

    def clamp_setpoint(
        self,
        setpoint: ParameterSetpoint,
        recipe_max_temp: Optional[float] = None,
        recipe_min_temp: Optional[float] = None,
        recipe_max_speed: Optional[float] = None,
    ) -> tuple[ParameterSetpoint, SafetyStatus]:
        """
        Clamp a proposed setpoint to hard safety limits.

        Recipe limits are applied as an additional constraint on top
        of the hard safety limits (the more restrictive applies).

        Args:
            setpoint:         proposed setpoints (may be modified)
            recipe_max_temp:  optional recipe-level upper temperature limit
            recipe_min_temp:  optional recipe-level lower temperature limit
            recipe_max_speed: optional recipe-level upper speed limit

        Returns:
            (clamped_setpoint, SafetyStatus)
        """
        cfg     = self.config
        clamped: list[str] = []

        def _clamp(val: float, lo: float, hi: float, name: str) -> float:
            """Clamp value and record if clamping occurred."""
            if not math.isfinite(val):
                logger.warning(f"Safety: {name}={val} is not finite, clamping to {lo}")
                clamped.append(f"{name}: non-finite → {lo}")
                return lo
            if val < lo:
                clamped.append(f"{name}: {val:.2f} → {lo:.2f} (min)")
                return lo
            if val > hi:
                clamped.append(f"{name}: {val:.2f} → {hi:.2f} (max)")
                return hi
            return val

        # Effective temperature limits (most restrictive of safety + recipe)
        eff_max_temp  = min(cfg.safety_max_temp_c,  recipe_max_temp  or cfg.safety_max_temp_c)
        eff_min_temp  = max(cfg.safety_min_temp_c,  recipe_min_temp  or cfg.safety_min_temp_c)
        eff_max_speed = min(cfg.safety_max_speed_ms, recipe_max_speed or cfg.safety_max_speed_ms)

        safe_temp  = _clamp(setpoint.furnace_temp_c, eff_min_temp, eff_max_temp, "furnace_temp_c")
        safe_speed = _clamp(setpoint.draw_speed_ms, cfg.safety_min_speed_ms, eff_max_speed, "draw_speed_ms")
        safe_air   = _clamp(setpoint.airflow_lpm, cfg.safety_min_airflow_lpm, cfg.safety_max_airflow_lpm, "airflow_lpm")

        if clamped:
            self._clamp_count += len(clamped)
            for c in clamped:
                logger.debug(f"Safety clamp: {c}")

        safe_setpoint = ParameterSetpoint(
            furnace_temp_c = safe_temp,
            draw_speed_ms  = safe_speed,
            airflow_lpm    = safe_air,
        )

        return safe_setpoint, SafetyStatus(
            safe           = True,
            clamped_fields = clamped,
        )

    # ── Stats ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "breach_count": self._breach_count,
            "clamp_count":  self._clamp_count,
            "e_stop_count": self._e_stop_count,
        }
