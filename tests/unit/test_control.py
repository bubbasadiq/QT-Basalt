"""
Tests for the adaptive control layer.

Covers safety monitor, recipe loader, controller state machine,
and full closed-loop integration.
"""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock

import pytest

from qutlas.schema import DataSource, FiberClass
from qutlas.data_pipeline.sync    import SyncedReading
from qutlas.models.predictor      import PropertyPredictionResult
from qutlas.control.types         import ControlConfig, ControlState, AdjustmentReason
from qutlas.control.safety        import SafetyMonitor
from qutlas.control.recipe_loader import RecipeLoader
from qutlas.control.controller    import AdaptiveController


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_synced(
    temp:  float = 1480.0,
    diam:  float = 13.0,
    speed: float = 12.0,
) -> SyncedReading:
    s = SyncedReading(
        bin_timestamp       = datetime.now(UTC),
        source              = DataSource.SIMULATOR,
        furnace_temp_c      = temp,
        melt_viscosity_cp   = 750.0,
        melt_flow_rate      = 120.0,
        fiber_diameter_um   = diam,
        draw_speed_ms       = speed,
        draw_tension_n      = 0.05,
        cooling_zone_temp_c = 520.0,
        airflow_rate_lpm    = 50.0,
        complete            = True,
    )
    return s


def make_prediction(
    tensile:    float = 2.9,
    modulus:    float = 85.0,
    thermal:    float = 650.0,
    cv:         float = 1.5,
    confidence: float = 0.85,
    within_tol: bool  = False,
) -> PropertyPredictionResult:
    return PropertyPredictionResult(
        model_version        = "test",
        predicted_at         = datetime.now(UTC),
        tensile_strength_gpa = tensile,
        elastic_modulus_gpa  = modulus,
        elongation_pct       = 2.5,
        thermal_stability_c  = thermal,
        diameter_cv_pct      = cv,
        defect_probability   = 0.02,
        confidence           = confidence,
        feature_complete     = True,
        within_tolerance     = within_tol,
    )


# ── SafetyMonitor ─────────────────────────────────────────────────────────────

class TestSafetyMonitor:

    def setup_method(self) -> None:
        self.monitor = SafetyMonitor()

    def test_valid_reading_is_safe(self) -> None:
        status = self.monitor.check_reading(make_synced())
        assert status.safe

    def test_over_temperature_is_breach(self) -> None:
        reading = make_synced(temp=1650.0)   # above 1600°C limit
        status  = self.monitor.check_reading(reading)
        assert not status.safe
        assert status.emergency_stop

    def test_under_temperature_is_breach(self) -> None:
        reading = make_synced(temp=1200.0)   # below 1350°C limit
        status  = self.monitor.check_reading(reading)
        assert not status.safe

    def test_critically_low_diameter_is_breach(self) -> None:
        reading = make_synced(diam=0.3)   # below 1µm threshold
        status  = self.monitor.check_reading(reading)
        assert not status.safe

    def test_breach_message_is_descriptive(self) -> None:
        reading = make_synced(temp=1650.0)
        status  = self.monitor.check_reading(reading)
        assert len(status.breaches) > 0
        assert "1650" in status.breaches[0]

    def test_setpoint_clamped_above_safety_max(self) -> None:
        from qutlas.control.types import ParameterSetpoint
        sp = ParameterSetpoint(furnace_temp_c=1700.0, draw_speed_ms=12.0, airflow_lpm=50.0)
        safe, status = self.monitor.clamp_setpoint(sp)
        assert safe.furnace_temp_c <= 1600.0
        assert status.any_clamped

    def test_setpoint_clamped_below_safety_min(self) -> None:
        from qutlas.control.types import ParameterSetpoint
        sp = ParameterSetpoint(furnace_temp_c=1000.0, draw_speed_ms=12.0, airflow_lpm=50.0)
        safe, status = self.monitor.clamp_setpoint(sp)
        assert safe.furnace_temp_c >= 1350.0

    def test_recipe_limit_more_restrictive_than_safety(self) -> None:
        from qutlas.control.types import ParameterSetpoint
        sp = ParameterSetpoint(furnace_temp_c=1580.0, draw_speed_ms=12.0, airflow_lpm=50.0)
        safe, _ = self.monitor.clamp_setpoint(sp, recipe_max_temp=1540.0)
        assert safe.furnace_temp_c <= 1540.0

    def test_nan_setpoint_is_clamped_to_safe_value(self) -> None:
        import math
        from qutlas.control.types import ParameterSetpoint
        sp = ParameterSetpoint(furnace_temp_c=float('nan'), draw_speed_ms=12.0, airflow_lpm=50.0)
        safe, status = self.monitor.clamp_setpoint(sp)
        assert math.isfinite(safe.furnace_temp_c)
        assert status.any_clamped

    def test_stats_track_breaches(self) -> None:
        self.monitor.check_reading(make_synced(temp=1700.0))
        assert self.monitor.stats["breach_count"] > 0


