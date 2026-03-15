"""
Integration test: full end-to-end pipeline.

Runs the simulator for a full production cycle, passes all data
through the pipeline, and verifies the exported run record is correct.

This test exercises every layer of the Phase 1 stack:
  Simulator → Ingestion → Ring Buffer → Sync → Export
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from qutlas.data_pipeline.pipeline    import DataPipeline
from qutlas.data_pipeline.export      import RunExporter
from qutlas.simulation.process        import ProcessSimulator
from qutlas.simulation.runner         import DEFAULT_RECIPES, SimpleController


class TestEndToEnd:

    def test_full_simulation_run_exports_correctly(self, tmp_path: Path) -> None:
        """
        Full run: simulator → pipeline → export → verify.
        """
        recipe     = DEFAULT_RECIPES["structural"]
        sim        = ProcessSimulator(noise_level=0.02)
        controller = SimpleController(recipe)
        pipeline   = DataPipeline(export_root=tmp_path / "runs")
        exporter   = RunExporter(export_root=tmp_path / "runs")

        pipeline.start()
        run = sim.start_run(recipe)

        # Run for 200 steps at 100ms each = 20 simulated seconds
        last_reading = None
        for _ in range(200):
            action       = controller.decide(last_reading) if last_reading else None
            last_reading = sim.step(action)
            pipeline.ingest(last_reading)

        completed = sim.complete_run()
        pipeline.export_run(completed)
        pipeline.stop()

        # Verify run has data
        assert completed.timestep_count == 200
        assert completed.outcome_tensile_gpa  is not None
        assert completed.outcome_modulus_gpa  is not None

        # Verify export files exist
        export_dirs = list(tmp_path.rglob(f"{completed.run_id[:8]}"))
        assert len(export_dirs) == 1

        export_dir = export_dirs[0]
        meta_files = list(export_dir.glob("*_meta.json"))
        assert len(meta_files) == 1

        # Verify metadata content
        with open(meta_files[0]) as f:
            meta = json.load(f)

        assert meta["run_id"]            == completed.run_id
        assert meta["status"]            == "complete"
        assert meta["timestep_count"]    == 200
        assert meta["recipe"]            == "structural"
        assert meta["outcome"]["tensile_gpa"] is not None

    def test_pipeline_synced_window_feeds_feature_vectors(self) -> None:
        """
        Synced readings from the pipeline should produce valid feature
        vectors for model input.
        """
        recipe   = DEFAULT_RECIPES["structural"]
        sim      = ProcessSimulator(noise_level=0.01)
        pipeline = DataPipeline()
        pipeline.start()
        sim.start_run(recipe)

        for _ in range(100):
            reading = sim.step()
            pipeline.ingest(reading)

        window = pipeline.synced_window(100)
        assert len(window) > 0

        for synced in window:
            fv = synced.to_feature_vector()
            assert len(fv) == 8
            assert all(isinstance(v, float) for v in fv)

        pipeline.stop()

    def test_multiple_recipes_produce_different_outputs(self) -> None:
        """
        Running different recipes through the simulator should
        produce measurably different material outcomes.
        """
        results = {}

        for recipe_name in ["structural", "high_temperature"]:
            recipe = DEFAULT_RECIPES[recipe_name]
            sim    = ProcessSimulator(noise_level=0.005)
            sim.start_run(recipe)

            for _ in range(500):
                sim.step()

            run = sim.complete_run()
            results[recipe_name] = {
                "tensile":  run.outcome_tensile_gpa,
                "thermal":  run.outcome_thermal_c,
            }

        # High temperature recipe should produce higher thermal stability
        assert (
            results["high_temperature"]["thermal"] >
            results["structural"]["thermal"]
        ), (
            f"High temp recipe ({results['high_temperature']['thermal']:.0f}°C) "
            f"should exceed structural ({results['structural']['thermal']:.0f}°C)"
        )
