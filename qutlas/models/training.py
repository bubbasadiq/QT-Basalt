"""
Model Training Utilities

Generates labeled training datasets from simulator runs,
prepares features and targets, and provides train/eval split logic.

In Phase 1 this builds a dataset from the process simulator.
In Phase 2 it will augment with real production run data.
In Phase 3 it feeds the AMI Labs world model training pipeline.

Dataset schema:
  Each sample is one completed production run:
    X: feature vector at run completion (26 features, v1.0 schema)
    y: material outcome vector (4 targets)

  X columns: see FEATURE_NAMES_V1 in models/features.py
  y columns: tensile_gpa, modulus_gpa, diameter_cv, thermal_c
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Iterator

from qutlas.schema          import FiberRecipe, FiberClass
from qutlas.data_pipeline   import DataPipeline
from qutlas.models.features import FeatureEngineer, FeatureVector, FEATURE_NAMES_V1
from qutlas.simulation.process import ProcessSimulator
from qutlas.simulation.runner  import DEFAULT_RECIPES, SimpleController

logger = logging.getLogger(__name__)


@dataclass
class TrainingSample:
    """A single labeled training sample."""
    run_id:          str
    recipe_name:     str
    fiber_class:     str
    features:        list[float]    # FEATURE_DIM values
    tensile_gpa:     float
    modulus_gpa:     float
    diameter_cv:     float
    thermal_c:       float
    simulated:       bool = True
    created_at:      str  = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    @property
    def targets(self) -> list[float]:
        return [self.tensile_gpa, self.modulus_gpa, self.diameter_cv, self.thermal_c]

    @staticmethod
    def target_names() -> list[str]:
        return ["tensile_gpa", "modulus_gpa", "diameter_cv", "thermal_c"]


class SimulatorDataGenerator:
    """
    Generates labeled training samples by running the process simulator
    across a range of conditions and recording outcomes.

    Varies initial conditions to explore the process space:
      - Different recipes (fiber classes)
      - Perturbed initial temperatures
      - Different noise levels
      - Varying run durations

    Usage:
        gen = SimulatorDataGenerator()
        samples = gen.generate(n_runs=200)
        gen.save(samples, Path("models/training/datasets/sim_v1.json"))
    """

    def __init__(
        self,
        steps_per_run: int   = 500,
        dt:            float = 0.1,
        noise_range:   tuple[float, float] = (0.01, 0.04),
    ) -> None:
        self.steps_per_run = steps_per_run
        self.dt            = dt
        self.noise_range   = noise_range
        self.engineer      = FeatureEngineer(window_size=100)

    def generate(
        self,
        n_runs:          int = 100,
        recipes:         list[FiberRecipe] | None = None,
        temp_perturbation: float = 30.0,   # ±°C variation around recipe initial temp
    ) -> list[TrainingSample]:
        """
        Generate n_runs labeled training samples.

        Args:
            n_runs:              number of runs to simulate
            recipes:             recipes to use (default: all 2 defaults)
            temp_perturbation:   ±°C random offset on initial temperature

        Returns:
            List of TrainingSample with features and material outcomes
        """
        if recipes is None:
            recipes = list(DEFAULT_RECIPES.values())

        samples: list[TrainingSample] = []

        for i in range(n_runs):
            recipe = recipes[i % len(recipes)]
            noise  = random.uniform(*self.noise_range)
            temp_offset = random.uniform(-temp_perturbation, temp_perturbation)

            sample = self._run_once(recipe, noise, temp_offset)
            if sample is not None:
                samples.append(sample)

            if (i + 1) % 10 == 0:
                logger.info(f"Generated {i + 1}/{n_runs} samples")

        logger.info(
            f"Dataset generation complete: {len(samples)} valid samples "
            f"from {n_runs} runs"
        )
        return samples

    def _run_once(
        self,
        recipe:       FiberRecipe,
        noise:        float,
        temp_offset:  float,
    ) -> TrainingSample | None:
        """Run a single simulation and return a training sample."""
        try:
            pipeline   = DataPipeline()
            pipeline.start()
            pipeline.reset_for_new_run()

            # Perturb initial temperature
            perturbed = FiberRecipe(
                **{
                    **recipe.__dict__,
                    "initial_temp_c": recipe.initial_temp_c + temp_offset,
                }
            )

            sim        = ProcessSimulator(noise_level=noise, dt=self.dt)
            controller = SimpleController(perturbed)
            run        = sim.start_run(perturbed)

            last_reading = None
            for _ in range(self.steps_per_run):
                action       = controller.decide(last_reading) if last_reading else None
                last_reading = sim.step(action)
                pipeline.ingest(last_reading)

            completed = sim.complete_run()
            pipeline.stop()

            # Skip runs with missing outcomes
            if any(v is None for v in [
                completed.outcome_tensile_gpa,
                completed.outcome_modulus_gpa,
                completed.outcome_diameter_cv,
                completed.outcome_thermal_c,
            ]):
                return None

            # Compute feature vector from final window
            window = pipeline.synced_window(100)
            if len(window) < 10:
                return None

            fv = self.engineer.compute(window)

            return TrainingSample(
                run_id       = completed.run_id,
                recipe_name  = recipe.name,
                fiber_class  = recipe.fiber_class.value,
                features     = fv.values,
                tensile_gpa  = completed.outcome_tensile_gpa,   # type: ignore[arg-type]
                modulus_gpa  = completed.outcome_modulus_gpa,   # type: ignore[arg-type]
                diameter_cv  = completed.outcome_diameter_cv,   # type: ignore[arg-type]
                thermal_c    = completed.outcome_thermal_c,     # type: ignore[arg-type]
                simulated    = True,
            )

        except Exception as e:
            logger.warning(f"Run failed: {e}")
            return None

    @staticmethod
    def save(samples: list[TrainingSample], path: Path) -> None:
        """Save samples to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version":  "1.0",
            "feature_names":   FEATURE_NAMES_V1,
            "target_names":    TrainingSample.target_names(),
            "n_samples":       len(samples),
            "created_at":      datetime.now(UTC).isoformat(),
            "samples":         [asdict(s) for s in samples],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(samples)} samples to {path}")

    @staticmethod
    def load(path: Path) -> tuple[list[list[float]], list[list[float]]]:
        """
        Load a dataset and return (X, y) as lists of lists.

        Returns:
            X: list of feature vectors
            y: list of target vectors [tensile, modulus, cv, thermal]
        """
        with open(path) as f:
            data = json.load(f)

        X = [s["features"] for s in data["samples"]]
        y = [
            [s["tensile_gpa"], s["modulus_gpa"], s["diameter_cv"], s["thermal_c"]]
            for s in data["samples"]
        ]
        return X, y
