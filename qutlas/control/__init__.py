"""
Control layer — adaptive manufacturing control and recipe management.

Public API:
    AdaptiveController  — main controller (use this)
    SafetyMonitor       — hard limit enforcement
    RecipeLoader        — loads fiber recipes
    ControlConfig       — tuning and safety parameters
    ControlState        — state machine states
    ControlDecision     — controller output type
    ParameterSetpoint   — setpoint type
"""

from qutlas.control.types         import (
    ControlConfig, ControlState, ControlDecision,
    ParameterSetpoint, AdjustmentReason,
)
from qutlas.control.safety        import SafetyMonitor, SafetyStatus
from qutlas.control.recipe_loader import RecipeLoader
from qutlas.control.controller    import AdaptiveController

__all__ = [
    "AdaptiveController",
    "SafetyMonitor", "SafetyStatus",
    "RecipeLoader",
    "ControlConfig", "ControlState", "ControlDecision",
    "ParameterSetpoint", "AdjustmentReason",
]
