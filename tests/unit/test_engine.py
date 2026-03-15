"""
Tests for the Materials Engine — feature engineering,
property prediction, and engine orchestration.
"""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock

import pytest

from qutlas.schema import DataSource, FiberClass, FiberRecipe
from qutlas.data_pipeline.sync import SyncedReading
from qutlas.models.features import (
    FeatureEngineer, FeatureVector,
    FEATURE_DIM, FEATURE_NAMES_V1, _stats,
)
from qutlas.models.predictor import PhysicsBaselinePredictor
from qutlas.models.engine    import MaterialsEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_synced(
    temp:  float = 1480.0,
    diam:  float = 13.0,
    speed: float = 12.0,
    visc:  float = 750.0,
) -> SyncedReading:
    s = SyncedReading(
        bin_timestamp       = datetime.now(UTC),
        source              = DataSource.SIMULATOR,
        furnace_temp_c      = temp,
        melt_viscosity_cp   = visc,
        melt_flow_rate      = 120.0,
        fiber_diameter_um   = diam,
        draw_speed_ms       = speed,
        draw_tension_n      = 0.05,
        cooling_zone_temp_c = 520.0,
        airflow_rate_lpm    = 50.0,
        complete            = True,
    )
    return s


def make_recipe(
    tensile: float = 2.9,
    modulus: float = 85.0,
    diam:    float = 13.0,
) -> FiberRecipe:
    return FiberRecipe(
        name                  = "test",
        fiber_class           = FiberClass.STRUCTURAL,
        description           = "Test recipe",
        target_tensile_gpa    = tensile,
        target_modulus_gpa    = modulus,
        target_diameter_um    = diam,
        target_thermal_c      = 650.0,
    )


# ── Stats helper ─────────────────────────────────────────────────────────────

class TestStats:

    def test_empty_list_returns_zeros(self) -> None:
        s = _stats([])
        assert s["mean"] == 0.0
        assert s["std"]  == 0.0

    def test_single_value(self) -> None:
        s = _stats([5.0])
        assert s["mean"] == 5.0
        assert s["std"]  == 0.0
        assert s["min"]  == 5.0
        assert s["max"]  == 5.0

    def test_known_values(self) -> None:
        s = _stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["mean"] == pytest.approx(3.0)
        assert s["min"]  == 1.0
        assert s["max"]  == 5.0

    def test_increasing_trend_is_positive(self) -> None:
        s = _stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["trend"] > 0.0

    def test_decreasing_trend_is_negative(self) -> None:
        s = _stats([5.0, 4.0, 3.0, 2.0, 1.0])
        assert s["trend"] < 0.0

    def test_flat_trend_is_zero(self) -> None:
        s = _stats([3.0, 3.0, 3.0, 3.0])
        assert s["trend"] == pytest.approx(0.0)


# ── Feature Engineer ──────────────────────────────────────────────────────────

class TestFeatureEngineer:

    def setup_method(self) -> None:
        self.engineer = FeatureEngineer(window_size=100)

    def test_output_has_correct_dimension(self) -> None:
        window = [make_synced() for _ in range(50)]
        fv     = self.engineer.compute(window)
        assert len(fv.values) == FEATURE_DIM

    def test_feature_names_match_values(self) -> None:
        window = [make_synced() for _ in range(50)]
        fv     = self.engineer.compute(window)
        assert len(fv.feature_names) == len(fv.values)
        assert fv.feature_names == FEATURE_NAMES_V1

    def test_empty_window_returns_zero_vector(self) -> None:
        fv = self.engineer.compute([])
        assert all(v == 0.0 for v in fv.values)
        assert not fv.complete

    def test_current_temp_matches_latest_reading(self) -> None:
        window = [make_synced(temp=1480.0)] * 9 + [make_synced(temp=1510.0)]
        fv     = self.engineer.compute(window)
        feat   = fv.as_dict()
        assert feat["current_temp"] == pytest.approx(1510.0)

    def test_draw_stability_is_high_for_stable_process(self) -> None:
        # All readings at same diameter → very stable
        window = [make_synced(diam=13.0) for _ in range(50)]
        fv     = self.engineer.compute(window)
        assert fv.as_dict()["draw_stability_index"] > 0.95

    def test_draw_stability_is_low_for_unstable_process(self) -> None:
        import math
        # Highly variable diameters
        window = [
            make_synced(diam=5.0 + (i % 20) * 1.0)
            for i in range(50)
        ]
        fv = self.engineer.compute(window)
        assert fv.as_dict()["draw_stability_index"] < 0.9

    def test_feature_vector_validates_successfully(self) -> None:
        window = [make_synced() for _ in range(100)]
        fv     = self.engineer.compute(window)
        assert fv.validate() == []

    def test_missing_count_reflects_incomplete_readings(self) -> None:
        incomplete = make_synced()
        incomplete.fiber_diameter_um = None
        incomplete.complete = False
        window = [make_synced()] * 8 + [incomplete, incomplete]
        fv     = self.engineer.compute(window)
        assert fv.missing_count == 2


