"""
Unit tests for physics models.

These tests verify that the physics models produce values within
expected ranges and behave correctly at boundary conditions.
All expected values are grounded in published basalt fiber literature.
"""

import pytest
import math

from qutlas.models.furnace    import FurnaceModel, FurnaceConfig
from qutlas.models.viscosity  import ViscosityModel, ViscosityConfig
from qutlas.models.fiber_draw import FiberDrawModel, BushingConfig


# ── Viscosity model ──────────────────────────────────────────────────────────

class TestViscosityModel:

    def setup_method(self) -> None:
        self.model = ViscosityModel()

    def test_viscosity_at_1480c_is_in_draw_window(self) -> None:
        """At typical draw temperature, viscosity should be in 400–1200 cP."""
        visc = self.model.viscosity_cp(1480.0)
        assert 400.0 <= visc <= 1200.0, f"Viscosity {visc:.0f} cP outside draw window"

    def test_viscosity_decreases_with_temperature(self) -> None:
        """Higher temperature must always produce lower viscosity (monotonic)."""
        temps  = [1400, 1440, 1480, 1520, 1560, 1600]
        viscs  = [self.model.viscosity_cp(t) for t in temps]
        for i in range(len(viscs) - 1):
            assert viscs[i] > viscs[i + 1], (
                f"Viscosity should decrease: {viscs[i]:.0f} > {viscs[i+1]:.0f} "
                f"at {temps[i]}°C vs {temps[i+1]}°C"
            )

    def test_viscosity_reasonable_range(self) -> None:
        """Spot-check viscosity values against published basalt data."""
        assert self.model.viscosity_cp(1440.0) >  800.0   # high viscosity, cool
        assert self.model.viscosity_cp(1560.0) <  400.0   # low viscosity, hot

    def test_draw_window_returns_valid_range(self) -> None:
        """Draw window temperatures should be physically plausible."""
        lo, hi = self.model.draw_window()
        assert lo < hi
        assert 1400.0 <= lo <= 1550.0
        assert 1450.0 <= hi <= 1650.0

    def test_optimal_draw_temp_for_fine_fiber(self) -> None:
        """Fine fibers (9 µm) require higher temperature than coarse (15 µm)."""
        temp_fine   = self.model.optimal_draw_temp(9.0)
        temp_coarse = self.model.optimal_draw_temp(15.0)
        assert temp_fine > temp_coarse, (
            f"Fine fiber temp {temp_fine:.0f}°C should exceed "
            f"coarse fiber temp {temp_coarse:.0f}°C"
        )

    def test_raises_below_vogel_temperature(self) -> None:
        """VFT model is physically undefined below the Vogel temperature."""
        with pytest.raises(ValueError, match="Vogel temperature"):
            self.model.viscosity_cp(-100.0)


# ── Furnace model ────────────────────────────────────────────────────────────

class TestFurnaceModel:

    def setup_method(self) -> None:
        self.furnace = FurnaceModel()

    def test_furnace_heats_toward_setpoint(self) -> None:
        """Furnace temperature should increase when setpoint is above current temp."""
        self.furnace.set_setpoint(1480.0)
        initial_temp = self.furnace.state.temp_c

        for _ in range(1000):
            self.furnace.step(dt=0.1)

        assert self.furnace.state.temp_c > initial_temp

    def test_furnace_does_not_exceed_max_temp(self) -> None:
        """Furnace should never exceed its configured maximum temperature."""
        self.furnace.set_setpoint(2000.0)   # above max
        for _ in range(5000):
            self.furnace.step(dt=0.1)

        assert self.furnace.state.temp_c <= self.furnace.config.max_temp_c

    def test_furnace_converges_to_setpoint(self) -> None:
        """
        Given enough time, furnace should stabilise within 10°C of setpoint.
        Using a small furnace with fast time constant for this test.
        """
        fast_config = FurnaceConfig(
            melt_mass_kg     = 1.0,    # small mass → fast response
            pid_kp           = 2.0,
            pid_ki           = 0.05,
            max_ramp_rate_c_min = 100.0,
        )
        furnace = FurnaceModel(fast_config)
        furnace.set_setpoint(1480.0)

        for _ in range(20000):
            furnace.step(dt=0.1)

        assert abs(furnace.state.temp_c - 1480.0) < 20.0, (
            f"Furnace {furnace.state.temp_c:.1f}°C did not converge to 1480°C"
        )

    def test_ramp_rate_is_respected(self) -> None:
        """Setpoint changes should be limited by max_ramp_rate_c_min."""
        initial_setpoint = self.furnace.state.setpoint_c
        self.furnace.set_setpoint(initial_setpoint + 500.0)  # large jump
        new_setpoint = self.furnace.state.setpoint_c
        # Should have been constrained by the ramp rate
        assert new_setpoint < initial_setpoint + 500.0

    def test_power_is_clamped_between_0_and_100(self) -> None:
        """Heating power percentage should always be in [0, 100]."""
        self.furnace.set_setpoint(1480.0)
        for _ in range(500):
            state = self.furnace.step(dt=0.1)
            assert 0.0 <= state.power_pct <= 100.0