# ── RecipeLoader ──────────────────────────────────────────────────────────────

class TestRecipeLoader:

    def setup_method(self) -> None:
        self.loader = RecipeLoader()

    def test_loads_structural_recipe(self) -> None:
        recipe = self.loader.load("structural")
        assert recipe.name == "structural"
        assert recipe.fiber_class == FiberClass.STRUCTURAL
        assert recipe.target_tensile_gpa == pytest.approx(2.9)

    def test_loads_all_five_recipes(self) -> None:
        for name in ["structural", "high_temperature", "electrical_insulation",
                     "corrosion_resistant", "precision_structural"]:
            recipe = self.loader.load(name)
            assert recipe.name == name

    def test_unknown_recipe_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            self.loader.load("nonexistent_recipe")

    def test_list_available_includes_all_defaults(self) -> None:
        available = self.loader.list_available()
        assert "structural" in available
        assert "high_temperature" in available

    def test_recipe_cached_on_second_load(self) -> None:
        r1 = self.loader.load("structural")
        r2 = self.loader.load("structural")
        assert r1 is r2   # same object — cached

    def test_recipe_tolerances_are_positive(self) -> None:
        recipe = self.loader.load("structural")
        assert recipe.tol_tensile_gpa  > 0
        assert recipe.tol_modulus_gpa  > 0
        assert recipe.tol_diameter_um  > 0
        assert recipe.tol_thermal_c    > 0

    def test_recipe_limits_are_consistent(self) -> None:
        recipe = self.loader.load("structural")
        assert recipe.min_temp_c < recipe.initial_temp_c < recipe.max_temp_c


# ── AdaptiveController ───────────────────────────────────────────────────────

