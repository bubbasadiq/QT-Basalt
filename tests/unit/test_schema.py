"""
Unit tests for the Qutlas data schema.
Verifies that data structures behave correctly and
that validation logic works as expected.
"""

from datetime import datetime, UTC

import pytest

from qutlas.schema import (
    ControlAction,
    DataSource,
    FiberClass,
    FiberRecipe,
    ProductionRun,
    RunStatus,
    SensorReading,
)


class TestSensorReading:

    def _make_reading(self, **kwargs) -> SensorReading:
        defaults = dict(
            timestamp         = datetime.now(UTC),
            source            = DataSource.SIMULATOR,
            furnace_temp_c    = 1480.0,
            melt_viscosity_cp = 750.0,
            melt_flow_rate    = 120.0,
            fiber_diameter_um = 13.0,
            draw_speed_ms     = 12.0,
            draw_tension_n    = 0.05,
            cooling_zone_temp_c = 520.0,
            airflow_rate_lpm  = 50.0,
        )
        defaults.update(kwargs)
        return SensorReading(**defaults)

    def test_is_complete_with_all_critical_fields(self) -> None:
        reading = self._make_reading()
        assert reading.is_complete()

    def test_is_incomplete_without_diameter(self) -> None:
        reading = self._make_reading(fiber_diameter_um=None)
        assert not reading.is_complete()

    def test_is_incomplete_without_temp(self) -> None:
        reading = self._make_reading(furnace_temp_c=None)
        assert not reading.is_complete()


class TestFiberRecipe:

    def _make_recipe(self) -> FiberRecipe:
        return FiberRecipe(
            name                  = "test_recipe",
            fiber_class           = FiberClass.STRUCTURAL,
            description           = "Test recipe",
            target_tensile_gpa    = 2.9,
            target_modulus_gpa    = 85.0,
            target_diameter_um    = 13.0,
            target_thermal_c      = 650.0,
        )

    def test_recipe_is_met_when_within_tolerance(self) -> None:
        from qutlas.schema import PropertyPrediction
        recipe = self._make_recipe()
        pred   = PropertyPrediction(
            timestamp              = datetime.now(UTC),
            run_id                 = "test",
            model_version          = "0.1",
            tensile_strength_gpa   = 2.9,   # exact target
            elastic_modulus_gpa    = 85.0,
            elongation_pct         = 2.5,
            thermal_stability_c    = 650.0,
            dielectric_strength    = None,
            diameter_cv_pct        = 1.5,
            defect_probability     = 0.02,
            prediction_confidence  = 0.95,
        )
        assert recipe.is_met_by(pred)

    def test_recipe_not_met_when_outside_tolerance(self) -> None:
        from qutlas.schema import PropertyPrediction
        recipe = self._make_recipe()
        pred   = PropertyPrediction(
            timestamp              = datetime.now(UTC),
            run_id                 = "test",
            model_version          = "0.1",
            tensile_strength_gpa   = 2.0,   # well below target
            elastic_modulus_gpa    = 85.0,
            elongation_pct         = 2.5,
            thermal_stability_c    = 650.0,
            dielectric_strength    = None,
            diameter_cv_pct        = 1.5,
            defect_probability     = 0.02,
            prediction_confidence  = 0.95,
        )
        assert not recipe.is_met_by(pred)


class TestProductionRun:

    def test_duration_is_none_before_completion(self) -> None:
        run = ProductionRun()
        assert run.duration_seconds is None

    def test_duration_calculated_correctly(self) -> None:
        from datetime import timedelta
        run = ProductionRun()
        run.started_at   = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        run.completed_at = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
        assert run.duration_seconds == 300.0

    def test_timestep_count_reflects_readings(self) -> None:
        run = ProductionRun()
        assert run.timestep_count == 0

        dummy = SensorReading(
            timestamp         = datetime.now(UTC),
            source            = DataSource.SIMULATOR,
            furnace_temp_c    = 1480.0,
            melt_viscosity_cp = 750.0,
            melt_flow_rate    = None,
            fiber_diameter_um = 13.0,
            draw_speed_ms     = 12.0,
            draw_tension_n    = None,
            cooling_zone_temp_c = None,
            airflow_rate_lpm  = None,
        )
        run.readings.append(dummy)
        assert run.timestep_count == 1

    def test_run_id_is_unique(self) -> None:
        run_a = ProductionRun()
        run_b = ProductionRun()
        assert run_a.run_id != run_b.run_id

    def test_default_status_is_pending(self) -> None:
        run = ProductionRun()
        assert run.status == RunStatus.PENDING
