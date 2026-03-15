"""
Qutlas Process Simulator

Ties the furnace, viscosity, and fiber draw models into a single
coherent process simulation. Acts as a hardware replacement during
Phase 1 — the control layer communicates with this simulator
using the same interface it will use with real hardware.

The simulator:
  1. Accepts control actions (setpoints) as inputs
  2. Advances physics models by one timestep
  3. Returns realistic sensor readings with configurable noise
  4. Records a complete ProductionRun for training data export
"""

from __future__ import annotations

import random
import math
from datetime import datetime, UTC
from typing import Optional

from qutlas.schema import (
    ControlAction,
    DataSource,
    FiberRecipe,
    ProductionRun,
    RunStatus,
    SensorReading,
)
from qutlas.models.furnace    import FurnaceModel, FurnaceConfig
from qutlas.models.viscosity  import ViscosityModel
from qutlas.models.fiber_draw import FiberDrawModel, BushingConfig


class ProcessSimulator:
    """
    Closed-loop process simulator for basalt fiber manufacturing.

    Simulates the full production environment:
      - Furnace thermal dynamics
      - Melt viscosity from temperature
      - Fiber draw mechanics
      - Realistic sensor noise and drift

    Usage:
        sim = ProcessSimulator(noise_level=0.02)
        sim.start_run(recipe)

        for _ in range(steps):
            action = controller.decide(sim.latest_reading)
            reading = sim.step(action, dt=0.01)

        run = sim.complete_run()
    """

    def __init__(
        self,
        noise_level:   float = 0.02,
        dt:            float = 0.01,   # seconds per timestep
        furnace_config: FurnaceConfig  | None = None,
        bushing_config: BushingConfig  | None = None,
    ) -> None:
        self.noise_level = noise_level
        self.dt          = dt

        # Physics models
        self.furnace  = FurnaceModel(furnace_config)
        self.viscosity = ViscosityModel()
        self.draw      = FiberDrawModel(bushing_config)

        # State
        self._run:     Optional[ProductionRun] = None
        self._step_n:  int = 0
        self._draw_speed_ms: float = 12.0

    # ── Run lifecycle ───────────────────────────────────────────────────

    def start_run(self, recipe: FiberRecipe) -> ProductionRun:
        """
        Initialise a new production run against a fiber recipe.

        Primes the furnace at the recipe's initial temperature target.
        """
        self._run = ProductionRun(
            recipe     = recipe,
            status     = RunStatus.ACTIVE,
            source     = DataSource.SIMULATOR,
            started_at = datetime.now(UTC),
        )
        self._step_n = 0
        self._draw_speed_ms = recipe.initial_draw_speed_ms

        # Prime furnace to recipe initial temperature
        self.furnace.set_setpoint(recipe.initial_temp_c)

        return self._run

    def step(
        self,
        action: Optional[ControlAction] = None,
    ) -> SensorReading:
        """
        Advance simulation by one timestep.

        If a ControlAction is provided, applies setpoint changes
        before computing the next state.

        Args:
            action: optional control action from the control layer

        Returns:
            SensorReading reflecting the new process state
        """
        if action is not None:
            self._apply_action(action)

        # Advance physics
        furnace_state = self.furnace.step(self.dt)
        visc_cp       = self.viscosity.viscosity_cp(furnace_state.temp_c)
        draw_state    = self.draw.step(
            draw_speed_ms = self._draw_speed_ms,
            viscosity_cp  = visc_cp,
            temp_c        = furnace_state.temp_c,
        )

        # Build sensor reading with noise
        reading = SensorReading(
            timestamp          = datetime.now(UTC),
            source             = DataSource.SIMULATOR,
            furnace_temp_c     = self._noisy(furnace_state.temp_c,    scale=2.0),
            melt_viscosity_cp  = self._noisy(visc_cp,                 scale=visc_cp * 0.015),
            melt_flow_rate     = self._noisy(draw_state.mass_flow_g_min, scale=0.5),
            fiber_diameter_um  = self._noisy(draw_state.fiber_diameter_um, scale=0.3),
            draw_speed_ms      = self._noisy(self._draw_speed_ms,     scale=0.05),
            draw_tension_n     = self._noisy(draw_state.draw_tension_n, scale=0.02),
            cooling_zone_temp_c= self._noisy(furnace_state.temp_c * 0.35, scale=3.0),
            airflow_rate_lpm   = self._noisy(50.0,                    scale=0.8),
            run_id             = self._run.run_id if self._run else None,
            sequence           = self._step_n,
        )

        # Accumulate in run record
        if self._run is not None:
            self._run.readings.append(reading)
            if action is not None:
                self._run.actions.append(action)

        self._step_n += 1
        return reading

    def complete_run(self) -> ProductionRun:
        """
        Mark the active run as complete and return the full record.
        Generates synthetic outcome values from final process state.
        """
        if self._run is None:
            raise RuntimeError("No active run to complete.")

        self._run.status       = RunStatus.COMPLETE
        self._run.completed_at = datetime.now(UTC)

        # Synthetic material outcomes based on final process state
        if self._run.readings:
            last = self._run.readings[-1]
            self._run.outcome_tensile_gpa  = self._estimate_tensile(last)
            self._run.outcome_modulus_gpa  = self._estimate_modulus(last)
            self._run.outcome_diameter_cv  = self._estimate_diameter_cv()
            self._run.outcome_thermal_c    = self._estimate_thermal(last)

        run = self._run
        self._run = None
        return run

    # ── Internal helpers ────────────────────────────────────────────────

    def _apply_action(self, action: ControlAction) -> None:
        """Apply a control action to the physical models."""
        if action.emergency_stop:
            self.furnace.set_setpoint(self.furnace.config.min_temp_c)
            self._draw_speed_ms = 0.0
            return

        self.furnace.set_setpoint(action.furnace_temp_setpoint_c)
        self._draw_speed_ms = max(0.0, action.draw_speed_setpoint_ms)

    def _noisy(self, value: float, scale: float = 1.0) -> float:
        """Add Gaussian noise scaled by noise_level."""
        sigma = scale * self.noise_level
        return value + random.gauss(0.0, sigma)

    def _estimate_tensile(self, reading: SensorReading) -> float:
        """
        Estimate tensile strength from process conditions.
        Empirical relationship: higher temp + finer diameter → stronger fiber.
        Valid range: 2.4–3.4 GPa for basalt.
        """
        temp_factor  = (reading.furnace_temp_c  - 1420.0) / 140.0  # 0–1
        diam_factor  = 1.0 - (reading.fiber_diameter_um - 8.0) / 15.0
        base         = 2.6 + 0.6 * temp_factor * 0.5 + 0.4 * diam_factor * 0.5
        return round(self._noisy(base, scale=0.04), 3)

    def _estimate_modulus(self, reading: SensorReading) -> float:
        """Estimate elastic modulus. Range: 75–95 GPa for basalt."""
        temp_factor = (reading.furnace_temp_c - 1420.0) / 140.0
        base        = 80.0 + 10.0 * temp_factor
        return round(self._noisy(base, scale=1.0), 2)

    def _estimate_diameter_cv(self) -> float:
        """Estimate diameter coefficient of variation from run stability."""
        if not self._run or len(self._run.readings) < 10:
            return 3.0
        diameters = [r.fiber_diameter_um for r in self._run.readings[-100:]]
        mean = sum(diameters) / len(diameters)
        if mean == 0:
            return 5.0
        variance = sum((d - mean) ** 2 for d in diameters) / len(diameters)
        return round(math.sqrt(variance) / mean * 100.0, 2)

    def _estimate_thermal(self, reading: SensorReading) -> float:
        """Estimate thermal stability temperature. Range: 600–800°C."""
        temp_factor = (reading.furnace_temp_c - 1420.0) / 140.0
        base        = 630.0 + 120.0 * temp_factor
        return round(self._noisy(base, scale=8.0), 1)