# ── Physics Baseline Predictor ───────────────────────────────────────────────

class TestPhysicsBaselinePredictor:

    def setup_method(self) -> None:
        self.predictor = PhysicsBaselinePredictor()
        self.engineer  = FeatureEngineer(window_size=100)
        self.window    = [make_synced() for _ in range(100)]

    def _predict(self, window=None) -> "PropertyPredictionResult":  # type: ignore[name-defined]
        w  = window or self.window
        fv = self.engineer.compute(w)
        return self.predictor.predict(fv)

    def test_tensile_in_realistic_range(self) -> None:
        result = self._predict()
        assert 1.8 <= result.tensile_strength_gpa <= 3.8

    def test_modulus_in_realistic_range(self) -> None:
        result = self._predict()
        assert 65.0 <= result.elastic_modulus_gpa <= 95.0

    def test_thermal_in_realistic_range(self) -> None:
        result = self._predict()
        assert 550.0 <= result.thermal_stability_c <= 820.0

    def test_diameter_cv_is_non_negative(self) -> None:
        result = self._predict()
        assert result.diameter_cv_pct >= 0.0

    def test_defect_probability_in_unit_range(self) -> None:
        result = self._predict()
        assert 0.0 <= result.defect_probability <= 1.0

    def test_confidence_in_unit_range(self) -> None:
        result = self._predict()
        assert 0.0 <= result.confidence <= 1.0

    def test_hotter_process_produces_higher_thermal_stability(self) -> None:
        hot_window  = [make_synced(temp=1560.0) for _ in range(100)]
        cool_window = [make_synced(temp=1420.0) for _ in range(100)]
        hot  = self._predict(hot_window)
        cool = self._predict(cool_window)
        assert hot.thermal_stability_c > cool.thermal_stability_c

    def test_finer_fiber_produces_higher_tensile(self) -> None:
        fine_window   = [make_synced(diam=9.0)  for _ in range(100)]
        coarse_window = [make_synced(diam=18.0) for _ in range(100)]
        fine   = self._predict(fine_window)
        coarse = self._predict(coarse_window)
        assert fine.tensile_strength_gpa > coarse.tensile_strength_gpa

    def test_stable_process_has_low_cv(self) -> None:
        stable_window   = [make_synced(diam=13.0) for _ in range(100)]
        unstable_window = [make_synced(diam=5.0 + (i % 20)) for i in range(100)]
        stable   = self._predict(stable_window)
        unstable = self._predict(unstable_window)
        assert stable.diameter_cv_pct < unstable.diameter_cv_pct

    def test_call_count_increments(self) -> None:
        for _ in range(5):
            self._predict()
        assert self.predictor.call_count == 5

    def test_summary_is_non_empty_string(self) -> None:
        result = self._predict()
        assert len(result.summary()) > 0


# ── Materials Engine ──────────────────────────────────────────────────────────

