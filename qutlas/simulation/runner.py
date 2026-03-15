"""
Simulation Runner

Orchestrates a complete simulated production run:
  1. Loads a fiber recipe
  2. Starts the process simulator
  3. Runs a basic proportional controller to chase the recipe targets
  4. Exports the run record for training data

Run via:
    python -m qutlas.simulation.runner
    qutlas simulate --recipe structural --duration 120
"""

from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import Optional

from rich.console import Console
from rich.live    import Live
from rich.table   import Table
from rich.text    import Text

from qutlas.schema            import ControlAction, FiberClass, FiberRecipe
from qutlas.simulation.process import ProcessSimulator

console = Console()

# Built-in default recipes keyed by short name
DEFAULT_RECIPES: dict[str, FiberRecipe] = {
    "structural": FiberRecipe(
        name                  = "structural",
        fiber_class           = FiberClass.STRUCTURAL,
        description           = "Structural reinforcement fiber",
        target_tensile_gpa    = 2.9,
        target_modulus_gpa    = 85.0,
        target_diameter_um    = 13.0,
        target_thermal_c      = 650.0,
        initial_temp_c        = 1480.0,
        initial_draw_speed_ms = 12.0,
        initial_airflow_lpm   = 48.0,
    ),
    "high_temperature": FiberRecipe(
        name                  = "high_temperature",
        fiber_class           = FiberClass.HIGH_TEMPERATURE,
        description           = "High temperature insulation fiber",
        target_tensile_gpa    = 2.5,
        target_modulus_gpa    = 78.0,
        target_diameter_um    = 11.0,
        target_thermal_c      = 760.0,
        initial_temp_c        = 1540.0,
        initial_draw_speed_ms = 9.0,
        initial_airflow_lpm   = 35.0,
    ),
}


class SimpleController:
    """
    Proportional controller that adjusts furnace temperature and draw speed
    to converge toward recipe targets.

    This is the Phase 1 baseline controller — a placeholder that will be
    replaced by the AMI Labs world model planner in Phase 3.
    """

    def __init__(self, recipe: FiberRecipe) -> None:
        self.recipe = recipe
        self._temp_setpoint   = recipe.initial_temp_c
        self._speed_setpoint  = recipe.initial_draw_speed_ms

    def decide(self, reading: "SensorReading") -> ControlAction:  # type: ignore[name-defined]
        """
        Compute a control action from the current sensor reading.

        Proportional adjustment: if fiber is thicker than target,
        increase temperature (lower viscosity → finer fiber) or
        increase draw speed.
        """
        from qutlas.schema import ControlAction

        r = self.recipe

        # Diameter error → temperature adjustment
        diam_error = reading.fiber_diameter_um - r.target_diameter_um
        self._temp_setpoint  += diam_error * 0.4   # °C per µm error
        self._speed_setpoint -= diam_error * 0.08  # m/s per µm error

        # Clamp to recipe limits
        self._temp_setpoint  = max(r.min_temp_c,  min(r.max_temp_c,  self._temp_setpoint))
        self._speed_setpoint = max(1.0,            min(r.max_draw_speed_ms, self._speed_setpoint))

        return ControlAction(
            timestamp                  = datetime.now(UTC),
            run_id                     = "",
            furnace_temp_setpoint_c    = self._temp_setpoint,
            furnace_ramp_rate_c_min    = 5.0,
            draw_speed_setpoint_ms     = self._speed_setpoint,
            cooling_airflow_setpoint   = r.initial_airflow_lpm,
        )


class SimulationRunner:
    """
    Runs a complete simulated production run and displays live metrics.
    """

    def __init__(
        self,
        recipe:   str  = "structural",
        duration: int  = 60,
        noise:    float = 0.02,
    ) -> None:
        self.recipe_name = recipe
        self.duration    = duration
        self.noise       = noise

    def run(self) -> None:
        """Execute the simulation and print live metrics to the terminal."""
        recipe = DEFAULT_RECIPES.get(self.recipe_name)
        if recipe is None:
            console.print(f"[red]Unknown recipe: {self.recipe_name}[/red]")
            console.print(f"Available: {list(DEFAULT_RECIPES.keys())}")
            return

        sim        = ProcessSimulator(noise_level=self.noise)
        controller = SimpleController(recipe)
        run        = sim.start_run(recipe)

        dt          = 0.1   # 100ms timestep for readable output
        steps       = int(self.duration / dt)
        report_every = max(1, steps // 50)   # ~50 status updates

        console.print(f"\n[bold]Recipe:[/bold] {recipe.name}")
        console.print(f"[bold]Target diameter:[/bold] {recipe.target_diameter_um} µm")
        console.print(f"[bold]Target tensile:[/bold]  {recipe.target_tensile_gpa} GPa")
        console.print()

        last_reading = None
        t_start = time.monotonic()

        for i in range(steps):
            action       = controller.decide(last_reading) if last_reading else None
            last_reading = sim.step(action)

            if i % report_every == 0:
                elapsed = time.monotonic() - t_start
                pct     = int((i / steps) * 100)
                self._print_row(i * dt, elapsed, last_reading, recipe, pct)

        completed_run = sim.complete_run()
        self._print_summary(completed_run)

    def _print_row(
        self,
        sim_time:  float,
        wall_time: float,
        reading:   "SensorReading",  # type: ignore[name-defined]
        recipe:    FiberRecipe,
        pct:       int,
    ) -> None:
        diam_err = reading.fiber_diameter_um - recipe.target_diameter_um
        err_color = "green" if abs(diam_err) < recipe.tol_diameter_um else "yellow"

        console.print(
            f"[dim]{sim_time:6.1f}s[/dim]  "
            f"T={reading.furnace_temp_c:7.1f}°C  "
            f"visc={reading.melt_viscosity_cp or 0:6.0f}cP  "
            f"d=[{err_color}]{reading.fiber_diameter_um:5.1f}µm[/{err_color}]  "
            f"err=[{err_color}]{diam_err:+.2f}[/{err_color}]  "
            f"[dim]{pct}%[/dim]"
        )

    def _print_summary(self, run: "ProductionRun") -> None:  # type: ignore[name-defined]
        console.print()
        console.print("[bold green]Run complete[/bold green]")
        console.print(f"  Duration    : {run.duration_seconds:.1f}s")
        console.print(f"  Timesteps   : {run.timestep_count}")
        console.print(f"  Tensile     : {run.outcome_tensile_gpa:.3f} GPa")
        console.print(f"  Modulus     : {run.outcome_modulus_gpa:.1f} GPa")
        console.print(f"  Diameter CV : {run.outcome_diameter_cv:.2f}%")
        console.print(f"  Thermal     : {run.outcome_thermal_c:.0f}°C")
        console.print(f"  Run ID      : [dim]{run.run_id}[/dim]")


if __name__ == "__main__":
    SimulationRunner(recipe="structural", duration=60, noise=0.02).run()
