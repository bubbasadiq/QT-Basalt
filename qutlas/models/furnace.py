"""
Furnace Thermodynamics Model

Models the thermal behaviour of a basalt melting furnace.
Implements heat transfer dynamics, melt temperature response
to setpoint changes, and thermal lag characteristics.

Physics basis:
  - First-order thermal lag model for furnace response
  - Basalt specific heat capacity: ~880 J/(kg·K) (Lira et al., 2016)
  - Basalt thermal conductivity: ~1.7 W/(m·K) at melt temperatures
  - Typical furnace time constant: 120–300 seconds depending on mass
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class FurnaceState:
    """Current state of the furnace thermal system."""
    temp_c:          float = 1480.0   # current melt temperature °C
    setpoint_c:      float = 1480.0   # active temperature setpoint °C
    power_pct:       float = 0.0      # heating element power 0–100%
    wall_temp_c:     float = 1200.0   # furnace wall temperature °C
    heat_loss_w:     float = 0.0      # instantaneous heat loss to environment


@dataclass
class FurnaceConfig:
    """
    Physical configuration of the furnace.
    Values represent a small-scale pilot furnace suitable for Phase 1.
    """
    # Thermal mass
    melt_mass_kg:          float = 8.0      # kg of basalt melt in crucible
    specific_heat_j_kgk:   float = 880.0    # J/(kg·K) basalt melt

    # Heating
    max_power_w:           float = 15000.0  # W — maximum heating element power
    thermal_efficiency:    float = 0.72     # fraction of power reaching melt

    # Heat loss (to environment)
    insulation_r_value:    float = 3.2      # thermal resistance m²·K/W
    furnace_surface_m2:    float = 0.8      # external surface area m²
    ambient_temp_c:        float = 25.0     # ambient temperature °C

    # Controller
    pid_kp:                float = 0.8      # proportional gain
    pid_ki:                float = 0.02     # integral gain
    pid_kd:                float = 0.1      # derivative gain
    max_ramp_rate_c_min:   float = 8.0      # maximum heating ramp °C/min

    # Operating limits
    max_temp_c:            float = 1620.0
    min_temp_c:            float = 800.0


class FurnaceModel:
    """
    First-order thermal model of a basalt melting furnace.

    Simulates temperature response to setpoint changes using
    energy balance: dT/dt = (P_input - P_loss) / (m * Cp)

    Usage:
        furnace = FurnaceModel()
        furnace.set_setpoint(1500.0)
        for _ in range(steps):
            state = furnace.step(dt=0.01)
    """

    def __init__(self, config: FurnaceConfig | None = None) -> None:
        self.config = config or FurnaceConfig()
        self.state  = FurnaceState(
            temp_c     = self.config.ambient_temp_c,
            setpoint_c = 1480.0,
            wall_temp_c= self.config.ambient_temp_c * 1.1,
        )
        # PID state
        self._integral:    float = 0.0
        self._prev_error:  float = 0.0
        self._last_dt:     float = 0.01

    # ── Public interface ────────────────────────────────────────────────

    def set_setpoint(self, temp_c: float) -> None:
        """Set the furnace temperature target."""
        clamped = max(self.config.min_temp_c,
                      min(self.config.max_temp_c, temp_c))
        # Enforce ramp rate limit
        max_step = self.config.max_ramp_rate_c_min / 60.0 * self._last_dt * 100
        delta = clamped - self.state.setpoint_c
        if abs(delta) > max_step:
            clamped = self.state.setpoint_c + math.copysign(max_step, delta)
        self.state.setpoint_c = clamped

    def step(self, dt: float = 0.01) -> FurnaceState:
        """
        Advance simulation by dt seconds.

        Args:
            dt: timestep in seconds

        Returns:
            Updated FurnaceState
        """
        self._last_dt = dt
        cfg = self.config

        # ── PID controller ──────────────────────────────────────────
        error      = self.state.setpoint_c - self.state.temp_c
        self._integral  += error * dt
        derivative  = (error - self._prev_error) / dt if dt > 0 else 0.0
        self._prev_error = error

        power_demand = (
            cfg.pid_kp * error +
            cfg.pid_ki * self._integral +
            cfg.pid_kd * derivative
        )
        # Clamp to 0–100%
        self.state.power_pct = max(0.0, min(100.0, power_demand))

        # ── Energy balance ──────────────────────────────────────────
        # Power delivered to melt
        p_input = (self.state.power_pct / 100.0) * cfg.max_power_w * cfg.thermal_efficiency

        # Heat loss through furnace walls (Newton's law of cooling)
        p_loss = (
            (self.state.temp_c - cfg.ambient_temp_c) *
            cfg.furnace_surface_m2 /
            cfg.insulation_r_value
        )
        self.state.heat_loss_w = p_loss

        # Net power → temperature change
        thermal_mass = cfg.melt_mass_kg * cfg.specific_heat_j_kgk
        dT_dt = (p_input - p_loss) / thermal_mass
        self.state.temp_c += dT_dt * dt

        # Clamp to physical limits
        self.state.temp_c = max(cfg.ambient_temp_c,
                                min(cfg.max_temp_c, self.state.temp_c))

        # Wall temperature lags melt temperature
        wall_lag = 0.005 * dt  # very slow thermal mass
        self.state.wall_temp_c += (self.state.temp_c * 0.82 - self.state.wall_temp_c) * wall_lag

        return self.state

    @property
    def is_at_setpoint(self, tolerance_c: float = 5.0) -> bool:
        """True when melt temperature is within tolerance of setpoint."""
        return abs(self.state.temp_c - self.state.setpoint_c) <= tolerance_c

    def steady_state_temp(self, power_pct: float) -> float:
        """
        Calculate the steady-state temperature for a given power level.
        Useful for feed-forward control initialisation.
        """
        cfg = self.config
        p_input = (power_pct / 100.0) * cfg.max_power_w * cfg.thermal_efficiency
        # At steady state: p_input = p_loss → T = T_amb + p_input * R / A
        return cfg.ambient_temp_c + (
            p_input * cfg.insulation_r_value / cfg.furnace_surface_m2
        )