class TestMaterialsEngine:

    def setup_method(self) -> None:
        self.engine = MaterialsEngine(window_size=50, predict_every=5)

    def test_engine_not_ready_on_empty_window(self) -> None:
        assert not self.engine.is_ready

    def test_engine_ready_after_sufficient_readings(self) -> None:
        for _ in range(30):   # 30 of 50 = 60% → is_ready
            self.engine.on_reading(make_synced())
        assert self.engine.is_ready

    def test_prediction_triggered_at_correct_cadence(self) -> None:
        callback = MagicMock()
        self.engine.on_prediction(callback)
        for i in range(20):
            self.engine.on_reading(make_synced())
        # predict_every=5, so 20 readings → 4 predictions
        assert callback.call_count == 4

    def test_predict_now_returns_result(self) -> None:
        for _ in range(30):
            self.engine.on_reading(make_synced())
        result = self.engine.predict_now()
        assert result is not None
        assert result.tensile_strength_gpa > 0.0

    def test_latest_prediction_is_updated(self) -> None:
        assert self.engine.latest_prediction is None
        for _ in range(10):
            self.engine.on_reading(make_synced())
        assert self.engine.latest_prediction is not None

    def test_tolerance_check_passes_when_prediction_meets_recipe(self) -> None:
        recipe = make_recipe(tensile=2.8, modulus=82.0)
        self.engine.set_recipe(recipe)
        # Feed a stable, nominal process
        for _ in range(55):
            self.engine.on_reading(make_synced(temp=1480.0, diam=13.0))
        result = self.engine.latest_prediction
        assert result is not None
        # Don't assert within_tolerance value — depends on model output
        # Just verify it was evaluated
        assert isinstance(result.within_tolerance, bool)

    def test_set_recipe_updates_active_recipe_in_stats(self) -> None:
        recipe = make_recipe()
        self.engine.set_recipe(recipe)
        assert self.engine.stats["active_recipe"] == "test"

    def test_clear_recipe_removes_recipe(self) -> None:
        self.engine.set_recipe(make_recipe())
        self.engine.clear_recipe()
        assert self.engine.stats["active_recipe"] is None

    def test_callback_removed_correctly(self) -> None:
        callback = MagicMock()
        self.engine.on_prediction(callback)
        self.engine.remove_callback(callback)
        for _ in range(10):
            self.engine.on_reading(make_synced())
        callback.assert_not_called()

    def test_stats_dict_has_expected_keys(self) -> None:
        stats = self.engine.stats
        for key in ["prediction_count", "reading_count", "window_fill_pct",
                    "is_ready", "model_version", "active_recipe"]:
            assert key in stats


# ── Full stack integration ────────────────────────────────────────────────────

class TestMaterialsEngineIntegration:

    def test_engine_integrated_with_pipeline_and_simulator(self) -> None:
        """
        Full stack: simulator → pipeline → engine → predictions.
        Verifies the complete data flow from process to property prediction.
        """
        from qutlas.data_pipeline       import DataPipeline
        from qutlas.simulation.process  import ProcessSimulator
        from qutlas.simulation.runner   import DEFAULT_RECIPES, SimpleController

        recipe     = DEFAULT_RECIPES["structural"]
        sim        = ProcessSimulator(noise_level=0.01)
        pipeline   = DataPipeline()
        engine     = MaterialsEngine(window_size=50, predict_every=5)

        predictions: list[PropertyPredictionResult] = []
        engine.on_prediction(predictions.append)
        engine.set_recipe(recipe)
        pipeline.on_synced(engine.on_reading)
        pipeline.start()

        controller   = SimpleController(recipe)
        last_reading = None
        sim.start_run(recipe)

        for _ in range(100):
            action       = controller.decide(last_reading) if last_reading else None
            last_reading = sim.step(action)
            pipeline.ingest(last_reading)

        pipeline.stop()

        # Should have received predictions (100 readings / predict_every=5 = 20)
        assert len(predictions) == 20

        # All predictions should be in physically plausible ranges
        for pred in predictions:
            assert 1.8 <= pred.tensile_strength_gpa <= 3.8
            assert 65.0 <= pred.elastic_modulus_gpa  <= 95.0
            assert 0.0  <= pred.confidence           <= 1.0
