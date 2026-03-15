"""
Models — physics simulation, feature engineering, and ML inference.

Public API:
    MaterialsEngine           — main orchestrator (use this)
    FeatureEngineer           — feature computation
    PhysicsBaselinePredictor  — Phase 1 predictor
    PropertyPredictionResult  — prediction output type
    SimulatorDataGenerator    — training data generation
"""

from qutlas.models.furnace    import FurnaceModel, FurnaceConfig
from qutlas.models.viscosity  import ViscosityModel
from qutlas.models.fiber_draw import FiberDrawModel, BushingConfig
from qutlas.models.features   import FeatureEngineer, FeatureVector, FEATURE_NAMES_V1, FEATURE_DIM
from qutlas.models.predictor  import PhysicsBaselinePredictor, PropertyPredictionResult
from qutlas.models.engine     import MaterialsEngine
from qutlas.models.training   import SimulatorDataGenerator, TrainingSample

__all__ = [
    "MaterialsEngine",
    "FeatureEngineer", "FeatureVector", "FEATURE_NAMES_V1", "FEATURE_DIM",
    "PhysicsBaselinePredictor", "PropertyPredictionResult",
    "SimulatorDataGenerator", "TrainingSample",
    "FurnaceModel", "FurnaceConfig",
    "ViscosityModel",
    "FiberDrawModel", "BushingConfig",
]
