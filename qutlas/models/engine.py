"""
Materials Engine

The central intelligence layer of the Qutlas platform.

Sits between the data pipeline and the adaptive control system.
On each new synchronised reading, it:

  1. Maintains a rolling window of recent process observations
  2. Computes a feature vector from the window
  3. Runs the property prediction model
  4. Compares predictions to the active recipe targets
  5. Publishes results to registered callbacks

The control layer subscribes to engine output and uses the
predictions to decide parameter adjustments.

Architecture note:
  The engine is model-agnostic. It accepts any predictor that
  implements the predict(FeatureVector) → PropertyPredictionResult
  interface. Swapping from the physics baseline to an AMI Labs
  world model in Phase 3 requires only changing the predictor
  passed to the constructor.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, UTC
from typing import Callable, Optional

from qutlas.schema import FiberRecipe
from qutlas.data_pipeline.sync   import SyncedReading
from qutlas.models.features      import FeatureEngineer, FeatureVector, FEATURE_DIM
from qutlas.models.predictor     import (
    PhysicsBaselinePredictor,
    PropertyPredictionResult,
)

logger = logging.getLogger(__name__)


class MaterialsEngine:
    """
    Real-time material property prediction engine.

    Subscribes to the data pipeline and produces property predictions
    on a configurable cadence. Compares predictions to the active
    recipe and publishes results downstream.

    Usage:
        engine = MaterialsEngine(window_size=100, predict_every=10)
        engine.set_recipe(recipe)
        pipeline.on_synced(engine.on_reading)

        # Subscribe to predictions:
        engine.on_prediction(control_layer.handle_prediction)

        # Access latest prediction:
        pred = engine.latest_prediction
    """

    def __init__(
        self,
        window_size:    int = 100,    # readings to use for each prediction
        predict_every:  int = 10,     # make a prediction every N readings
        predictor=None,               # inject predictor (default: physics baseline)
    ) -> None:
        self.window_size   = window_size
        self.predict_every = predict_every
        self.predictor     = predictor or PhysicsBaselinePredictor()
        self.engineer      = FeatureEngineer(window_size=window_size)

        self._window:     deque[SyncedReading] = deque(maxlen=window_size)
        self._recipe:     Optional[FiberRecipe] = None
        self._reading_n:  int = 0
        self._lock:       threading.Lock = threading.Lock()
        self._callbacks:  list[Callable[[PropertyPredictionResult], None]] = []

        self.latest_prediction: Optional[PropertyPredictionResult] = None
        self.prediction_count:  int = 0

    # ── Configuration ───────────────────────────────────────────────────

    def set_recipe(self, recipe: FiberRecipe) -> None:
        """
        Set the active target recipe.

        The engine will compare each prediction to this recipe's
        targets and set the within_tolerance flag accordingly.
        """
        self._recipe = recipe
        logger.info(f"MaterialsEngine: active recipe set to '{recipe.name}'")

    def clear_recipe(self) -> None:
        """Remove the active recipe (predictions continue without tolerance check)."""
        self._recipe = None

    # ── Data input ──────────────────────────────────────────────────────

    def on_reading(self, reading: SyncedReading) -> None:
        """
        Process a new synchronised reading from the data pipeline.

        Called automatically when registered as a pipeline callback:
            pipeline.on_synced(engine.on_reading)

        Triggers a prediction every predict_every readings.
        """
        with self._lock:
            self._window.append(reading)
            self._reading_n += 1
            should_predict = (self._reading_n % self.predict_every == 0)

        if should_predict:
            self._run_prediction()

    def predict_now(self) -> PropertyPredictionResult:
        """
        Force an immediate prediction from the current window.
        Useful for on-demand queries from the control layer.
        """
        return self._run_prediction()

    # ── Callbacks ───────────────────────────────────────────────────────

    def on_prediction(
        self,
        callback: Callable[[PropertyPredictionResult], None],
    ) -> None:
        """
        Register a callback to receive each new prediction.

        The control layer registers here to receive property predictions
        and decide parameter adjustments:
            engine.on_prediction(controller.handle_prediction)
        """
        self._callbacks.append(callback)

    def remove_callback(
        self,
        callback: Callable[[PropertyPredictionResult], None],
    ) -> None:
        self._callbacks = [c for c in self._callbacks if c is not callback]

    # ── Status ──────────────────────────────────────────────────────────

    @property
    def window_fill_pct(self) -> float:
        """How full the observation window is (0–1)."""
        return min(1.0, len(self._window) / max(self.window_size, 1))

    @property
    def is_ready(self) -> bool:
        """
        True when the engine has enough data to make reliable predictions.
        Requires the window to be at least 50% full.
        """
        return self.window_fill_pct >= 0.5

    @property
    def stats(self) -> dict:
        return {
            "prediction_count":  self.prediction_count,
            "reading_count":     self._reading_n,
            "window_fill_pct":   round(self.window_fill_pct, 3),
            "is_ready":          self.is_ready,
            "model_version":     self.predictor.version,
            "active_recipe":     self._recipe.name if self._recipe else None,
        }

    # ── Private ─────────────────────────────────────────────────────────

    def _run_prediction(self) -> PropertyPredictionResult:
        """Compute features, run model, check tolerance, notify callbacks."""
        with self._lock:
            window_snapshot = list(self._window)

        # Compute features
        fv   = self.engineer.compute(window_snapshot)
        issues = fv.validate()
        if issues:
            logger.warning(f"Feature vector issues: {issues}")

        # Run prediction
        result = self.predictor.predict(fv)

        # Check tolerance against active recipe
        if self._recipe is not None:
            result.within_tolerance = self._check_tolerance(result)

        self.latest_prediction = result
        self.prediction_count += 1

        logger.debug(
            f"Prediction #{self.prediction_count}: {result.summary()}"
        )

        # Notify subscribers
        for cb in self._callbacks:
            try:
                cb(result)
            except Exception as e:
                logger.warning(f"Prediction callback error: {e}")

        return result

    def _check_tolerance(self, result: PropertyPredictionResult) -> bool:
        """
        Check whether the predicted properties satisfy the active recipe.
        """
        r = self._recipe
        if r is None:
            return False

        tensile_ok = (
            abs(result.tensile_strength_gpa - r.target_tensile_gpa)
            <= r.tol_tensile_gpa
        )
        modulus_ok = (
            abs(result.elastic_modulus_gpa - r.target_modulus_gpa)
            <= r.tol_modulus_gpa
        )
        cv_ok = result.diameter_cv_pct <= 3.0   # 3% CV is a hard quality limit

        return tensile_ok and modulus_ok and cv_ok
