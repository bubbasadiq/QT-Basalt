"""
Adaptive Controller

The core control logic of the Qutlas platform.

Receives property predictions from the Materials Engine and
computes coordinated parameter adjustments across temperature,
draw speed, and airflow to converge the process toward the
active recipe's target property profile.

Control strategy:
  The controller implements a model-based proportional approach
  where each controllable parameter is adjusted based on the
  prediction errors most strongly influenced by that parameter.

  Temperature affects:     viscosity → diameter, tensile, thermal stability
  Draw speed affects:      diameter (direct), tensile (indirect)
  Airflow affects:         cooling rate → thermal stability, crystallinity

  Adjustments are coordinated — temperature and speed are adjusted
  together when diameter is off target to avoid over-correcting
  one variable while the other compensates in the wrong direction.

  The controller is conservative by design:
    - Acts only when prediction confidence exceeds threshold
    - Step sizes are bounded by max_step configuration
    - Prefers smaller frequent adjustments over large infrequent ones
    - Enters STABLE state and minimises changes once within tolerance

Phase 3 replacement:
  This controller will be replaced by the AMI Labs world model
  planner, which will compute optimal action sequences rather
  than greedy per-step adjustments. The interface (on_prediction /
  latest_decision) is designed to be compatible with both.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, UTC
from typing import Callable, Optional

from qutlas.schema import FiberRecipe
from qutlas.data_pipeline.sync    import SyncedReading
from qutlas.models.predictor      import PropertyPredictionResult
from qutlas.control.types         import (
    AdjustmentReason,
    ControlConfig,
    ControlDecision,
    ControlState,
    ParameterSetpoint,
)
from qutlas.control.safety        import SafetyMonitor
from qutlas.control.recipe_loader import RecipeLoader

logger = logging.getLogger(__name__)


class AdaptiveController:
    """
    Multi-variable adaptive controller for basalt fiber manufacturing.

    Subscribes to Materials Engine predictions and sensor readings.
    Produces ControlDecision objects that are sent to the hardware
    execution layer (or simulator).

    Usage:
        controller = AdaptiveController()
        controller.activate_recipe("structural")

        # Wire to Materials Engine:
        engine.on_prediction(controller.on_prediction)

        # Wire to sensor stream for safety monitoring:
        pipeline.on_synced(controller.on_reading)

        # Subscribe to control decisions:
        controller.on_decision(hardware.execute)

        # Get the latest decision at any time:
        decision = controller.latest_decision
    """

    def __init__(
        self,
        config:        ControlConfig | None = None,
        recipe_loader: RecipeLoader  | None = None,
    ) -> None:
        self.config   = config or ControlConfig()
        self.loader   = recipe_loader or RecipeLoader()
        self.safety   = SafetyMonitor(self.config)

        self._state:   ControlState         = ControlState.IDLE
        self._recipe:  Optional[FiberRecipe] = None
        self._run_id:  Optional[str]         = None
        self._lock:    threading.Lock        = threading.Lock()

        # Current setpoints — start at safe defaults
        self._setpoint = ParameterSetpoint()

        # Stability tracking
        self._consecutive_stable: int = 0
        self._total_decisions:    int = 0
        self._total_adjustments:  int = 0

        # Callbacks
        self._decision_callbacks: list[Callable[[ControlDecision], None]] = []

        # Latest outputs
        self.latest_decision: Optional[ControlDecision] = None
        self.latest_reading:  Optional[SyncedReading]   = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    def activate_recipe(self, recipe_name: str, run_id: Optional[str] = None) -> None:
        """
        Activate a fiber recipe and begin production.

        Transitions from IDLE → WARMING. The controller will
        set the furnace toward the recipe's initial temperature
        and wait until it's reached before beginning active control.

        Args:
            recipe_name: name of the recipe (must exist in control/recipes/)
            run_id:      optional run identifier for logging
        """
        with self._lock:
            recipe = self.loader.load(recipe_name)
            self._recipe   = recipe
            self._run_id   = run_id
            self._state    = ControlState.WARMING
            self._consecutive_stable = 0

            # Seed setpoints from recipe
            self._setpoint = ParameterSetpoint(
                furnace_temp_c = recipe.initial_temp_c,
                draw_speed_ms  = recipe.initial_draw_speed_ms,
                airflow_lpm    = recipe.initial_airflow_lpm,
            )

        logger.info(
            f"AdaptiveController: recipe '{recipe_name}' activated, "
            f"state → WARMING, initial_temp={recipe.initial_temp_c}°C"
        )

    def deactivate(self) -> None:
        """Stop active control and return to IDLE."""
        with self._lock:
            self._recipe  = None
            self._state   = ControlState.IDLE
            self._run_id  = None
        logger.info("AdaptiveController: deactivated → IDLE")

    def reset_after_abort(self) -> None:
        """
        Reset from ABORTED state to IDLE.
        Requires manual call — the system will not self-recover from abort.
        """
        with self._lock:
            if self._state == ControlState.ABORTED:
                self._state = ControlState.IDLE
                logger.info("AdaptiveController: manual reset from ABORTED → IDLE")

    # ── Data inputs ─────────────────────────────────────────────────────

    def on_reading(self, reading: SyncedReading) -> None:
        """
        Process a new sensor reading.

        Primarily used for safety monitoring — checks every reading
        against hard limits and triggers emergency stop if breached.
        """
        self.latest_reading = reading

        if self._state == ControlState.ABORTED:
            return

        status = self.safety.check_reading(reading)
        if not status.safe:
            self._emergency_stop(status.breaches)

        # Check WARMING transition: has furnace reached initial temperature?
        if (
            self._state == ControlState.WARMING
            and self._recipe is not None
            and reading.furnace_temp_c is not None
        ):
            delta = abs(reading.furnace_temp_c - self._recipe.initial_temp_c)
            if delta < 15.0:   # within 15°C of initial setpoint
                with self._lock:
                    self._state = ControlState.CONVERGING
                logger.info(
                    f"AdaptiveController: furnace at {reading.furnace_temp_c:.1f}°C, "
                    f"state → CONVERGING"
                )

    def on_prediction(self, prediction: PropertyPredictionResult) -> None:
        """
        Process a new property prediction from the Materials Engine.

        This is the main control trigger. On each prediction, the
        controller computes new setpoints and issues a ControlDecision.
        """
        if self._state in (ControlState.IDLE, ControlState.ABORTED):
            return

        if self._state == ControlState.WARMING:
            # During warming, just hold the initial setpoints
            self._emit_hold_decision(prediction, reason=AdjustmentReason.WARMING)
            return

        # Low confidence — hold current setpoints
        if prediction.confidence < self.config.min_confidence_to_act:
            self._emit_hold_decision(prediction, reason=AdjustmentReason.CONFIDENCE_LOW)
            return

        decision = self._compute_decision(prediction)
        self._publish(decision)

    # ── Callbacks ───────────────────────────────────────────────────────

    def on_decision(self, callback: Callable[[ControlDecision], None]) -> None:
        """
        Register a callback to receive each ControlDecision.

        The hardware execution layer registers here:
            controller.on_decision(hardware.execute)
        """
        self._decision_callbacks.append(callback)

    # ── Status ──────────────────────────────────────────────────────────

    @property
    def state(self) -> ControlState:
        return self._state

    @property
    def setpoint(self) -> ParameterSetpoint:
        return self._setpoint

    @property
    def stats(self) -> dict:
        return {
            "state":                self._state.value,
            "active_recipe":        self._recipe.name if self._recipe else None,
            "total_decisions":      self._total_decisions,
            "total_adjustments":    self._total_adjustments,
            "consecutive_stable":   self._consecutive_stable,
            "current_temp_sp":      self._setpoint.furnace_temp_c,
            "current_speed_sp":     self._setpoint.draw_speed_ms,
            "current_airflow_sp":   self._setpoint.airflow_lpm,
            "safety":               self.safety.stats,
        }

    # ── Core control logic ───────────────────────────────────────────────

    def _compute_decision(
        self,
        prediction: PropertyPredictionResult,
    ) -> ControlDecision:
        """
        Compute new setpoints from a property prediction.

        Implements coordinated multi-variable control:
          1. Compute errors against recipe targets
          2. Calculate raw adjustments via gain parameters
          3. Clip adjustments to max step sizes
          4. Apply safety clamping
          5. Determine new control state
        """
        recipe = self._recipe
        cfg    = self.config
        prev   = self._setpoint

        # ── Error signals ────────────────────────────────────────────
        tensile_err  = prediction.tensile_strength_gpa - recipe.target_tensile_gpa
        thermal_err  = prediction.thermal_stability_c  - recipe.target_thermal_c

        # Diameter error requires the latest sensor reading
        diam_err = 0.0
        if (
            self.latest_reading is not None
            and self.latest_reading.fiber_diameter_um is not None
        ):
            diam_err = (
                self.latest_reading.fiber_diameter_um - recipe.target_diameter_um
            )

        reasons: list[AdjustmentReason] = []

        # ── Temperature adjustment ───────────────────────────────────
        # Temperature is the primary lever for both tensile and diameter.
        # Higher temp → lower viscosity → finer diameter → higher tensile.
        # So: diameter too large OR tensile too low → increase temperature.
        delta_temp = (
            diam_err    * cfg.temp_gain_diameter      # positive diam_err → need hotter
            - tensile_err * cfg.temp_gain_tensile * 0.3  # tensile too low → need hotter
        )
        if abs(diam_err) > recipe.tol_diameter_um * 0.5:
            reasons.append(AdjustmentReason.DIAMETER_ERROR)
        if abs(tensile_err) > recipe.tol_tensile_gpa * 0.5:
            reasons.append(AdjustmentReason.TENSILE_ERROR)

        # ── Speed adjustment ─────────────────────────────────────────
        # Speed is a secondary diameter lever.
        # Faster draw → thinner fiber (at same viscosity).
        # Used to fine-tune diameter after temperature settles.
        delta_speed = -diam_err * cfg.speed_gain_diameter   # negative: faster → thinner

        # ── Airflow adjustment ───────────────────────────────────────
        # Airflow controls the cooling rate after draw.
        # More airflow → faster cooling → less devitrification → better thermal stability.
        delta_air = -thermal_err * cfg.airflow_gain_thermal
        if abs(thermal_err) > recipe.tol_thermal_c * 0.5:
            reasons.append(AdjustmentReason.THERMAL_ERROR)

        # ── Stability check ──────────────────────────────────────────
        if prediction.diameter_cv_pct > cfg.stability_cv_threshold * 1.5:
            # High instability: reduce speed to stabilise
            delta_speed -= 0.2
            reasons.append(AdjustmentReason.STABILITY_LOW)

        # ── Clip to max step sizes ───────────────────────────────────
        delta_temp  = _clip(delta_temp,  cfg.max_temp_step_c)
        delta_speed = _clip(delta_speed, cfg.max_speed_step_ms)
        delta_air   = _clip(delta_air,   cfg.max_airflow_step_lpm)

        # ── If within tolerance, make only maintenance trims ─────────
        if prediction.within_tolerance:
            delta_temp  *= 0.2   # 80% reduction — gentle maintenance
            delta_speed *= 0.2
            delta_air   *= 0.2
            if not reasons:
                reasons.append(AdjustmentReason.MAINTENANCE)

        # ── Proposed new setpoints ───────────────────────────────────
        proposed = ParameterSetpoint(
            furnace_temp_c = prev.furnace_temp_c + delta_temp,
            draw_speed_ms  = prev.draw_speed_ms  + delta_speed,
            airflow_lpm    = prev.airflow_lpm    + delta_air,
        )

        # ── Safety clamping ──────────────────────────────────────────
        safe_setpoint, safety_status = self.safety.clamp_setpoint(
            proposed,
            recipe_max_temp  = recipe.max_temp_c,
            recipe_min_temp  = recipe.min_temp_c,
            recipe_max_speed = recipe.max_draw_speed_ms,
        )
        if safety_status.any_clamped:
            reasons.append(AdjustmentReason.SAFETY_LIMIT)

        # ── State transition ─────────────────────────────────────────
        new_state = self._update_state(prediction)

        # ── Record decision ──────────────────────────────────────────
        actual_delta_temp  = safe_setpoint.furnace_temp_c - prev.furnace_temp_c
        actual_delta_speed = safe_setpoint.draw_speed_ms  - prev.draw_speed_ms
        actual_delta_air   = safe_setpoint.airflow_lpm    - prev.airflow_lpm

        with self._lock:
            self._setpoint = safe_setpoint

        decision = ControlDecision(
            timestamp              = datetime.now(UTC),
            run_id                 = self._run_id,
            state                  = new_state,
            setpoint               = safe_setpoint,
            delta_temp_c           = actual_delta_temp,
            delta_speed_ms         = actual_delta_speed,
            delta_airflow_lpm      = actual_delta_air,
            reasons                = reasons,
            prediction_confidence  = prediction.confidence,
            within_tolerance       = prediction.within_tolerance,
            consecutive_stable     = self._consecutive_stable,
        )

        return decision

    def _update_state(self, prediction: PropertyPredictionResult) -> ControlState:
        """Update and return the new control state based on prediction."""
        if self._state == ControlState.ABORTED:
            return ControlState.ABORTED

        if prediction.within_tolerance:
            self._consecutive_stable += 1
            if self._consecutive_stable >= self.config.stable_predictions_needed:
                new_state = ControlState.STABLE
            else:
                new_state = ControlState.CONVERGING
        else:
            self._consecutive_stable = 0
            new_state = ControlState.CONVERGING

        if new_state != self._state:
            logger.info(
                f"AdaptiveController: state {self._state.value} → {new_state.value} "
                f"(stable={self._consecutive_stable})"
            )

        with self._lock:
            self._state = new_state

        return new_state

    # ── Internal helpers ─────────────────────────────────────────────────

    def _emit_hold_decision(
        self,
        prediction: PropertyPredictionResult,
        reason:     AdjustmentReason,
    ) -> None:
        """Emit a decision that holds the current setpoints unchanged."""
        decision = ControlDecision(
            timestamp             = datetime.now(UTC),
            run_id                = self._run_id,
            state                 = self._state,
            setpoint              = self._setpoint,
            reasons               = [reason],
            prediction_confidence = prediction.confidence,
            within_tolerance      = prediction.within_tolerance,
        )
        self._publish(decision)

    def _emergency_stop(self, breaches: list[str]) -> None:
        """Trigger emergency stop due to safety breach."""
        with self._lock:
            self._state = ControlState.ABORTED
            self.safety._e_stop_count += 1

        decision = ControlDecision(
            timestamp  = datetime.now(UTC),
            run_id     = self._run_id,
            state      = ControlState.ABORTED,
            setpoint   = self._setpoint,
            reasons    = [AdjustmentReason.EMERGENCY_STOP],
            notes      = f"EMERGENCY STOP: {'; '.join(breaches)}",
        )
        self._publish(decision)
        logger.critical(f"EMERGENCY STOP triggered: {breaches}")

    def _publish(self, decision: ControlDecision) -> None:
        """Record and broadcast a control decision."""
        self.latest_decision = decision
        self._total_decisions += 1
        if decision.any_adjustment:
            self._total_adjustments += 1

        logger.debug(f"Control: {decision.summary()}")

        for cb in self._decision_callbacks:
            try:
                cb(decision)
            except Exception as e:
                logger.warning(f"Decision callback error: {e}")


# ── Utility ──────────────────────────────────────────────────────────────────

def _clip(value: float, max_abs: float) -> float:
    """Clip a value to [-max_abs, +max_abs]."""
    return max(-max_abs, min(max_abs, value))
