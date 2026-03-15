"""
Property Predictor

The core inference model of the Materials Engine.

Takes a FeatureVector derived from a window of process observations
and predicts downstream material properties — tensile strength,
elastic modulus, thermal stability, and diameter consistency —
while the material is still being produced.

Architecture: Phase 1 uses a physics-informed baseline model
(gradient boosted regression, no neural network required at this
stage) that encodes known process-property relationships as priors.
This gives interpretable predictions on small datasets.

Phase 3 will replace the gradient boosted model with the AMI Labs
world model once sufficient labeled production run data exists
(target: 500+ completed runs with physical test outcomes).

Model versioning:
  v0.1 — physics baseline (this file)
  v0.2 — gradient boosted on simulator data (Phase 2)
  v1.0 — AMI Labs world model on real production data (Phase 3)
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional

from qutlas.models.features import FeatureVector, FEATURE_NAMES_V1

logger = logging.getLogger(__name__)

MODEL_VERSION = "0.1-physics-baseline"


@dataclass
class PropertyPredictionResult:
    """
    Predicted material properties from the inference model.

    All predictions include a confidence score (0–1) reflecting
    how reliable the prediction is given the current feature quality.
    """
    model_version:         str
    predicted_at:          datetime

    # Mechanical properties
    tensile_strength_gpa:  float          # GPa
    elastic_modulus_gpa:   float          # GPa
    elongation_pct:        float          # % at break

    # Thermal
    thermal_stability_c:   float          # max service temperature °C

    # Quality metrics
    diameter_cv_pct:       float          # coefficient of variation %
    defect_probability:    float          # 0–1 (0 = no defects predicted)

    # Prediction quality
    confidence:            float          # 0–1 composite confidence
    feature_complete:      bool           # True if input had no missing values

    # Tolerance check (populated by engine after comparing to recipe)
    within_tolerance:      bool = False

    def summary(self) -> str:
        return (
            f"tensile={self.tensile_strength_gpa:.3f}GPa  "
            f"modulus={self.elastic_modulus_gpa:.1f}GPa  "
            f"thermal={self.thermal_stability_c:.0f}°C  "
            f"cv={self.diameter_cv_pct:.2f}%  "
            f"confidence={self.confidence:.2f}"
        )


class PhysicsBaselinePredictor:
    """
    Physics-informed baseline property predictor.

    Uses analytical relationships between process parameters and
    material properties derived from published basalt fiber research.

    This is the Phase 1 model — interpretable, fast, and correct
    in its qualitative behaviour even before training data exists.

    Key relationships encoded:
      - Higher draw temperature → lower viscosity → finer diameter
        → higher tensile strength (Hall-Petch analog for glass fibers)
      - Draw stability (low diameter CV) → fewer surface defects
        → higher tensile strength
      - Higher cooling rate → less crystallisation → better thermal stability
      - Optimal viscosity window → better structural uniformity

    Sources:
      - Dhand et al. (2015) "A short review of basalt fiber reinforced
        polymer composites" Composites Part B, 73, 166-180.
      - Liu et al. (2006) "Basalt fiber and its composite" Fibre Chemistry
    """

    def __init__(self) -> None:
        self.version = MODEL_VERSION
        self._call_count = 0

    def predict(self, fv: FeatureVector) -> PropertyPredictionResult:
        """
        Predict material properties from a feature vector.

        Args:
            fv: FeatureVector from FeatureEngineer.compute()

        Returns:
            PropertyPredictionResult with all predicted properties
        """
        self._call_count += 1
        feat = fv.as_dict()

        # ── Extract key features ─────────────────────────────────────
        temp_mean    = feat.get("temp_mean",   1480.0)
        temp_std     = feat.get("temp_std",    5.0)
        diam_mean    = feat.get("diam_mean",   13.0)
        diam_std     = feat.get("diam_std",    0.5)
        speed_mean   = feat.get("speed_mean",  12.0)
        visc_mean    = feat.get("visc_mean",   750.0)
        stability    = feat.get("draw_stability_index",  0.95)
        uniformity   = feat.get("thermal_uniformity",    0.99)
        pw_score     = feat.get("process_window_score",  0.8)

        # ── Tensile strength prediction ───────────────────────────────
        # Finer fibers are stronger (Griffith flaw theory)
        # Optimal range: 9–14 µm → 2.8–3.2 GPa
        # Coarser fibers: 14–20 µm → 2.4–2.8 GPa
        diam_factor   = max(0.0, 1.0 - (diam_mean - 9.0) / 20.0)
        # Higher draw stability → fewer surface flaws → higher strength
        stab_factor   = stability
        # Temperature proximity to optimal (1480°C) → better homogeneity
        temp_opt_factor = max(0.0, 1.0 - abs(temp_mean - 1480.0) / 150.0)

        tensile = 2.4 + 0.8 * (
            0.45 * diam_factor +
            0.35 * stab_factor +
            0.20 * temp_opt_factor
        )
        tensile = max(1.8, min(3.8, tensile))

        # ── Elastic modulus ───────────────────────────────────────────
        # Modulus is less sensitive to processing than tensile strength
        # Primarily driven by glass composition (fixed for basalt)
        # Small process dependence via crystallinity and draw tension
        modulus = 78.0 + 12.0 * temp_opt_factor * 0.6 + 5.0 * stab_factor * 0.4
        modulus = max(65.0, min(95.0, modulus))

        # ── Thermal stability ─────────────────────────────────────────
        # Higher draw temperature → more homogeneous glass → better thermal perf
        # Faster draw speed → less time at high temp → less devitrification
        temp_factor  = (temp_mean - 1400.0) / 200.0  # 0–1 over 1400–1600°C
        speed_factor = min(1.0, speed_mean / 20.0)
        thermal = 580.0 + 180.0 * (0.6 * temp_factor + 0.4 * speed_factor)
        thermal = max(550.0, min(820.0, thermal))

        # ── Diameter CV ───────────────────────────────────────────────
        # Directly from draw stability
        # CV = std/mean * 100 — we invert stability index
        diam_cv = (1.0 - stability) * 15.0   # 0–15% range
        diam_cv = max(0.1, min(10.0, diam_cv))

        # ── Elongation at break ───────────────────────────────────────
        # Finer fibers have higher elongation
        elongation = 1.8 + diam_factor * 1.0 + stab_factor * 0.5
        elongation = max(1.5, min(3.5, elongation))

        # ── Defect probability ────────────────────────────────────────
        # Increases with instability, poor thermal uniformity, and
        # operating outside the process window
        defect_prob = (
            (1.0 - stability)   * 0.5 +
            (1.0 - uniformity)  * 0.3 +
            (1.0 - pw_score)    * 0.2
        )
        defect_prob = max(0.0, min(1.0, defect_prob))

        # ── Confidence ───────────────────────────────────────────────
        # Based on feature completeness and window size
        window_confidence = min(1.0, fv.window_size / 100.0)
        data_confidence   = 1.0 if fv.complete else max(0.3, 1.0 - fv.missing_count / fv.window_size)
        confidence        = 0.6 * window_confidence + 0.4 * data_confidence

        return PropertyPredictionResult(
            model_version        = self.version,
            predicted_at         = datetime.now(UTC),
            tensile_strength_gpa = round(tensile,   3),
            elastic_modulus_gpa  = round(modulus,   2),
            elongation_pct       = round(elongation, 2),
            thermal_stability_c  = round(thermal,   1),
            diameter_cv_pct      = round(diam_cv,   3),
            defect_probability   = round(defect_prob, 4),
            confidence           = round(confidence, 3),
            feature_complete     = fv.complete,
        )

    @property
    def call_count(self) -> int:
        """Number of predictions made since instantiation."""
        return self._call_count
