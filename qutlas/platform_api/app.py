"""
Qutlas Platform API

FastAPI application exposing the manufacturing platform
over REST and WebSocket.

Endpoints:
  GET  /health                  — liveness check
  GET  /status                  — full platform status
  GET  /recipes                 — list available recipes
  GET  /recipes/{name}          — recipe detail
  POST /runs/start              — start a production run
  POST /runs/stop               — stop the active run
  GET  /runs/current            — current run status
  GET  /runs/{run_id}           — historical run detail
  GET  /predictions/latest      — latest property prediction
  GET  /sensors/latest          — latest sensor reading
  WS   /ws/telemetry            — live sensor + prediction stream

Start:
  uvicorn qutlas.platform_api.app:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from qutlas.schema import DataSource, FiberClass
from qutlas.data_pipeline       import DataPipeline
from qutlas.models.engine       import MaterialsEngine
from qutlas.control.controller  import AdaptiveController
from qutlas.control.recipe_loader import RecipeLoader
from qutlas.simulation.process  import ProcessSimulator
from qutlas.simulation.runner   import DEFAULT_RECIPES, SimpleController

logger = logging.getLogger(__name__)


# ── Platform state (singleton, lives for app lifetime) ─────────────────────

class PlatformState:
    """Holds all running platform components."""

    def __init__(self) -> None:
        self.pipeline   = DataPipeline()
        self.engine     = MaterialsEngine(window_size=100, predict_every=5)
        self.controller = AdaptiveController()
        self.loader     = RecipeLoader()
        self.simulator: Optional[ProcessSimulator] = None
        self.active_run_id: Optional[str]          = None
        self.active_recipe_name: Optional[str]     = None
        self._sim_task: Optional[asyncio.Task]     = None  # type: ignore[type-arg]
        self._ws_clients: list[WebSocket]          = []

        # Wire components together
        self.pipeline.on_synced(self.engine.on_reading)
        self.pipeline.on_synced(self.controller.on_reading)
        self.engine.on_prediction(self.controller.on_prediction)
        self.engine.on_prediction(self._broadcast_prediction)

    def start(self) -> None:
        self.pipeline.start()
        logger.info("Platform started")

    def stop(self) -> None:
        self.pipeline.stop()
        if self._sim_task:
            self._sim_task.cancel()
        logger.info("Platform stopped")

    async def _broadcast_prediction(self, prediction: Any) -> None:
        """Broadcast a new prediction to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        msg = json.dumps({
            "type": "prediction",
            "data": {
                "tensile_gpa":  prediction.tensile_strength_gpa,
                "modulus_gpa":  prediction.elastic_modulus_gpa,
                "thermal_c":    prediction.thermal_stability_c,
                "diameter_cv":  prediction.diameter_cv_pct,
                "confidence":   prediction.confidence,
                "within_tol":   prediction.within_tolerance,
                "ts":           prediction.predicted_at.isoformat(),
            }
        })
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.remove(ws)


platform = PlatformState()


# ── App lifecycle ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    platform.start()
    yield
    platform.stop()


