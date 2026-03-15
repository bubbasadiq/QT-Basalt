"""
Control Layer Types

Defines the state machine, action types, and configuration
structures used by the adaptive control layer.

The control system operates in one of five states:
  IDLE       — no active recipe, holding last known safe parameters
  WARMING    — furnace heating toward recipe initial temperature
  CONVERGING — actively adjusting parameters toward recipe targets
  STABLE     — within tolerance band, minor maintenance adjustments only
  ABORTED    — safety limit breached, emergency stop issued

State transitions:
  IDLE → WARMING    (on recipe activation)
  WARMING → CONVERGING (on reaching initial temperature)
  CONVERGING → STABLE   (on first tolerance-met prediction)
  STABLE → CONVERGING   (on drift outside tolerance)
  ANY → ABORTED     (on safety limit breach)
  ABORTED → IDLE    (on manual reset only)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Optional


class ControlState(str, Enum):
    IDLE       = "idle"
    WARMING    = "warming"
    CONVERGING = "converging"
    STABLE     = "stable"
    ABORTED    = "aborted"


class AdjustmentReason(str, Enum):
    """Why a parameter adjustment was made."""
    DIAMETER_ERROR     = "diameter_error"      # fiber diameter off target
    TENSILE_ERROR      = "tensile_error"       # tensile prediction off target
    THERMAL_ERROR      = "thermal_error"       # thermal prediction off target
    STABILITY_LOW      = "stability_low"       # draw stability below threshold
    WARMING            = "warming"             # heating toward initial setpoint
    MAINTENANCE        = "maintenance"         # minor trim while stable
    SAFETY_LIMIT       = "safety_limit"        # hard limit enforcement
    CONFIDENCE_LOW     = "confidence_low"      # holding due to low prediction confidence
    EMERGENCY_STOP     = "emergency_stop"      # safety breach


@dataclass
class ControlConfig:
    """
    Configuration for the adaptive controller.

    Tuning parameters are separated from safety limits.
    Safety limits are hard constraints — they cannot be overridden
    by recipe configuration or runtime tuning.
    """

    # ── Tuning parameters ─────────────────────────────────────────────
    # Temperature adjustment gain: °C per GPa tensile error
    temp_gain_tensile:       float = 12.0
    # Temperature adjustment gain: °C per µm diameter error
    temp_gain_diameter:      float = 3.5
    # Speed adjustment gain: m/s per µm diameter error
    speed_gain_diameter:     float = 0.15
    # Airflow adjustment gain: L/min per °C thermal error
    airflow_gain_thermal:    float = 0.08

    # Maximum adjustment per control cycle (prevents over-correction)
    max_temp_step_c:         float = 8.0    # °C per cycle
    max_speed_step_ms:       float = 0.5    # m/s per cycle
    max_airflow_step_lpm:    float = 3.0    # L/min per cycle

    # Confidence threshold below which controller holds current params
    min_confidence_to_act:   float = 0.35

    # Stability threshold — diameter CV below this → STABLE state
    stability_cv_threshold:  float = 2.5    # %

    # How many consecutive stable predictions before declaring STABLE
    stable_predictions_needed: int = 5

    # ── Safety limits (hard — never violated) ─────────────────────────
    safety_max_temp_c:       float = 1600.0
    safety_min_temp_c:       float = 1350.0
    safety_max_speed_ms:     float = 25.0
    safety_min_speed_ms:     float = 0.5
    safety_max_airflow_lpm:  float = 120.0
    safety_min_airflow_lpm:  float = 10.0

    # ── Ramp rate limits ──────────────────────────────────────────────
    max_temp_ramp_c_per_min: float = 8.0


@dataclass
class ParameterSetpoint:
    """Current target setpoints for all controllable parameters."""
    furnace_temp_c:    float = 1480.0
    draw_speed_ms:     float = 12.0
    airflow_lpm:       float = 50.0


@dataclass
class ControlDecision:
    """
    A single control decision — the output of one control cycle.

    Records both the action taken and the reasoning behind it,
    providing full auditability of the control system's behaviour.
    """
    timestamp:         datetime
    run_id:            Optional[str]
    state:             ControlState

    # New setpoints
    setpoint:          ParameterSetpoint

    # Adjustments made this cycle (delta from previous setpoint)
    delta_temp_c:      float = 0.0
    delta_speed_ms:    float = 0.0
    delta_airflow_lpm: float = 0.0

    # Reasoning
    reasons:           list[AdjustmentReason] = field(default_factory=list)
    notes:             str = ""

    # Context
    prediction_confidence: float = 0.0
    within_tolerance:      bool  = False
    consecutive_stable:    int   = 0

    @property
    def any_adjustment(self) -> bool:
        return any([
            abs(self.delta_temp_c)      > 0.01,
            abs(self.delta_speed_ms)    > 0.001,
            abs(self.delta_airflow_lpm) > 0.01,
        ])

    def summary(self) -> str:
        reasons_str = ", ".join(r.value for r in self.reasons) or "none"
        return (
            f"[{self.state.value}] "
            f"T={self.setpoint.furnace_temp_c:.1f}°C "
            f"({self.delta_temp_c:+.1f})  "
            f"v={self.setpoint.draw_speed_ms:.2f}m/s "
            f"({self.delta_speed_ms:+.3f})  "
            f"reasons={reasons_str}"
        )