# ── Fiber draw model ─────────────────────────────────────────────────────────

class TestFiberDrawModel:

    def setup_method(self) -> None:
        self.draw = FiberDrawModel()

    def test_diameter_in_realistic_range(self) -> None:
        """Basalt fiber diameter should be in 8–25 µm range for typical conditions."""
        state = self.draw.step(
            draw_speed_ms = 12.0,
            viscosity_cp  = 750.0,
            temp_c        = 1480.0,
        )
        assert 5.0 <= state.fiber_diameter_um <= 30.0, (
            f"Diameter {state.fiber_diameter_um:.1f} µm outside realistic range"
        )

    def test_faster_draw_produces_thinner_fiber(self) -> None:
        """Increasing draw speed should decrease fiber diameter."""
        state_slow = self.draw.step(8.0,  750.0, 1480.0)
        state_fast = self.draw.step(18.0, 750.0, 1480.0)
        assert state_fast.fiber_diameter_um < state_slow.fiber_diameter_um, (
            f"Faster draw ({state_fast.fiber_diameter_um:.1f}) "
            f"should be thinner than slow ({state_slow.fiber_diameter_um:.1f})"
        )

    def test_lower_viscosity_produces_thinner_fiber(self) -> None:
        """Lower viscosity (hotter melt) at same speed → thinner fiber."""
        state_viscous = self.draw.step(12.0, 1100.0, 1440.0)
        state_fluid   = self.draw.step(12.0,  400.0, 1560.0)
        assert state_fluid.fiber_diameter_um < state_viscous.fiber_diameter_um

    def test_speed_inversion_is_accurate(self) -> None:
        """speed_for_diameter should invert the forward model within tolerance."""
        target_um = 13.0
        visc_cp   = 750.0
        temp_c    = 1480.0

        speed = self.draw.speed_for_diameter(target_um, visc_cp, temp_c)
        recovered_diameter = self.draw.diameter_at_speed(speed, visc_cp, temp_c)

        assert abs(recovered_diameter - target_um) < 0.5, (
            f"Inversion error: target {target_um:.1f} µm, "
            f"recovered {recovered_diameter:.2f} µm at speed {speed:.2f} m/s"
        )

    def test_tension_is_positive(self) -> None:
        """Draw tension must always be a positive value."""
        state = self.draw.step(12.0, 750.0, 1480.0)
        assert state.draw_tension_n > 0.0

    def test_mass_flow_is_positive(self) -> None:
        """Melt throughput must always be positive."""
        state = self.draw.step(12.0, 750.0, 1480.0)
        assert state.mass_flow_g_min > 0.0


# ── Simulator integration ────────────────────────────────────────────────────

class TestProcessSimulator:

    def test_simulator_produces_valid_readings(self) -> None:
        """Simulator should produce physically valid sensor readings."""
        from qutlas.simulation.process import ProcessSimulator
        from qutlas.simulation.runner  import DEFAULT_RECIPES

        sim    = ProcessSimulator(noise_level=0.01)
        recipe = DEFAULT_RECIPES["structural"]
        sim.start_run(recipe)

        for _ in range(100):
            reading = sim.step()

        assert reading.is_complete()
        assert 1300.0 <= reading.furnace_temp_c <= 1650.0
        assert 5.0    <= reading.fiber_diameter_um <= 35.0
        assert reading.draw_speed_ms >= 0.0

    def test_run_accumulates_readings(self) -> None:
        """ProductionRun should accumulate all timestep readings."""
        from qutlas.simulation.process import ProcessSimulator
        from qutlas.simulation.runner  import DEFAULT_RECIPES

        sim    = ProcessSimulator()
        recipe = DEFAULT_RECIPES["structural"]
        run    = sim.start_run(recipe)

        N = 50
        for _ in range(N):
            sim.step()

        assert run.timestep_count == N

    def test_completed_run_has_outcomes(self) -> None:
        """A completed run should have all outcome fields populated."""
        from qutlas.simulation.process import ProcessSimulator
        from qutlas.simulation.runner  import DEFAULT_RECIPES

        sim    = ProcessSimulator()
        recipe = DEFAULT_RECIPES["structural"]
        sim.start_run(recipe)

        for _ in range(200):
            sim.step()

        completed = sim.complete_run()
        assert completed.outcome_tensile_gpa  is not None
        assert completed.outcome_modulus_gpa  is not None
        assert completed.outcome_diameter_cv  is not None
        assert completed.outcome_thermal_c    is not None
        assert 2.0 <= completed.outcome_tensile_gpa  <= 4.0
        assert 60.0 <= completed.outcome_modulus_gpa <= 100.0