app = FastAPI(
    title       = "Qutlas Platform API",
    description = "Programmable Materials Manufacturing Infrastructure",
    version     = "0.1.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)


# ── Pydantic models ─────────────────────────────────────────────────────────

class StartRunRequest(BaseModel):
    recipe_name:  str
    use_simulator: bool = True


class RunResponse(BaseModel):
    run_id:        Optional[str]
    recipe:        Optional[str]
    state:         str
    started_at:    Optional[str]
    timestep_count: int


class PredictionResponse(BaseModel):
    tensile_gpa:  Optional[float]
    modulus_gpa:  Optional[float]
    thermal_c:    Optional[float]
    diameter_cv:  Optional[float]
    confidence:   Optional[float]
    within_tol:   Optional[bool]
    model:        Optional[str]


class SensorResponse(BaseModel):
    furnace_temp_c:     Optional[float]
    fiber_diameter_um:  Optional[float]
    draw_speed_ms:      Optional[float]
    melt_viscosity_cp:  Optional[float]
    airflow_lpm:        Optional[float]
    ts:                 Optional[str]


# ── REST endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ts": datetime.now(UTC).isoformat()}


@app.get("/status")
async def status() -> dict:
    ctrl  = platform.controller
    eng   = platform.engine
    pipe  = platform.pipeline
    return {
        "platform_version": "0.1.0",
        "controller":       ctrl.stats,
        "engine":           eng.stats,
        "pipeline":         pipe.stats,
        "active_run":       platform.active_run_id,
        "active_recipe":    platform.active_recipe_name,
        "ws_clients":       len(platform._ws_clients),
    }


@app.get("/recipes")
async def list_recipes() -> dict:
    return {
        "recipes": platform.loader.list_available()
    }


@app.get("/recipes/{name}")
async def get_recipe(name: str) -> dict:
    try:
        r = platform.loader.load(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "name":               r.name,
        "fiber_class":        r.fiber_class.value,
        "description":        r.description,
        "targets": {
            "tensile_gpa":    r.target_tensile_gpa,
            "modulus_gpa":    r.target_modulus_gpa,
            "diameter_um":    r.target_diameter_um,
            "thermal_c":      r.target_thermal_c,
        },
        "tolerances": {
            "tensile_gpa":    r.tol_tensile_gpa,
            "modulus_gpa":    r.tol_modulus_gpa,
            "diameter_um":    r.tol_diameter_um,
            "thermal_c":      r.tol_thermal_c,
        },
        "initial_params": {
            "temp_c":         r.initial_temp_c,
            "draw_speed_ms":  r.initial_draw_speed_ms,
            "airflow_lpm":    r.initial_airflow_lpm,
        },
    }


@app.post("/runs/start")
async def start_run(req: StartRunRequest) -> dict:
    if platform.active_run_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Run {platform.active_run_id} already active. Stop it first."
        )
    try:
        recipe = platform.loader.load(req.recipe_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Start simulator
    sim = ProcessSimulator(noise_level=0.02)
    run = sim.start_run(recipe)

    platform.simulator           = sim
    platform.active_run_id       = run.run_id
    platform.active_recipe_name  = req.recipe_name
    platform.pipeline.reset_for_new_run()
    platform.engine.set_recipe(recipe)
    platform.controller.activate_recipe(req.recipe_name, run_id=run.run_id)

    # Start async simulation loop
    platform._sim_task = asyncio.create_task(
        _simulation_loop(sim, recipe)
    )

    logger.info(f"Run started: {run.run_id[:8]} — recipe={req.recipe_name}")
    return {
        "run_id":    run.run_id,
        "recipe":    req.recipe_name,
        "message":   "Run started",
    }


@app.post("/runs/stop")
async def stop_run() -> dict:
    if platform.active_run_id is None:
        raise HTTPException(status_code=404, detail="No active run")
    if platform._sim_task:
        platform._sim_task.cancel()
        platform._sim_task = None
    run_id = platform.active_run_id
    platform.active_run_id      = None
    platform.active_recipe_name = None
    platform.controller.deactivate()
    return {"message": f"Run {run_id[:8]} stopped"}


@app.get("/runs/current", response_model=RunResponse)
async def current_run() -> RunResponse:
    ctrl = platform.controller
    return RunResponse(
        run_id         = platform.active_run_id,
        recipe         = platform.active_recipe_name,
        state          = ctrl.state.value,
        started_at     = None,
        timestep_count = platform.pipeline.ingestion.buffer.size,
    )


@app.get("/predictions/latest", response_model=PredictionResponse)
async def latest_prediction() -> PredictionResponse:
    pred = platform.engine.latest_prediction
    if pred is None:
        return PredictionResponse(
            tensile_gpa=None, modulus_gpa=None, thermal_c=None,
            diameter_cv=None, confidence=None, within_tol=None, model=None,
        )
    return PredictionResponse(
        tensile_gpa = pred.tensile_strength_gpa,
        modulus_gpa = pred.elastic_modulus_gpa,
        thermal_c   = pred.thermal_stability_c,
        diameter_cv = pred.diameter_cv_pct,
        confidence  = pred.confidence,
        within_tol  = pred.within_tolerance,
        model       = pred.model_version,
    )


@app.get("/sensors/latest", response_model=SensorResponse)
async def latest_sensors() -> SensorResponse:
    synced = platform.pipeline.current_synced()
    if synced is None:
        return SensorResponse(
            furnace_temp_c=None, fiber_diameter_um=None,
            draw_speed_ms=None, melt_viscosity_cp=None,
            airflow_lpm=None, ts=None,
        )
    return SensorResponse(
        furnace_temp_c    = synced.furnace_temp_c,
        fiber_diameter_um = synced.fiber_diameter_um,
        draw_speed_ms     = synced.draw_speed_ms,
        melt_viscosity_cp = synced.melt_viscosity_cp,
        airflow_lpm       = synced.airflow_rate_lpm,
        ts                = synced.bin_timestamp.isoformat(),
    )


# ── WebSocket ───────────────────────────────────────────────────────────────

@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    platform._ws_clients.append(ws)
    try:
        while True:
            # Send a heartbeat every second with current sensor state
            synced = platform.pipeline.current_synced()
            ctrl   = platform.controller
            if synced:
                msg = json.dumps({
                    "type": "sensor",
                    "data": {
                        "temp_c":    synced.furnace_temp_c,
                        "diam_um":   synced.fiber_diameter_um,
                        "speed_ms":  synced.draw_speed_ms,
                        "visc_cp":   synced.melt_viscosity_cp,
                        "state":     ctrl.state.value,
                        "sp_temp":   ctrl.setpoint.furnace_temp_c,
                        "sp_speed":  ctrl.setpoint.draw_speed_ms,
                        "ts":        synced.bin_timestamp.isoformat(),
                    }
                })
                await ws.send_text(msg)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        if ws in platform._ws_clients:
            platform._ws_clients.remove(ws)


# ── Simulation loop ─────────────────────────────────────────────────────────

async def _simulation_loop(
    sim:    ProcessSimulator,
    recipe: Any,
) -> None:
    """
    Async loop that advances the simulator and feeds the pipeline.
    Runs until cancelled (by /runs/stop) or the run completes.
    """
    from qutlas.schema import ControlAction
    controller = SimpleController(recipe)
    last_reading = None
    step = 0

    try:
        while True:
            # Build action from adaptive controller setpoint if available
            latest_decision = platform.controller.latest_decision
            if latest_decision and step > 0:
                sp = latest_decision.setpoint
                action = ControlAction(
                    timestamp               = datetime.now(UTC),
                    run_id                  = platform.active_run_id or "",
                    furnace_temp_setpoint_c = sp.furnace_temp_c,
                    draw_speed_setpoint_ms  = sp.draw_speed_ms,
                    cooling_airflow_setpoint= sp.airflow_lpm,
                )
            else:
                action = controller.decide(last_reading) if last_reading else None

            last_reading = sim.step(action)
            last_reading.run_id = platform.active_run_id
            platform.pipeline.ingest(last_reading)
            step += 1

            # Yield to event loop every step
            await asyncio.sleep(0.02)   # 50 Hz simulation

    except asyncio.CancelledError:
        logger.info(f"Simulation loop cancelled after {step} steps")
        raise