class TestAdaptiveController:

    def setup_method(self) -> None:
        self.controller = AdaptiveController()

    def test_initial_state_is_idle(self) -> None:
        assert self.controller.state == ControlState.IDLE

    def test_activate_recipe_transitions_to_warming(self) -> None:
        self.controller.activate_recipe("structural")
        assert self.controller.state == ControlState.WARMING

    def test_on_prediction_ignored_when_idle(self) -> None:
        callback = MagicMock()
        self.controller.on_decision(callback)
        self.controller.on_prediction(make_prediction())
        callback.assert_not_called()

    def test_warming_state_emits_hold_decision(self) -> None:
        decisions: list = []
        self.controller.on_decision(decisions.append)
        self.controller.activate_recipe("structural")
        self.controller.on_prediction(make_prediction())
        assert len(decisions) == 1
        assert AdjustmentReason.WARMING in decisions[0].reasons

    def test_transitions_to_converging_when_warm(self) -> None:
        self.controller.activate_recipe("structural")
        # Simulate furnace reaching initial temperature
        warm_reading = make_synced(temp=1480.0)   # within 15°C of 1480
        self.controller.on_reading(warm_reading)
        assert self.controller.state == ControlState.CONVERGING

    def test_low_confidence_triggers_hold(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1480.0))  # warm up
        decisions: list = []
        self.controller.on_decision(decisions.append)
        self.controller.on_prediction(make_prediction(confidence=0.1))
        assert AdjustmentReason.CONFIDENCE_LOW in decisions[-1].reasons

    def test_setpoint_stays_within_recipe_limits(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1480.0))
        recipe = RecipeLoader().load("structural")
        for _ in range(20):
            self.controller.on_prediction(make_prediction())
        sp = self.controller.setpoint
        assert recipe.min_temp_c <= sp.furnace_temp_c <= recipe.max_temp_c
        assert 0.5 <= sp.draw_speed_ms <= recipe.max_draw_speed_ms

    def test_transitions_to_stable_after_consecutive_tolerance(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1480.0))
        cfg = self.controller.config
        for _ in range(cfg.stable_predictions_needed + 1):
            self.controller.on_prediction(make_prediction(within_tol=True))
        assert self.controller.state == ControlState.STABLE

    def test_stable_state_reverts_to_converging_on_drift(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1480.0))
        cfg = self.controller.config
        # First reach stable
        for _ in range(cfg.stable_predictions_needed + 1):
            self.controller.on_prediction(make_prediction(within_tol=True))
        assert self.controller.state == ControlState.STABLE
        # Now drift out of tolerance
        self.controller.on_prediction(make_prediction(within_tol=False))
        assert self.controller.state == ControlState.CONVERGING

    def test_safety_breach_triggers_abort(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1700.0))   # over limit
        assert self.controller.state == ControlState.ABORTED

    def test_emergency_stop_decision_has_correct_reason(self) -> None:
        decisions: list = []
        self.controller.on_decision(decisions.append)
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1700.0))
        assert any(
            AdjustmentReason.EMERGENCY_STOP in d.reasons
            for d in decisions
        )

    def test_abort_state_ignores_further_predictions(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1700.0))   # abort
        decisions_before = self.controller.stats["total_decisions"]
        self.controller.on_prediction(make_prediction())
        assert self.controller.stats["total_decisions"] == decisions_before

    def test_manual_reset_from_aborted(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1700.0))
        assert self.controller.state == ControlState.ABORTED
        self.controller.reset_after_abort()
        assert self.controller.state == ControlState.IDLE

    def test_deactivate_returns_to_idle(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.deactivate()
        assert self.controller.state == ControlState.IDLE

    def test_stats_dict_has_expected_keys(self) -> None:
        stats = self.controller.stats
        for key in ["state", "active_recipe", "total_decisions",
                    "current_temp_sp", "current_speed_sp", "safety"]:
            assert key in stats

    def test_decision_summary_is_non_empty(self) -> None:
        self.controller.activate_recipe("structural")
        self.controller.on_reading(make_synced(temp=1480.0))
        self.controller.on_prediction(make_prediction())
        assert self.controller.latest_decision is not None
        assert len(self.controller.latest_decision.summary()) > 0


# ── Full closed-loop integration ──────────────────────────────────────────────

class TestClosedLoopIntegration:

    def test_full_closed_loop_converges(self) -> None:
        """
        Full stack integration: simulator → pipeline → engine → controller.
        Verifies the closed loop runs without error and produces decisions.
        """
        from qutlas.data_pipeline import DataPipeline
        from qutlas.models.engine import MaterialsEngine
        from qutlas.simulation.process import ProcessSimulator
        from qutlas.simulation.runner import DEFAULT_RECIPES

        recipe     = DEFAULT_RECIPES["structural"]
        sim        = ProcessSimulator(noise_level=0.01)
        pipeline   = DataPipeline()
        engine     = MaterialsEngine(window_size=50, predict_every=5)
        controller = AdaptiveController()

        # Wire everything together
        pipeline.on_synced(engine.on_reading)
        pipeline.on_synced(controller.on_reading)
        engine.on_prediction(controller.on_prediction)
        engine.set_recipe(recipe)

        decisions: list = []
        controller.on_decision(decisions.append)

        pipeline.start()
        controller.activate_recipe("structural")
        sim.start_run(recipe)

        last_reading = None
        for _ in range(120):
            # Feed control decision back to simulator
            if controller.latest_decision:
                sp = controller.latest_decision.setpoint
                from qutlas.schema import ControlAction
                action = ControlAction(
                    timestamp                = datetime.now(UTC),
                    run_id                   = "",
                    furnace_temp_setpoint_c  = sp.furnace_temp_c,
                    draw_speed_setpoint_ms   = sp.draw_speed_ms,
                    cooling_airflow_setpoint = sp.airflow_lpm,
                )
                last_reading = sim.step(action)
            else:
                last_reading = sim.step()
            pipeline.ingest(last_reading)

        pipeline.stop()

        # Controller should have produced decisions
        assert len(decisions) > 0
        # System should not have aborted
        assert controller.state != ControlState.ABORTED
        # All setpoints should be within recipe limits
        sp = controller.setpoint
        assert recipe.min_temp_c  <= sp.furnace_temp_c <= recipe.max_temp_c
        assert 0.5               <= sp.draw_speed_ms  <= recipe.max_draw_speed_ms
